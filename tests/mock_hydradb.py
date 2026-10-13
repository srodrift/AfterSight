"""Mock enforcing HydraDB's documented OpenCypher subset (cypher-compat.md) for AFTERSIGHT's queries."""
import json, re, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
NODES = {}; EDGES = {}
TOKEN = "local-development-token-32-bytes"
def tag(v):
    if v is None: return {"type": "null"}
    if isinstance(v, bool): return {"type": "boolean", "value": v}
    if isinstance(v, int): return {"type": "signed_integer" if v < 0 else "integer", "value": v}
    return {"type": "string", "value": v}
def reject(msg): raise ValueError(msg)
def validate(q, p):
    if re.search(r"\bWHERE\b[^R]*\bIN\b", q): reject("WHERE supports boolean combinations of property comparisons")
    if re.search(r"\bWITH\b", q): reject("WITH that orders/limits not supported")
    if re.search(r"count\((?!\*)", q): reject("property values support integer, float, boolean, and string literals")
    if "CREATE (:" in q or re.search(r"CREATE \(\w*(:\w+)? \{(?!id)", q): reject("CREATE requires relationship paths with ids")
    for m in re.finditer(r"\{([^}]*)\}", q):  # property maps: literal or $param only, except row.<field> for id
        for kv in m.group(1).split(","):
            if ":" not in kv: continue
            k, v = [x.strip() for x in kv.split(":", 1)]
            if re.match(r"^\w+\.\w+$", v) and k != "id": reject("property values support integer, float, boolean, and string literals")
    if "UNWIND" in q:
        lst = p.get(re.search(r"UNWIND \$(\w+)", q).group(1))
        if not (isinstance(lst, list) and all(isinstance(x, dict) for x in lst)): reject("UNWIND needs a list of maps")
    for k, v in p.items():
        if isinstance(v, int) and k == "id" and v < 0: reject("negative node id")
FIELDS = ["memory_id","ts","epoch","object","quantity","has_quantity","place","event","source","caption"]
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _send(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self): self._send(200, {"status":"ok"}) if self.path == "/healthz" else self._send(404, {})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.headers.get("Authorization") != f"Bearer {TOKEN}": return self._send(401, {"error": {"code":"unauthenticated","message":"bad token"}})
        q, p = " ".join(body["query"].split()), body.get("parameters", {})
        try: validate(q, p)
        except ValueError as e: return self._send(400, {"error": {"code":"invalid_request","message":f"OpenCypher query is not supported yet: {e}"}})
        if "count(*)" in q: return self._send(200, {"columns":["n"],"rows":[[tag(len(NODES))]]})
        if q.startswith("UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Observation"):
            for r in p["rows"]: NODES[r["id"]] = dict(r)
            return self._send(200, {"columns":[],"rows":[]})
        if "MERGE (s)-[r:NEXT_SEEN" in q:
            for r in p["rows"]:
                assert r["src"] in NODES and r["dst"] in NODES
                EDGES[r["rel"]] = (r["src"], r["dst"])
            return self._send(200, {"columns":[],"rows":[]})
        if "RETURN o.id AS id, o.epoch AS epoch" in q:
            c = sorted([n for n in NODES.values() if n["object"] == p["object"] and n["epoch"] < p["before"]], key=lambda n: -n["epoch"])[:1]
            return self._send(200, {"columns":["id","epoch"],"rows":[[tag(n["id"]),tag(n["epoch"])] for n in c]})
        if "RETURN DISTINCT o.object" in q:
            return self._send(200, {"columns":["object"],"rows":[[tag(o)] for o in sorted({n["object"] for n in NODES.values()})]})
        if q.startswith("MATCH (o:Observation)"):
            sel = [n for n in NODES.values() if "WHERE o.object = $object" not in q or n["object"] == p["object"]]
            sel.sort(key=lambda n: -n["epoch"]); lim = int(re.search(r"LIMIT (\d+)", q).group(1))
            return self._send(200, {"columns":FIELDS,"rows":[[tag(n[f]) for f in FIELDS] for n in sel[:lim]]})
        return self._send(400, {"error":{"code":"invalid_request","message":"mock: unhandled "+q[:80]}})
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
