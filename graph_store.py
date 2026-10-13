"""
Self-hosted HydraDB (open-source graph-node) adapter for AFTERSIGHT.

Talks to the documented HTTP query API:
    POST {HYDRA_GRAPH_URL}/v1/graphs/{graph}/query
    Authorization: Bearer <token>      X-Graph-Namespace: <namespace>
    body: {"cell_id", "query" (OpenCypher), "parameters"}
    200 -> {"columns": [...], "rows": [[{"type","value"}, ...]], "bookmark", ...}
    err -> {"error": {"code", "message"}}

Graph model — every object seen in a glasses capture is one timestamped node:

    (:Observation {id, obs_id, memory_id, ts, epoch, object, quantity, has_quantity,
                   place, event, source, caption})
    (:Observation)-[:NEXT_SEEN]->(:Observation)   same object, next time it was seen

id is a stable non-negative integer derived from obs_id (HydraDB nodes are keyed by integer id).
quantity = 0 with has_quantity = false when the count is unknown.
Queries follow the subset in the repo's cypher-compat.md: vertex upsert is
UNWIND $rows MERGE (n {id: row.id}) SET ...; no IN / WITH-ordering; one statement per request.

Env:
    HYDRA_GRAPH_URL        e.g. http://127.0.0.1:8443   (unset = adapter disabled)
    HYDRA_GRAPH_TOKEN      bearer token (or HYDRA_GRAPH_TOKEN_FILE=path)
    HYDRA_GRAPH_NAMESPACE  default "default"
    HYDRA_GRAPH_ID         default "default"
    HYDRA_GRAPH_CELL_ID    default "cell-0"

Self-test against a running server:
    python graph_store.py --selftest
"""
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime


class GraphError(RuntimeError):
    pass


def _cfg():
    url = (os.getenv("HYDRA_GRAPH_URL") or "").rstrip("/")
    token = os.getenv("HYDRA_GRAPH_TOKEN")
    tf = os.getenv("HYDRA_GRAPH_TOKEN_FILE")
    if not token and tf and os.path.exists(tf):
        token = open(tf).read().strip()
    return {
        "url": url,
        "token": token or "",
        "namespace": os.getenv("HYDRA_GRAPH_NAMESPACE", "default"),
        "graph": os.getenv("HYDRA_GRAPH_ID", "default"),
        "cell": os.getenv("HYDRA_GRAPH_CELL_ID", "cell-0"),
    }


def enabled():
    return bool(_cfg()["url"])


def _decode(v):
    """Decode one adjacently-tagged value: {"type": ..., "value": ...}."""
    if not isinstance(v, dict) or "type" not in v:
        return v
    t = v["type"]
    if t == "null":
        return None
    if t == "list":
        return [_decode(x) for x in v.get("value", [])]
    return v.get("value")


def query(cypher, parameters=None, timeout=15):
    """Run one OpenCypher statement. Returns a list of dict rows keyed by column name."""
    c = _cfg()
    if not c["url"]:
        raise GraphError("HYDRA_GRAPH_URL not set")
    body = {"cell_id": c["cell"], "query": cypher, "parameters": parameters or {}}
    req = urllib.request.Request(
        f"{c['url']}/v1/graphs/{c['graph']}/query",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {c['token']}", "X-Graph-Namespace": c["namespace"],
                 "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get("error", {})
            raise GraphError(f"{e.code} {err.get('code', '')}: {err.get('message', '')}".strip()) from None
        except (ValueError, AttributeError):
            raise GraphError(f"HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise GraphError(f"cannot reach {c['url']}: {e.reason}") from None
    if "error" in data:
        err = data["error"]
        raise GraphError(f"{err.get('code', '')}: {err.get('message', '')}")
    cols = data.get("columns", [])
    return [dict(zip(cols, [_decode(v) for v in row])) for row in data.get("rows", [])]


def health():
    """(ok, message). Uses GET /healthz, then a trivial query to prove auth + Cypher work."""
    c = _cfg()
    if not c["url"]:
        return False, "HYDRA_GRAPH_URL not set"
    try:
        urllib.request.urlopen(f"{c['url']}/healthz", timeout=5).read()
    except Exception as e:
        return False, f"healthz failed: {e}"
    try:
        query("MATCH (o:Observation) RETURN count(*) AS n")
        return True, "connected"
    except GraphError as e:
        return False, str(e)


def _epoch(ts):
    try:
        return int(datetime.fromisoformat(ts).timestamp())
    except Exception:
        return 0


def node_id(key):
    """Stable non-negative integer node id (52 bits, safe in JSON)."""
    return int(hashlib.sha1(key.encode()).hexdigest()[:13], 16)


def observation_rows(event, source=None):
    """Flatten one AFTERSIGHT memory event into one graph row per observed object."""
    src = source or ("camera" if event.get("image") else
                     "agent" if event.get("place") == "AFTERSIGHT agent" else "simulated")
    rows = []
    for i, o in enumerate(event.get("objects", [])):
        q = o.get("count")
        rows.append({
            "id": node_id(f"{event['id']}:{i}"),
            "obs_id": f"{event['id']}:{i}",
            "memory_id": event["id"],
            "ts": event["ts"],
            "epoch": _epoch(event["ts"]),
            "object": str(o.get("name", "")).lower().strip(),
            "quantity": int(q) if isinstance(q, int) and q >= 0 else 0,
            "has_quantity": isinstance(q, int),
            "place": event.get("place", ""),
            "event": o.get("event", "observed"),
            "source": src,
            "caption": event.get("caption", ""),
        })
    return rows


# vertex upsert: the documented batch form (MERGE by id, then SET). Idempotent.
WRITE = """
UNWIND $rows AS row
MERGE (n {id: row.id})
SET n:Observation, n.obs_id = row.obs_id, n.memory_id = row.memory_id, n.ts = row.ts, n.epoch = row.epoch,
    n.object = row.object, n.quantity = row.quantity, n.has_quantity = row.has_quantity, n.place = row.place,
    n.event = row.event, n.source = row.source, n.caption = row.caption
"""

# temporal edge between consecutive sightings of the same object (documented UNWIND MATCH ... MERGE form)
LINK = """
UNWIND $rows AS row
MATCH (s:Observation {id: row.src}), (d:Observation {id: row.dst})
MERGE (s)-[r:NEXT_SEEN {id: row.rel}]->(d)
"""

LATEST_FOR = ("MATCH (o:Observation) WHERE o.object = $object AND o.epoch < $before "
              "RETURN o.id AS id, o.epoch AS epoch ORDER BY epoch DESC LIMIT 1")


def write_events(events):
    """Upsert memories as graph nodes (idempotent per observation). Returns number of rows written."""
    rows = [r for e in events for r in observation_rows(e)]
    if not rows:
        return 0
    query(WRITE, {"rows": rows})
    links = []
    for r in sorted(rows, key=lambda r: r["epoch"]):
        try:
            prev = query(LATEST_FOR, {"object": r["object"], "before": r["epoch"]})
            if prev and prev[0].get("id") is not None:
                links.append({"src": prev[0]["id"], "dst": r["id"],
                              "rel": node_id(f"next:{prev[0]['id']}:{r['id']}")})
        except GraphError:
            pass
    if links:
        try:
            query(LINK, {"rows": links})
        except GraphError:
            pass  # temporal edges are an enrichment; the nodes are already durable
    return len(rows)


READ_FIELDS = ("o.memory_id AS memory_id, o.ts AS ts, o.epoch AS epoch, o.object AS object, o.quantity AS quantity, "
               "o.has_quantity AS has_quantity, o.place AS place, o.event AS event, o.source AS source, "
               "o.caption AS caption")


def recall(objects=None, limit=25):
    """Retrieve observations, newest first — for the named objects if given, else the most recent."""
    if not objects:
        return query(f"MATCH (o:Observation) RETURN {READ_FIELDS} ORDER BY epoch DESC LIMIT {int(limit)}")
    rows = []
    for obj in objects:  # IN is not supported by HydraDB's WHERE, so query each object
        rows += query(f"MATCH (o:Observation) WHERE o.object = $object RETURN {READ_FIELDS} "
                      f"ORDER BY epoch DESC LIMIT {int(limit)}", {"object": obj})
    rows.sort(key=lambda r: r.get("epoch") or 0, reverse=True)
    return rows[:limit]


def known_objects():
    return [r["object"] for r in query("MATCH (o:Observation) RETURN DISTINCT o.object AS object")]


def objects_in_question(question, vocabulary):
    """Match question words to known object names ('egg' -> 'eggs', 'key' -> 'keys')."""
    words = set(re.findall(r"[a-z]+", question.lower()))
    stems = {w.rstrip("s") for w in words} | words
    hits = []
    for name in vocabulary:
        parts = set(re.findall(r"[a-z]+", name.lower()))
        if parts & words or {p.rstrip("s") for p in parts} & stems:
            hits.append(name)
    return hits


def as_text(row):
    qty = f" quantity {row['quantity']}" if row.get("has_quantity") else ""
    return (f"[{row['ts']}] {row['source']} observation at {row['place']}: {row['object']} "
            f"({row['event']}{qty}). {row.get('caption', '')} (memory id {row['memory_id']})")


def _selftest():
    ok, msg = health()
    print("health:", ok, msg)
    if not ok:
        sys.exit(1)
    ev = {"id": f"selftest-{int(datetime.now().timestamp())}", "ts": datetime.now().replace(microsecond=0).isoformat(),
          "place": "test bench", "caption": "Self-test observation",
          "objects": [{"name": "selftest widget", "event": "observed", "count": 3}]}
    print("rows written:", write_events([ev]))
    print("re-written (idempotent upsert):", write_events([ev]))
    rows = [r for r in recall(["selftest widget"], limit=10) if r["memory_id"] == ev["id"]]
    print("read back:", json.dumps(rows[:1], indent=1))
    ok = len(rows) == 1 and rows[0]["quantity"] == 3
    print("PASS" if ok else f"FAIL ({len(rows)} matching nodes)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass
        _selftest()
    else:
        print(__doc__)
