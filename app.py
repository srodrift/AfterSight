"""
AFTERSIGHT — Your eyes see it. Your AI remembers it.
Simulated smart-glasses memory: timestamped observations -> HydraDB memory ->
deterministic temporal state engine -> LLM agent -> approved actions (shopping list).

Run:  streamlit run app.py
Env:  HYDRA_DB_API_KEY, and ANTHROPIC_API_KEY or OPENAI_API_KEY
"""
import os, json, time, base64, uuid
from datetime import datetime, timedelta

import streamlit as st
from dotenv import load_dotenv
import streamlit.components.v1 as components
import scene
import graph_store
import memory_search
import voice

_cam = components.declare_component("aftersight_camera",
                                    path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera"))


def camera_component(**kw):
    return _cam(**kw)


_voice = components.declare_component("aftersight_voice",
                                      path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice"))

load_dotenv()

DB = os.getenv("HYDRA_DATABASE", "aftersight")
COLL = os.getenv("HYDRA_COLLECTION", "glasses_memory")
EVENTS_F, SHOP_F, ACT_F = "events.json", "shopping.json", "actions.json"

st.set_page_config(page_title="AFTERSIGHT", page_icon="👓", layout="wide")

# Optional password gate — set APP_PASSWORD in .env before sharing an https tunnel link
_pw = os.getenv("APP_PASSWORD")
if _pw and not st.session_state.get("authed"):
    st.markdown("## AFTERSIGHT")
    entered = st.text_input("Password", type="password")
    if entered == _pw:
        st.session_state.authed = True
        st.rerun()
    elif entered:
        st.error("Wrong password")
    st.stop()

# ---------------------------------------------------------------- seed data
NOW = datetime.now().replace(second=0, microsecond=0)


def at(h, m, days_ago=0):
    return (NOW - timedelta(days=days_ago)).replace(hour=h, minute=m).isoformat()


def ob(name, event="observed", count=1, note=None):
    d = {"name": name, "event": event, "count": count}
    if note:
        d["note"] = note
    return d


SEED = [
    dict(id="seed-01", ts=at(7, 42), place="kitchen fridge",
         caption="Opened fridge. Egg carton with 6 eggs, 1 carton of milk, 3 apples, spinach.",
         objects=[ob("eggs", count=6), ob("milk"), ob("apples", count=3), ob("spinach")]),
    dict(id="seed-02", ts=at(7, 55), place="kitchen stove",
         caption="Cracked 2 eggs into a pan.", objects=[ob("eggs", "used", 2)]),
    dict(id="seed-03", ts=at(8, 10), place="kitchen counter, next to coffee mug",
         caption="Keys on the kitchen counter next to the coffee mug.", objects=[ob("keys")]),
    dict(id="seed-04", ts=at(8, 30), place="dining table",
         caption="Medication bottle on the dining table, cap closed. No pill taken in view.",
         objects=[ob("medication bottle")]),
    dict(id="seed-05", ts=at(8, 52), place="office garage, level 3, spot 3C",
         caption="Parked the car on garage level 3, spot 3C.", objects=[ob("car")]),
    dict(id="seed-06", ts=at(12, 20), place="office desk",
         caption="Laptop open on the office desk, charger plugged in.",
         objects=[ob("laptop"), ob("laptop charger")]),
    dict(id="seed-07", ts=at(18, 5), place="kitchen fridge",
         caption="Opened fridge. Egg carton with 2 eggs. Milk carton nearly empty. 3 apples.",
         objects=[ob("eggs", count=2), ob("milk", note="nearly empty"), ob("apples", count=3)]),
    dict(id="seed-08", ts=at(18, 20), place="kitchen counter",
         caption="Ate 1 apple.", objects=[ob("apples", "used", 1)]),
    dict(id="seed-09", ts=at(18, 30), place="hallway table by the front door",
         caption="Dropped keys on the hallway table by the front door.", objects=[ob("keys")]),
    dict(id="seed-10", ts=at(9, 15), place="bedroom dresser",
         caption="Black sunglasses folded on the bedroom dresser, next to a small green succulent.",
         scene="Bedroom, morning light from the window.",
         objects=[dict(ob("sunglasses"), attributes="black, folded", position="on the dresser, left of the plant"),
                  dict(ob("succulent"), attributes="green, small white pot")]),
    dict(id="seed-11", ts=at(13, 40), place="office desk",
         caption="Yellow sticky note on the monitor that reads 'call dentist 3pm'.",
         scene="Office desk with a monitor and keyboard.",
         details=["sticky note text: call dentist 3pm"],
         objects=[dict(ob("sticky note"), attributes="yellow, handwritten", position="stuck to the monitor bezel")]),
    dict(id="seed-12", ts=at(17, 50), place="hallway by the front door",
         caption="Navy blue umbrella leaning against the wall by the front door, still wet.",
         objects=[dict(ob("umbrella"), attributes="navy blue, wet, closed", position="leaning against the wall")]),
]

# Scenes the "glasses" can capture live during the demo
SCENES = {
    "🍳 Use the last 2 eggs": dict(place="kitchen stove", caption="Cracked the last 2 eggs into a pan.",
                                  objects=[ob("eggs", "used", 2)]),
    "🧊 Open fridge (egg carton empty)": dict(place="kitchen fridge",
                                             caption="Opened fridge. Egg carton empty. Milk nearly empty. 2 apples.",
                                             objects=[ob("eggs", count=0), ob("milk", note="nearly empty"),
                                                      ob("apples", count=2)]),
    "🛒 Bought a dozen eggs at the store": dict(place="grocery store checkout",
                                                caption="Paid for a carton of 12 eggs at checkout.",
                                                objects=[ob("eggs", "purchased", 12)]),
    "🥚 Put new eggs in the fridge": dict(place="kitchen fridge", caption="Put a carton of 12 eggs in the fridge.",
                                         objects=[ob("eggs", "added", 12)]),
    "🔑 Keys into jacket pocket": dict(place="jacket pocket (coat rack)",
                                      caption="Put keys in the jacket pocket, jacket on the coat rack.",
                                      objects=[ob("keys")]),
}


# ---------------------------------------------------------------- persistence
def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


if "events" not in st.session_state:
    st.session_state.events = load(EVENTS_F, [dict(e) for e in SEED])
    st.session_state.shopping = load(SHOP_F, [])
    st.session_state.chat = []
    st.session_state.pending = []
    st.session_state.diag = {"last_query_hits": None, "last_error": None}
    st.session_state.actions = load(ACT_F, [])


def persist():
    save(EVENTS_F, st.session_state.events)
    save(SHOP_F, st.session_state.shopping)
    save(ACT_F, st.session_state.get("actions", []))


def fmt(ts):
    d = datetime.fromisoformat(ts)
    day = "today" if d.date() == NOW.date() else d.strftime("%a %b %d")
    return f"{d.strftime('%-I:%M %p')} {day}"


# ---------------------------------------------------------------- temporal state engine (deterministic)
def compute_state(events):
    """Replay events in time order. Observations set ground truth; used/added
    produce estimates; purchases are recorded but do not change home inventory."""
    state = {}
    for e in sorted(events, key=lambda e: e["ts"]):
        for o in e.get("objects", []):
            n = o["name"].lower().strip()
            s = state.setdefault(n, {"count": None, "basis": "unknown", "place": None,
                                     "last_observed": None, "notes": [], "evidence": []})
            ev, c = o.get("event", "observed"), o.get("count")
            if ev == "observed":
                if s["basis"] == "estimated" and s["count"] is not None and c is not None and s["count"] != c:
                    s["notes"].append(f"my estimate of {s['count']} was corrected by what I saw at {fmt(e['ts'])}")
                s.update(count=c, basis="observed", place=e["place"], last_observed=e["ts"])
                if o.get("note"):
                    s["notes"].append(f"{o['note']} ({fmt(e['ts'])})")
            elif ev in ("used", "added"):
                d = c if c is not None else 1
                if s["count"] is not None or ev == "added":
                    base = s["count"] or 0
                    s["count"] = max(0, base - d) if ev == "used" else base + d
                    s["basis"] = "estimated"
                if ev == "added":
                    s["place"] = e["place"]
            elif ev == "purchased":
                s["notes"].append(f"purchased {c} at {fmt(e['ts'])}, not yet seen at home")
            s["evidence"].append(e["id"])
            s["notes"] = s["notes"][-3:]
    return state


def state_table(state):
    rows = []
    for n, s in sorted(state.items()):
        rows.append({"object": n, "count": "?" if s["count"] is None else s["count"], "basis": s["basis"],
                     "last place": s["place"], "last seen": fmt(s["last_observed"]) if s["last_observed"] else "—",
                     "notes": "; ".join(s["notes"])})
    return rows


# ---------------------------------------------------------------- HydraDB
@st.cache_resource(show_spinner="Connecting to HydraDB…")
def hydra():
    key = os.getenv("HYDRA_DB_API_KEY")
    if not key:
        return None, "HYDRA_DB_API_KEY not set — running on local memory only"
    try:
        from hydra_db import HydraDB
        from hydra_db.errors import ConflictError
        c = HydraDB(token=key)
        try:
            c.databases.create(database=DB)
        except ConflictError:
            pass
        for _ in range(40):
            if c.databases.status(database=DB).data.infra.ready_for_ingestion:
                return c, "connected"
            time.sleep(3)
        return c, "database still provisioning"
    except Exception as ex:
        return None, f"error: {ex}"


def mem_text(e):
    def one(o):
        bits = [o["name"], o.get("event", "observed")]
        if o.get("count") is not None:
            bits.append(str(o["count"]))
        extra = "; ".join(str(o[k]) for k in ("attributes", "position") if o.get(k))
        return f"{bits[0]} ({' '.join(bits[1:])}{'; ' + extra if extra else ''})"
    objs = ", ".join(one(o) for o in e.get("objects", []))
    more = ""
    if e.get("scene"):
        more += f" Scene: {e['scene']}"
    if e.get("interactions"):
        more += f" Interactions: {'; '.join(map(str, e['interactions']))}."
    if e.get("details"):
        more += f" Details: {'; '.join(map(str, e['details']))}."
    photo = " [photo retained]" if e.get("image") else ""
    return (f"[{e['ts']}] Smart glasses at {e['place']}: {e['caption']} Objects: {objs}.{more}{photo} "
            f"(memory id {e['id']})")


@st.cache_resource(show_spinner="Connecting to self-hosted HydraDB…")
def graph():
    """Self-hosted HydraDB graph-node (OpenCypher over HTTP). Returns (ok, status)."""
    if not graph_store.enabled():
        return False, "not configured"
    return graph_store.health()


def any_store_connected():
    c, st_ = hydra()
    return (c is not None and st_ == "connected") or graph()[0]


def hydra_ingest(events):
    """Write memories to every configured HydraDB backend (managed SDK and/or self-hosted graph).
    An event is marked stored only after a backend confirmed the write."""
    if not events:
        return False
    c, _ = hydra()
    wrote = False
    if c:
        try:
            mems = [{"id": e["id"], "text": mem_text(e), "infer": False} for e in events]
            c.context.ingest(database=DB, collection=COLL, type="memory", memories=json.dumps(mems), upsert="true")
            for e in events:
                e["hydra"] = True
            wrote = True
        except Exception as ex:
            st.session_state.diag["last_error"] = f"ingest (managed): {ex}"
    if graph()[0]:
        try:
            graph_store.write_events(events)
            for e in events:
                e["hydra"] = True
                e["graph"] = True
            wrote = True
        except Exception as ex:
            st.session_state.diag["last_error"] = f"ingest (self-hosted graph): {ex}"
    if wrote:
        persist()
    return wrote


def hydra_query(q, k=8):
    """Retrieve evidence from every configured HydraDB backend. Each hit is tagged with its source."""
    hits = []
    c, _ = hydra()
    if c:
        try:
            try:
                r = c.query(database=DB, collection=COLL, query=q, type="memory",
                            query_by="hybrid", mode="fast", max_results=k, graph_context=False)
            except Exception:
                r = c.query(database=DB, collections={COLL: 1.0}, query=q, type="all",
                            query_by="hybrid", mode="fast", max_results=k, graph_context=False)
            hits += [{"text": getattr(ch, "chunk_content", "") or "", "score": getattr(ch, "relevancy_score", None),
                      "src": "managed"} for ch in (r.data.chunks or [])]
        except Exception as ex:
            st.session_state.diag["last_error"] = f"query (managed): {ex}"
    if graph()[0]:
        try:
            vocab = {o["name"].lower().strip() for e in st.session_state.events for o in e.get("objects", [])}
            try:
                vocab |= set(graph_store.known_objects())
            except Exception:
                pass
            objs = graph_store.objects_in_question(q, sorted(vocab))
            rows = graph_store.recall(objs or None, limit=20 if objs else 15)
            hits += [{"text": graph_store.as_text(r), "score": None, "src": "graph"} for r in rows]
        except Exception as ex:
            st.session_state.diag["last_error"] = f"query (self-hosted graph): {ex}"
    st.session_state.diag["last_query_hits"] = len(hits)
    return hits, "\n".join(h["text"] for h in hits)


# ---------------------------------------------------------------- LLM
def provider():
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return None


def llm(system, user, image=None, images=None):
    images = list(images or []) + ([image] if image else [])
    p = provider()
    if p == "anthropic":
        import anthropic
        content = [{"type": "image", "source": {"type": "base64", "media_type": m, "data": b}} for b, m in images]
        content.append({"type": "text", "text": user})
        r = anthropic.Anthropic().messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"), max_tokens=900,
            system=system, messages=[{"role": "user", "content": content}])
        return r.content[0].text
    if p == "openai":
        from openai import OpenAI
        content = [{"type": "text", "text": user}]
        content += [{"type": "image_url", "image_url": {"url": f"data:{m};base64,{b}"}} for b, m in images]
        r = OpenAI().chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": content}])
        return r.choices[0].message.content
    raise RuntimeError("No LLM key set (ANTHROPIC_API_KEY or OPENAI_API_KEY)")


def parse_json(txt):
    try:
        return json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    except Exception:
        return {"answer": txt, "propose_shopping": [], "evidence_ids": []}


AGENT_SYSTEM = """You are AFTERSIGHT, the visual episodic memory of the user's smart glasses.
Current time: {now}.
You get STATE (computed deterministically by replaying every observation in time order: counts and latest
locations) and EVIDENCE (memories retrieved for this question: captions, scenes, objects with colors,
positions and interactions). Some memories have a retained photo.
Rules:
- Answer only from STATE and EVIDENCE. Never invent a memory, object, color or detail.
- Trust STATE for counts and latest location; say WHEN and WHERE you saw it, naturally, in 1-3 sentences.
- Distinguish observed from inferred (e.g. counts estimated after usage). Never claim something is there
  *now* just because it was seen earlier, and never claim an action happened that was not observed.
- A purchase does not mean the item is at home until it is seen there.
- If the question asks about a visual detail (color, text, what was being held, layout, appearance) that the
  evidence text does not contain, but a relevant memory has a retained photo, put that memory id in
  "look_again" (max 2) instead of guessing. If no photo exists, say that detail was not recorded.
- If the user is low on or out of a consumable, or asks what to buy, propose shopping-list items with a short
  reason grounded in evidence. Never add them yourself; the user must approve.
- "status": "observed" if directly supported by a memory, "inferred" if derived from several memories,
  "unknown" if there is insufficient evidence.
Return ONLY JSON: {{"answer": str, "status": "observed"|"inferred"|"unknown",
"propose_shopping": [{{"item": str, "reason": str}}], "evidence_ids": [memory ids you relied on],
"look_again": [memory ids]}}"""

LOOK_SYSTEM = """You are AFTERSIGHT re-examining photos your smart glasses retained, to answer the user's question.
Answer ONLY from what is visible in the photos and the memory notes given. If the photos do not show the
answer, say so plainly. Mention when the photo was taken. 1-3 sentences.
Return ONLY JSON: {"answer": str, "status": "observed"|"unknown", "evidence_ids": [memory ids of photos used]}"""


def _img_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _proposals(raw):
    out = []
    for p in raw or []:
        if isinstance(p, str):
            out.append({"item": p, "reason": ""})
        elif isinstance(p, dict) and p.get("item"):
            out.append({"item": str(p["item"]), "reason": str(p.get("reason", ""))})
    return out


def ask(question, events, use_hydra=True, allow_look=True):
    """Grounded answer: local memory index + HydraDB retrieval -> reasoning -> optional photo re-examination."""
    state = compute_state(events)
    local = memory_search.search_events(question, events, k=8)
    hits, hctx = hydra_query(question) if use_hydra else ([], "")
    by_id = {e["id"]: e for e in events}
    parts = [f"local memory index: {len(local)}"]
    n_m = sum(h.get("src") == "managed" for h in hits)
    n_g = sum(h.get("src") == "graph" for h in hits)
    if n_g:
        parts.append(f"HydraDB graph (OpenCypher): {n_g}")
    if n_m:
        parts.append(f"HydraDB managed: {n_m}")
    source = " + ".join(parts)
    ctx = "\n".join(mem_text(e) for e in local)
    if hctx:
        ctx += "\n--- HydraDB results ---\n" + hctx
    user = (f"STATE:\n{json.dumps(state, indent=1)}\n\nEVIDENCE ({source}):\n{ctx}\n\n"
            f"SHOPPING LIST: {st.session_state.shopping}\n\nQUESTION: {question}")
    out = parse_json(llm(AGENT_SYSTEM.format(now=NOW.strftime('%-I:%M %p, %A %b %d')), user))
    out["reexamined"] = []
    look = [i for i in (out.get("look_again") or []) if i in by_id and by_id[i].get("image")
            and os.path.exists(by_id[i]["image"])][:2]
    if allow_look and look:
        notes = "\n".join(f"Photo {n + 1} = memory {i}: {mem_text(by_id[i])}" for n, i in enumerate(look))
        out2 = parse_json(llm(LOOK_SYSTEM, f"MEMORY NOTES:\n{notes}\n\nQUESTION: {question}",
                              images=[(_img_b64(by_id[i]["image"]), "image/jpeg") for i in look]))
        out.update(answer=out2.get("answer", out.get("answer", "")), status=out2.get("status", "observed"),
                   evidence_ids=out2.get("evidence_ids") or look)
        out["reexamined"] = look
        source += f" + re-examined {len(look)} retained photo(s)"
    out["evidence_ids"] = [i for i in (out.get("evidence_ids") or []) if i in by_id]
    out["source"], out["hits"] = source, hits
    have = {a["item"].lower() for a in st.session_state.get("actions", []) if a["status"] in ("pending", "approved")}
    have |= {x.lower() for x in st.session_state.shopping}
    out["propose_shopping"] = [p for p in _proposals(out.get("propose_shopping")) if p["item"].lower() not in have]
    return out


def add_event(place, caption, objects, image=None):
    """Save a memory locally, then to HydraDB. Returns (event, hydra_ok)."""
    e = dict(id=f"live-{uuid.uuid4().hex[:8]}", ts=datetime.now().replace(microsecond=0).isoformat(),
             place=place, caption=caption, objects=objects)
    if image:
        e["image"] = image
    st.session_state.events.append(e)
    persist()
    ok = hydra_ingest([e])
    return e, ok


# ---------------------------------------------------------------- UI
import io
import re as _re
import html as _html
from PIL import Image

html_escape = _html.escape

IMG_DIR = "images"
os.makedirs(IMG_DIR, exist_ok=True)
AUTO_INTERVAL_MS = int(os.getenv("AUTO_MEMORY_INTERVAL_MS", "10000"))
AUTO_MAX_SAMPLES = int(os.getenv("AUTO_MEMORY_MAX_SAMPLES", "90"))

# ---------------------------------------------------------------- design system
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Inter+Tight:wght@500;600;700&family=JetBrains+Mono:wght@500&display=swap');
:root { --bg:#080B0D; --surface:#111719; --surface2:#151d20; --line:rgba(243,246,245,.07); --text:#F3F6F5;
        --muted:#95A5A3; --mint:#72F5C2; --mint-dim:rgba(114,245,194,.12); --cyan:#7fd8ff; --amber:#f2c76b; --red:#ff7b7b; }
html, body, .stApp, [class*="css"], .stMarkdown, button, input, textarea { font-family: Inter, -apple-system, "SF Pro Text", sans-serif !important; }
.stApp { background: radial-gradient(1400px 700px at 70% -20%, #0f1d1c 0%, var(--bg) 60%) fixed; color: var(--text); }
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], footer, #MainMenu,
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stAppDeployButton"],
[data-testid="stMainMenu"], [data-testid="stStatusWidget"] { display: none !important; }
.block-container { padding: 1.4rem 2rem 4rem !important; max-width: min(92vw, 1500px) !important; width: 100% !important; }
h1, h2, h3, h4 { font-family: "Inter Tight", Inter, sans-serif !important; letter-spacing: -0.01em; color: var(--text); }
p, li, label, .stCaption { color: var(--text); }
.muted { color: var(--muted); font-size: 14px; }
.mono { font-family: "JetBrains Mono", Menlo, monospace !important; font-size: 12px; letter-spacing: .04em; }

/* top bar + navigation */
.topbar { display:flex; align-items:center; justify-content:space-between; margin: 0 0 10px; }
.wordmark { font: 700 22px/1 "Inter Tight", Inter, sans-serif; letter-spacing: .02em; color: var(--text); }
.wordmark i { color: var(--mint); font-style: normal; }
[data-testid="stButtonGroup"] { margin-bottom: 14px; }
[data-testid="stButtonGroup"] button { font-size: 15px !important; padding: 10px 8px !important; min-height: 46px;
     background: var(--surface) !important; border-color: var(--line) !important; color: var(--muted) !important; }
[data-testid="stButtonGroup"] button p { font-size: 15px !important; }
[data-testid="stButtonGroup"] button[aria-checked="true"] { color: #04110c !important; background: var(--mint) !important;
     border-color: var(--mint) !important; }
[data-testid="stButtonGroup"] button[aria-checked="true"] p { color: #04110c !important; font-weight: 600; }

/* buttons */
.stButton button, .stDownloadButton button { border-radius: 14px !important; min-height: 46px; font-size: 15px !important;
     background: var(--surface) !important; border: 1px solid var(--line) !important; color: var(--text) !important; }
.stButton button:hover { border-color: rgba(114,245,194,.45) !important; color: var(--mint) !important; }
.stButton button[kind="primary"] { background: var(--mint) !important; color: #04110c !important; border: 0 !important; font-weight: 600; }
.stButton button[kind="primary"] p { color: #04110c !important; }
.stTextInput input, .stChatInput textarea { background: var(--surface) !important; border-radius: 14px !important; color: var(--text) !important; font-size: 16px !important; }
[data-testid="stChatInput"] { border-radius: 18px !important; background: var(--surface) !important; border: 1px solid rgba(114,245,194,.25) !important; }
[data-testid="stExpander"] { border: 1px solid var(--line) !important; border-radius: 14px !important; background: transparent !important; }
[data-testid="stImage"] img { border-radius: 14px; }

/* home */
.eyebrow { font: 500 13px "JetBrains Mono", Menlo, monospace; color: var(--mint); letter-spacing: .12em; text-transform: uppercase; }
.hero-h { font: 700 clamp(36px, 3.9vw, 64px)/1.02 "Inter Tight", Inter, sans-serif; letter-spacing: -0.035em; color: var(--text); margin: 14px 0 0; }
.hero-h span { color: var(--mint); }
.hero-sub { color: var(--muted); font-size: clamp(16px, 1.6vw, 21px); margin: 18px 0 26px; max-width: 34ch; line-height: 1.45; }
.panel { background: linear-gradient(180deg, rgba(255,255,255,.035), rgba(255,255,255,.015)); border: 1px solid var(--line);
         border-radius: 24px; padding: 22px 24px; }
.step-card { height: 100%; }
.step-card .n { font: 500 12px "JetBrains Mono", monospace; color: var(--muted); }
.step-card .k { font: 700 28px "Inter Tight", Inter, sans-serif; color: var(--text); margin: 6px 0 6px; letter-spacing: -0.01em; }
.step-card .k b { color: var(--mint); }
.step-card .d { color: var(--muted); font-size: 15px; line-height: 1.5; }
.step-card .ex { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--line); color: var(--text); font-size: 14px; }
.stat { padding: 18px 6px; text-align: left; border-top: 1px solid var(--line); }
.stat .v { font: 600 clamp(30px, 3.4vw, 46px)/1 "Inter Tight", Inter, sans-serif; color: var(--text); }
.stat .l { color: var(--muted); font-size: 13px; margin-top: 8px; }

/* memory + answers */
.mcard { background: var(--surface); border: 1px solid var(--line); border-radius: 18px; padding: 14px 16px 12px; margin: 6px 0 14px; }
.mcard .cap { color: var(--text); font-size: 15px; line-height: 1.45; margin: 4px 0 8px; }
.mcard .meta { color: var(--muted); font-size: 12.5px; }
.chip { display:inline-block; background: var(--mint-dim); color: var(--mint); border-radius: 999px; padding: 3px 10px; margin: 2px 4px 2px 0; font-size: 12.5px; }
.chip.dim { background: rgba(243,246,245,.06); color: var(--muted); }
.badge { display:inline-block; border-radius: 999px; padding: 3px 11px; font: 600 11px "JetBrains Mono", monospace; letter-spacing: .08em; }
.b-observed { background: rgba(114,245,194,.14); color: var(--mint); }
.b-inferred { background: rgba(242,199,107,.14); color: var(--amber); }
.b-unknown { background: rgba(243,246,245,.08); color: var(--muted); }
.answer { font: 500 clamp(18px, 1.6vw, 22px)/1.45 Inter, sans-serif; color: var(--text); margin: 10px 0 8px; animation: rise .35s ease-out; }
.qbubble { display:flex; justify-content:flex-end; margin: 18px 0 6px; }
.qbubble span { background: var(--surface2); border: 1px solid var(--line); border-radius: 18px 18px 4px 18px; padding: 10px 16px; color: var(--text); font-size: 16px; max-width: 80%; }
.acard { background: var(--surface); border: 1px solid var(--line); border-radius: 20px; padding: 18px 20px; margin-bottom: 8px; }
.srcline { color: var(--muted); font-size: 12.5px; margin-left: 8px; }
.notice { border-left: 3px solid var(--mint); padding: 10px 14px; background: var(--mint-dim); border-radius: 0 14px 14px 0; margin: 6px 0; }
.notice .t { color: var(--muted); font-size: 12.5px; }
.chain { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin:8px 0 4px; }
.stepx { background: var(--surface); border:1px solid rgba(114,245,194,.28); border-radius:12px; padding:8px 12px; color: var(--text); font-size:14px; }
.stepx .t { color: var(--muted); font-size:11px; display:block; }
.stepx.inf { border-style: dashed; border-color: rgba(242,199,107,.45); }
.stepx.inf .k { color: var(--amber); font-size: 10px; letter-spacing: .08em; }
.arrow { color: #3d5a63; }
.latest { color: var(--mint); font-size: 14px; margin: 2px 0 14px; }
.stage { display:flex; flex-wrap: wrap; gap:8px; margin: 10px 0; }
.stage span { border:1px solid var(--line); border-radius:999px; padding:6px 14px; font: 500 12px "JetBrains Mono", monospace; letter-spacing:.08em; color:#56656c; }
.stage span.on { border-color: var(--mint); color: var(--mint); }
.stage span.warn { border-color: var(--amber); color: var(--amber); }
.stage span.skip { border-color: var(--line); color: var(--muted); }
.stage span.err { border-color: var(--red); color: var(--red); }
.kv { display:flex; justify-content:space-between; padding: 9px 0; border-bottom: 1px solid var(--line); font-size: 14px; }
.kv span:first-child { color: var(--muted); }
.ok { color: var(--mint); } .bad { color: var(--red); } .warnc { color: var(--amber); }
.live-dot { display:inline-block; width:9px; height:9px; border-radius:50%; background: var(--red); margin-right:8px; animation: blink 1.4s infinite; }
@keyframes blink { 50% { opacity: .2 } }
@keyframes rise { from { opacity: 0; transform: translateY(6px) } to { opacity: 1; transform: none } }
@media (max-width: 760px) {
  .block-container { padding: .8rem .9rem 4rem !important; max-width: 100% !important; }
  [data-testid="stButtonGroup"] button, [data-testid="stButtonGroup"] button p { font-size: 13px !important; min-height: 40px; padding: 6px 2px !important; }
  .hero-sub { max-width: none; }
}
</style>""", unsafe_allow_html=True)

client, hstatus = hydra()
hydra_ok = client is not None and hstatus == "connected"
_g_ok, _g_status = graph()
if graph_store.enabled():
    hstatus = (f"managed: {hstatus} · " if os.getenv("HYDRA_DB_API_KEY") else "") + f"self-hosted graph: {_g_status}"
    hydra_ok = hydra_ok or _g_ok

for k, v in (("auto_on", False), ("auto_stats", {"analyzed": 0, "stored": 0, "skipped": 0, "last_saved": None,
                                                    "last_reason": ""})):
    if k not in st.session_state:
        st.session_state[k] = v


# ---------------------------------------------------------------- render helpers
def iframe(page_html, height):
    doc = ("<html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
           "<style>html,body{margin:0;background:transparent;font-family:Inter,-apple-system,sans-serif}</style>"
           f"</head><body>{page_html}</body></html>")
    if hasattr(st, "iframe"):
        st.iframe(doc, height=height)
    else:
        components.html(doc, height=height)


def show_frame(event, live, height=560):
    if event.get("image") and os.path.exists(event["image"]):
        frame = scene.render_photo(event, _img_b64(event["image"]), fmt, event.get("hydra", False), live=live)
    else:
        frame = scene.render(event, fmt, event.get("hydra", False), live=live)
    iframe(f"<div style='max-width:1000px;margin:0 auto'>{frame}</div>", height)


def thumb(e):
    """Photo path for real captures; the drawn scene (SVG markup) for simulated memories."""
    if e.get("image") and os.path.exists(e["image"]):
        return e["image"]
    m = _re.search(r"<svg.*</svg>", scene.render(e, fmt, e.get("hydra", False), live=False), _re.S)
    return m.group(0) if m else None


def source_of(e):
    if e.get("place") == "AFTERSIGHT agent":
        return "agent"
    if e.get("auto"):
        return "auto"
    return "photo" if e.get("image") else "simulated"


def obj_label(o):
    s = o["name"] + ("" if o.get("count") is None else f" ×{o['count']}")
    return s + (f" · {o['attributes']}" if o.get("attributes") else "")


def memory_card(e, show_image=True, compact=False):
    if show_image:
        t = thumb(e)
        if t:
            st.image(t, width="stretch")
    chips = "".join(f"<span class='chip'>{html_escape(obj_label(o))}</span>" for o in e.get("objects", [])[:6])
    inter = "".join(f"<span class='chip dim'>{html_escape(str(x))}</span>" for x in (e.get("interactions") or [])[:2])
    store = "<span class='ok'>HydraDB ✓</span>" if e.get("hydra") else "<span class='warnc'>local only</span>"
    st.markdown(f"<div class='mcard'><div class='meta'>{fmt(e['ts'])} · {html_escape(e['place'])}</div>"
                f"<div class='cap'>{html_escape(e['caption'])}</div>{chips}{inter}"
                f"<div class='meta' style='margin-top:8px'>{source_of(e)} · {store}"
                + ("" if compact else f" · <span class='mono'>{e['id']}</span>") + "</div></div>",
                unsafe_allow_html=True)


def stages_html(states):
    labels = {"PERCEPTION": "PERCEPTION", "UNDERSTANDING": "UNDERSTANDING", "MEMORY": "MEMORY"}
    out = []
    for n in ("PERCEPTION", "UNDERSTANDING", "MEMORY"):
        cls = states.get(n, "")
        lab = labels[n]
        if n == "MEMORY" and cls == "warn":
            lab = "MEMORY · LOCAL ONLY"
        if n == "MEMORY" and cls == "skip":
            lab = "MEMORY · NO CHANGE"
        out.append(f"<span class='{cls}'>{lab}</span>")
    return "<div class='stage'>" + "<span style='border:0;padding:6px 0;color:#3d5a63'>→</span>".join(out) + "</div>"


def memory_field_html(events):
    """Home centerpiece: real observations plotted over time, one thread per tracked object."""
    evs = sorted([e for e in events if e.get("objects")], key=lambda e: e["ts"])
    if not evs:
        return "<div style='color:#95A5A3;padding:40px'>No memories yet — start seeing.</div>", 120
    counts = {}
    for e in evs:
        for o in e["objects"]:
            counts[o["name"].lower()] = counts.get(o["name"].lower(), 0) + 1
    rows = [n for n, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:7]
    state = compute_state(evs)
    hrs = [datetime.fromisoformat(e["ts"]) for e in evs]
    t0, t1 = min(hrs), max(hrs)
    span = max((t1 - t0).total_seconds(), 3600)
    W, L, R, top, rh = 760, 112, 196, 22, 46
    H = top + rh * len(rows) + 40
    def x(t):
        return L + (W - L - R) * ((t - t0).total_seconds() / span)
    parts, delay = [], 0.0
    order = {e["id"]: i for i, e in enumerate(evs)}
    for ri, name in enumerate(rows):
        y = top + ri * rh + rh / 2
        pts = [(x(datetime.fromisoformat(e["ts"])), e, o) for e in evs for o in e["objects"] if o["name"].lower() == name]
        parts.append(f"<text x='{L - 16}' y='{y + 5}' text-anchor='end' fill='#F3F6F5' font-size='17' font-family='Inter, -apple-system, Helvetica, Arial, sans-serif'>{html_escape(name)}</text>")
        parts.append(f"<line x1='{L}' y1='{y}' x2='{W - R}' y2='{y}' stroke='rgba(243,246,245,.06)' stroke-width='1'/>")
        if len(pts) > 1:
            d = "M" + " L".join(f"{px:.1f},{y}" for px, _, _ in pts)
            ln = pts[-1][0] - pts[0][0]
            parts.append(f"<path d='{d}' stroke='rgba(114,245,194,.45)' stroke-width='2' fill='none' "
                         f"style='stroke-dasharray:{ln:.0f};stroke-dashoffset:{ln:.0f};animation:draw 1.4s {0.3 + ri * .12:.2f}s ease-out forwards'/>")
        for px, e, o in pts:
            dl = 0.15 + order[e["id"]] * 0.07
            ev = o.get("event", "observed")
            if ev == "observed":
                node = f"<circle cx='{px:.1f}' cy='{y}' r='7' fill='#72F5C2'/>"
            else:
                node = f"<circle cx='{px:.1f}' cy='{y}' r='6' fill='#080B0D' stroke='#f2c76b' stroke-width='2' stroke-dasharray='3 2'/>"
            if e.get("image"):
                node += f"<circle cx='{px:.1f}' cy='{y}' r='11' fill='none' stroke='#F3F6F5' stroke-width='1.2'/>"
            tip = html_escape(f"{fmt(e['ts'])} · {e['place']} — {e['caption']}")
            parts.append(f"<g class='n' style='animation-delay:{dl:.2f}s'><title>{tip}</title>{node}</g>")
        s = state.get(name, {})
        if s.get("count") is not None and name not in ("keys", "laptop", "car"):
            lab = f"{s['count']} · {'observed' if s['basis'] == 'observed' else 'inferred'}"
        else:
            lab = (s.get("place") or "")
            lab = lab if len(lab) <= 20 else lab[:19] + "…"
        col = "#72F5C2" if s.get("basis") == "observed" else "#f2c76b"
        parts.append(f"<text x='{W - R + 18}' y='{y + 5}' fill='{col}' font-size='14' font-family='JetBrains Mono, Menlo, monospace'>{html_escape(lab)}</text>")
    for k in range(5):
        t = t0 + timedelta(seconds=span * k / 4)
        parts.append(f"<text x='{x(t):.1f}' y='{H - 10}' text-anchor='middle' fill='#95A5A3' font-size='13' font-family='JetBrains Mono, Menlo, monospace'>{t.strftime('%-I:%M %p')}</text>")
    svg = (f"<svg viewBox='0 0 {W} {H}' width='100%' xmlns='http://www.w3.org/2000/svg'>"
           "<style>.n{opacity:0;animation:pop .45s ease-out forwards}@keyframes pop{from{opacity:0;transform:scale(.6)}"
           "to{opacity:1;transform:none}}.n{transform-box:fill-box;transform-origin:center}"
           "@keyframes draw{to{stroke-dashoffset:0}}</style>" + "".join(parts) + "</svg>")
    legend = ("<div style='display:flex;gap:18px;flex-wrap:wrap;color:#95A5A3;font:12px JetBrains Mono,monospace;margin:4px 6px 0'>"
              "<span><span style='color:#72F5C2'>●</span> observed</span>"
              "<span><span style='color:#f2c76b'>◌</span> change (inferred)</span>"
              "<span>◯ photo retained</span><span style='margin-left:auto'>latest known state →</span></div>")
    return f"<div>{svg}{legend}</div>", int(H * 0.86) + 50


def timeline_strip_html(evs, sel_id):
    if not evs:
        return "", 60
    ts = [datetime.fromisoformat(e["ts"]) for e in evs]
    t0, t1 = min(ts), max(ts)
    span = max((t1 - t0).total_seconds(), 3600)
    W, pad = 1000, 30
    dots = []
    for e, t in zip(evs, ts):
        px = pad + (W - 2 * pad) * (t - t0).total_seconds() / span
        on = e["id"] == sel_id
        fill = "#72F5C2" if on else ("#F3F6F5" if e.get("image") else "#3e5552")
        r = 9 if on else 5
        dots.append(f"<circle cx='{px:.1f}' cy='26' r='{r}' fill='{fill}'><title>{html_escape(fmt(e['ts']) + ' · ' + e['place'])}</title></circle>")
        if on:
            dots.append(f"<line x1='{px:.1f}' y1='6' x2='{px:.1f}' y2='46' stroke='#72F5C2' stroke-width='1.5'/>")
    lab = (f"<text x='{pad}' y='66' fill='#95A5A3' font-size='12' font-family='JetBrains Mono, Menlo, monospace'>{t0.strftime('%-I:%M %p')}</text>"
           f"<text x='{W - pad}' y='66' text-anchor='end' fill='#95A5A3' font-size='12' font-family='JetBrains Mono, Menlo, monospace'>{t1.strftime('%-I:%M %p')}</text>")
    svg = (f"<svg viewBox='0 0 {W} 74' width='100%'><line x1='{pad}' y1='26' x2='{W - pad}' y2='26' stroke='rgba(243,246,245,.12)'/>"
           + "".join(dots) + lab + "</svg>")
    return svg, 90


def memory_formed_html(e):
    """Short animation: the captured frame settles into a memory card with what was remembered."""
    if not (e.get("image") and os.path.exists(e["image"])):
        return None
    chips = "".join(f"<span style='display:inline-block;background:rgba(114,245,194,.12);color:#72F5C2;border-radius:999px;"
                    f"padding:3px 10px;margin:2px;font-size:12.5px'>{html_escape(obj_label(o))}</span>" for o in e["objects"][:6])
    store = ("<span style='color:#72F5C2'>Stored in HydraDB ✓</span>" if e.get("hydra")
             else "<span style='color:#f2c76b'>Saved locally — HydraDB did not confirm</span>")
    return (f"<style>@keyframes shrink{{from{{transform:scale(1.25);filter:blur(6px);opacity:.2}}to{{transform:none;filter:none;opacity:1}}}}"
            f"@keyframes fade{{from{{opacity:0;transform:translateX(10px)}}to{{opacity:1;transform:none}}}}</style>"
            f"<div style='display:flex;gap:16px;align-items:center;background:#111719;border:1px solid rgba(114,245,194,.25);"
            f"border-radius:20px;padding:12px;color:#F3F6F5'>"
            f"<img src='data:image/jpeg;base64,{_img_b64(e['image'])}' style='width:150px;height:110px;object-fit:cover;"
            f"border-radius:14px;animation:shrink .7s ease-out'>"
            f"<div style='animation:fade .6s .35s ease-out both'>"
            f"<div style='font:500 11px JetBrains Mono,monospace;color:#95A5A3;letter-spacing:.1em'>MEMORY FORMED · {html_escape(fmt(e['ts']).upper())} · {html_escape(e['place'].upper())}</div>"
            f"<div style='font-size:15px;margin:6px 0'>{html_escape(e['caption'])}</div>{chips}"
            f"<div style='font-size:12.5px;margin-top:6px'>{store}</div></div></div>")


# ---------------------------------------------------------------- actions (human-approved)
def actions():
    return st.session_state.setdefault("actions", [])


def propose(item, reason):
    if any(a["item"].lower() == item.lower() and a["status"] == "pending" for a in actions()):
        return
    actions().append({"id": uuid.uuid4().hex[:8], "item": item, "reason": reason, "status": "pending",
                      "ts": datetime.now().replace(microsecond=0).isoformat(), "decided": None})
    persist()


def decide(action_id, approve):
    for a in actions():
        if a["id"] == action_id and a["status"] == "pending":
            a["status"] = "approved" if approve else "dismissed"
            a["decided"] = datetime.now().replace(microsecond=0).isoformat()
            if approve:
                if a["item"] not in st.session_state.shopping:
                    st.session_state.shopping.append(a["item"])
                add_event("AFTERSIGHT agent", f"User approved adding {a['item']} to the shopping list.", [])
                st.toast(f"Added {a['item']} to your shopping list")
            persist()
            return a


def action_card(a, key_prefix):
    reason = html_escape(a.get("reason") or "")
    st.markdown(f"<div class='notice'>{'<div class=t>AFTERSIGHT noticed</div>' + reason if reason else ''}"
                f"<div style='margin-top:4px'>Suggested action: <b>Add {html_escape(a['item'])} to your shopping list</b></div></div>",
                unsafe_allow_html=True)
    c1, c2, _ = st.columns([1, 1, 3])
    c1.button("Approve", key=f"{key_prefix}-ap-{a['id']}", type="primary", width="stretch",
              on_click=decide, args=(a["id"], True))
    c2.button("Dismiss", key=f"{key_prefix}-dm-{a['id']}", width="stretch", on_click=decide, args=(a["id"], False))


# ---------------------------------------------------------------- vision pipeline
VISION_PROMPT = """Describe this first-person smart-glasses photo as a structured episodic memory. Return ONLY JSON:
{"caption": "one vivid factual sentence: what is visible, colors, what the person is holding or doing",
 "scene": "short description of the setting and lighting",
 "place_guess": "kind of place, e.g. kitchen, office desk, bedroom, street",
 "objects": [{"name": "lowercase noun (plural for countable food, e.g. eggs)", "count": int or null,
              "attributes": "colors, material, size, state, any readable text",
              "position": "where it is, relative to other things or the person (e.g. held in right hand, on the desk left of the laptop)"}],
 "interactions": ["what the person is doing with things, e.g. holding a yellow mug in the right hand"],
 "details": ["other notable facts: readable text, brands, screen content, number of people present"]}
Use null for count when you cannot count reliably. An empty egg carton is eggs with count 0.
Only describe what is visible. Do not identify people by name or guess sensitive traits."""


def analyze_frame(jpeg_bytes):
    im = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    im.thumbnail((1280, 1280))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    raw = llm("You are the vision system of smart glasses. Return ONLY JSON.", VISION_PROMPT,
              image=(base64.b64encode(buf.getvalue()).decode(), "image/jpeg"))
    v = parse_json(raw)
    objs = []
    for o in v.get("objects", []) or []:
        if not (isinstance(o, dict) and o.get("name")):
            continue
        o2 = {"name": str(o["name"]).lower().strip(), "event": "observed",
              "count": o["count"] if isinstance(o.get("count"), int) and o["count"] >= 0 else None}
        for k in ("attributes", "position"):
            if o.get(k):
                o2[k] = str(o[k])[:160]
        objs.append(o2)
    return v, objs, buf.getvalue()


def process_frame(jpeg_bytes, place, auto=False):
    """PERCEPTION (frame) -> UNDERSTANDING (vision model) -> MEMORY (local + HydraDB).
    Returns (event or None, hydra_ok, stages, message). In auto mode, unchanged scenes are not stored."""
    stages = {"PERCEPTION": "on"}
    v, objs, jpeg = analyze_frame(jpeg_bytes)
    if not objs:
        stages["UNDERSTANDING"] = "err"
        return None, False, stages, "Nothing recognizable in that frame — try again closer."
    stages["UNDERSTANDING"] = "on"
    where = place.strip() or str(v.get("place_guess") or "unknown place")
    if auto:
        reasons = memory_search.meaningful_change(objs, where, compute_state(st.session_state.events))
        stats = st.session_state.auto_stats
        stats["analyzed"] += 1
        if not reasons:
            stats["skipped"] += 1
            stages["MEMORY"] = "skip"
            return None, False, stages, "No meaningful change — nothing stored (frame discarded)"
        stats["last_reason"] = "; ".join(reasons[:3])
    path = os.path.join(IMG_DIR, f"{uuid.uuid4().hex[:10]}.jpg")
    with open(path, "wb") as f:
        f.write(jpeg)
    e, ok = add_event(where, str(v.get("caption") or "Camera observation"), objs, image=path)
    for k in ("scene", "interactions", "details"):
        if v.get(k):
            e[k] = v[k] if k == "scene" else [str(x) for x in v[k]][:6]
    if auto:
        e["auto"] = True
        st.session_state.auto_stats["stored"] += 1
        st.session_state.auto_stats["last_saved"] = e["ts"]
    persist()
    stages["MEMORY"] = "on" if ok else "warn"
    names = ", ".join(obj_label(o).split(" · ")[0] for o in objs[:5])
    if ok:
        saved = "stored in HydraDB"
    elif not any_store_connected():
        saved = "saved locally (HydraDB not connected)"
    else:
        saved = "saved locally (HydraDB write failed — see Settings)"
    prefix = "Auto Memory · " if auto else ""
    return e, ok, stages, f"✓ {prefix}Remembered {names} · {saved}"


# ---------------------------------------------------------------- navigation
NAV_ITEMS = ["Home", "Vision", "Memories", "Ask", "Activity", "Settings"]
if st.session_state.get("nav") not in NAV_ITEMS:
    st.session_state.nav = "Home"


def go(view):
    st.session_state.nav = view


st.markdown("<div class='topbar'><div class='wordmark'>AFTER<i>SIGHT</i></div>"
            + (f"<div class='mono' style='color:#95A5A3'><span class='live-dot'></span>AUTO MEMORY ON</div>"
               if st.session_state.auto_on else "") + "</div>", unsafe_allow_html=True)
st.segmented_control("Navigate", NAV_ITEMS, key="nav", label_visibility="collapsed", width="stretch",
                     format_func=lambda v: "⚙" if v == "Settings" else v)
NAV = st.session_state.nav or "Home"
EVENTS = st.session_state.events

# ================================================================= HOME
if NAV == "Home":
    left, right = st.columns([1.05, 1.25], gap="large")
    with left:
        st.markdown("<div class='eyebrow'>Visual memory for the physical world</div>"
                    "<div class='hero-h'>Your eyes see it.<br><span>Your AI remembers it.</span></div>"
                    "<div class='hero-sub'>An intelligent memory for the physical world. Capture a moment, and ask about it "
                    "later — with the evidence to prove it.</div>", unsafe_allow_html=True)
        b1, b2 = st.columns(2)
        b1.button("Start Seeing", type="primary", width="stretch", on_click=go, args=("Vision",))
        b2.button("Explore Your Memories", width="stretch", on_click=go, args=("Memories",))
    with right:
        st.markdown("<div class='eyebrow' style='color:#95A5A3'>Your memories, forming over time</div>",
                    unsafe_allow_html=True)
        field, fh = memory_field_html(EVENTS)
        iframe(field, fh)

    st.write("")
    caps = [e for e in EVENTS if e.get("image")]
    latest_cap = sorted(caps, key=lambda e: e["ts"])[-1] if caps else None
    changed = None
    for name, s in compute_state(EVENTS).items():
        if s["basis"] == "estimated" or any("corrected" in n for n in s["notes"]):
            changed = (name, s)
            break
    last_act = next((a for a in reversed(actions()) if a["status"] == "approved"), None)
    ex_see = (f"Latest capture: {html_escape(latest_cap['caption'])}" if latest_cap else "No camera captures yet.")
    ex_rem = (f"Knows {html_escape(changed[0])} changed — latest: {changed[1]['count']} ({changed[1]['basis']})"
              if changed else f"{len(EVENTS)} memories on the timeline.")
    ex_act = (f"Approved: add {html_escape(last_act['item'])} to the shopping list" if last_act
              else "Nothing approved yet — suggestions wait for you.")
    c1, c2, c3 = st.columns(3, gap="medium")
    for col, (n, k, d, ex) in zip((c1, c2, c3), [
        ("01", "<b>See</b>", "Your camera captures a moment. A vision model reads objects, colors, positions and what you're doing.", ex_see),
        ("02", "<b>Remember</b>", "Each moment becomes structured, timestamped memory in HydraDB — and a temporal engine tracks what changed.", ex_rem),
        ("03", "<b>Act</b>", "Ask anything. Answers cite the memories behind them, and suggested actions wait for your approval.", ex_act),
    ]):
        col.markdown(f"<div class='panel step-card'><div class='n'>{n}</div><div class='k'>{k}</div>"
                     f"<div class='d'>{d}</div><div class='ex'>{ex}</div></div>", unsafe_allow_html=True)
    st.write("")
    stats = [
        (len([e for e in EVENTS if e.get("place") != "AFTERSIGHT agent"]), "memories"),
        (len(caps), "camera captures"),
        (len({o["name"].lower() for e in EVENTS for o in e.get("objects", [])}), "things remembered"),
        (sum(1 for e in EVENTS if e.get("hydra")), "stored in HydraDB"),
        (sum(1 for a in actions() if a["status"] == "approved"), "actions approved"),
    ]
    for col, (v, l) in zip(st.columns(len(stats)), stats):
        col.markdown(f"<div class='stat'><div class='v'>{v}</div><div class='l'>{l}</div></div>", unsafe_allow_html=True)

# ================================================================= VISION
if NAV == "Vision":
    mode = st.segmented_control("Mode", ["Live", "Rewind", "Demo scenes", "Upload"], key="vision_mode",
                                default="Live", label_visibility="collapsed")
    mode = mode or "Live"

    if mode == "Live":
        cam_col, side = st.columns([2.1, 1], gap="large")
        res = st.session_state.get("cam_result", {})
        with side:
            st.markdown("<div class='eyebrow'>Context</div>", unsafe_allow_html=True)
            place = st.text_input("Where are you?", key="cam_place", placeholder="optional — AFTERSIGHT will guess",
                                  label_visibility="collapsed")
            st.markdown("<div class='eyebrow' style='margin-top:18px'>Privacy</div>", unsafe_allow_html=True)
            st.markdown("<div class='muted'>Manual by default: nothing is analyzed until you tap <b>Remember this</b>. "
                        "No video is recorded; only frames you capture (or Auto Memory keeps) are stored.</div>",
                        unsafe_allow_html=True)
            st.markdown("<div class='eyebrow' style='margin-top:18px'>Auto Memory</div>", unsafe_allow_html=True)
            if not st.session_state.auto_on:
                st.markdown(f"<div class='muted'>Opt-in. While on, one frame every {AUTO_INTERVAL_MS // 1000}s is "
                            "analyzed; a memory is stored only when something meaningful changed. Unchanged frames are "
                            "discarded.</div>", unsafe_allow_html=True)
                consent = st.checkbox("I understand and want Auto Memory on for this session", key="auto_consent")
                if st.button("Start Auto Memory", width="stretch", disabled=not (consent and provider())):
                    st.session_state.auto_on = True
                    st.session_state.auto_stats.update(analyzed=0, stored=0, skipped=0)
                    st.rerun()
            else:
                s = st.session_state.auto_stats
                st.markdown(f"<div class='notice'><span class='live-dot'></span><b>Auto Memory is on</b>"
                            f"<div class='t'>Start Vision in the camera to begin sampling.</div></div>",
                            unsafe_allow_html=True)
                for k_, v_ in (("Frames analyzed", s["analyzed"]), ("Memories stored", s["stored"]),
                               ("Unchanged (discarded)", s["skipped"]),
                               ("Last saved", fmt(s["last_saved"]) if s["last_saved"] else "—")):
                    st.markdown(f"<div class='kv'><span>{k_}</span><span>{v_}</span></div>", unsafe_allow_html=True)
                if s.get("last_reason"):
                    st.caption(f"Last change: {s['last_reason']}")
                if st.button("■  Stop Auto Memory", type="primary", width="stretch"):
                    st.session_state.auto_on = False
                    st.rerun()
        with cam_col:
            if not provider():
                st.warning("Add an OpenAI (or Anthropic) key to .env so captured frames can be understood.")
            frame = camera_component(status=res.get("msg", ""), status_nonce=res.get("nonce"),
                                     auto=bool(st.session_state.auto_on), interval_ms=AUTO_INTERVAL_MS,
                                     key="live_cam", default=None)
            if frame and frame.get("id") and frame["id"] != st.session_state.get("cam_done"):
                st.session_state.cam_done = frame["id"]
                if frame.get("cmd") == "stop_auto":
                    st.session_state.auto_on = False
                    st.session_state.cam_result = {"msg": "Auto Memory stopped", "nonce": frame["id"], "event": None}
                    st.rerun()
                is_auto = frame.get("mode") == "auto"
                if is_auto and (not st.session_state.auto_on or
                                st.session_state.auto_stats["analyzed"] >= AUTO_MAX_SAMPLES):
                    if st.session_state.auto_on:
                        st.session_state.auto_on = False
                        msg = f"Auto Memory paused after {AUTO_MAX_SAMPLES} frames — start it again to continue"
                    else:
                        msg = "Auto Memory is off — frame ignored"
                    st.session_state.cam_result = {"msg": msg, "nonce": frame["id"], "event": None}
                    st.rerun()
                try:
                    with st.spinner("Perception → Understanding → Memory"):
                        e, ok, stages, msg = process_frame(base64.b64decode(frame["image"]), place, auto=is_auto)
                except Exception as ex:
                    e, stages, msg = None, {"PERCEPTION": "on", "UNDERSTANDING": "err"}, f"Capture failed: {ex}"
                prev_event = st.session_state.get("cam_result", {}).get("event")
                st.session_state.cam_result = {"msg": msg, "nonce": frame["id"],
                                               "event": e["id"] if e else (prev_event if is_auto else None),
                                               "stages": stages}
                st.rerun()
            st.markdown(stages_html(res.get("stages", {})) if res.get("stages") else
                        stages_html({}), unsafe_allow_html=True)
            last = res.get("event")
            ev_ = next((x for x in EVENTS if x["id"] == last), None) if last else None
            if ev_:
                card = memory_formed_html(ev_)
                if card:
                    iframe(card, 150)
                else:
                    memory_card(ev_, show_image=False)
            elif res.get("msg") and res.get("stages", {}).get("UNDERSTANDING") == "err":
                st.error(res["msg"])
            elif res.get("msg"):
                st.caption(res["msg"])

    elif mode == "Rewind":
        ordered = sorted(EVENTS, key=lambda e: e["ts"])
        idx = st.select_slider("Rewind my day", options=list(range(len(ordered))), value=len(ordered) - 1,
                               format_func=lambda i: f"{fmt(ordered[i]['ts'])} · {ordered[i]['place']}")
        strip, sh = timeline_strip_html(ordered, ordered[idx]["id"])
        iframe(strip, sh)
        show_frame(ordered[idx], False)

    elif mode == "Demo scenes":
        ordered = sorted(EVENTS, key=lambda e: e["ts"])
        show_frame(ordered[-1], True)
        st.caption("Simulated captures for demos — no camera needed. They go through the same memory pipeline.")
        cols = st.columns(len(SCENES))
        for col, (label, sc) in zip(cols, SCENES.items()):
            if col.button(label, width="stretch"):
                add_event(sc["place"], sc["caption"], [dict(o) for o in sc["objects"]])
                st.rerun()

    else:
        st.caption("Fallback: upload a photo instead of using the live camera.")
        up = st.file_uploader("Photo", type=["jpg", "jpeg", "png", "webp"], label_visibility="collapsed")
        place_u = st.text_input("Where was this?", "", placeholder="optional")
        if up is not None and st.button("Remember this", type="primary", disabled=not provider()):
            try:
                e, ok, stages, msg = process_frame(up.getvalue(), place_u)
                st.markdown(stages_html(stages), unsafe_allow_html=True)
                if e:
                    card = memory_formed_html(e)
                    if card:
                        iframe(card, 150)
                    st.caption(msg)
                else:
                    st.error(msg)
            except Exception as ex:
                st.error(f"Capture failed: {ex}")

# ================================================================= MEMORIES
if NAV == "Memories":
    allmem = sorted([e for e in EVENTS if e.get("place") != "AFTERSIGHT agent"], key=lambda e: e["ts"])
    q = st.text_input("Search", key="mem_q", placeholder="Search objects, colors, places, text, what you were doing…",
                      label_visibility="collapsed")
    f1, f2, f3 = st.columns([1.2, 1.4, 1.2])
    when = f1.segmented_control("Time", ["All day", "Morning", "Afternoon", "Evening"], default="All day",
                                key="mem_when", label_visibility="collapsed") or "All day"
    places = sorted({e["place"] for e in allmem})
    pick_places = f2.multiselect("Places", places, key="mem_places", placeholder="All places",
                                 label_visibility="collapsed")
    kind = f3.segmented_control("Source", ["All", "Photos", "Simulated", "Auto"], default="All", key="mem_kind",
                                label_visibility="collapsed") or "All"

    def keep(e):
        h = datetime.fromisoformat(e["ts"]).hour
        if when == "Morning" and not 5 <= h < 12:
            return False
        if when == "Afternoon" and not 12 <= h < 17:
            return False
        if when == "Evening" and not (h >= 17 or h < 5):
            return False
        if pick_places and e["place"] not in pick_places:
            return False
        src = source_of(e)
        return kind == "All" or (kind == "Photos" and src in ("photo", "auto")) or (kind == "Simulated" and src == "simulated") \
            or (kind == "Auto" and src == "auto")

    filtered = [e for e in allmem if keep(e)]
    if q.strip():
        hits = memory_search.search_events(q, filtered, k=50)
        qt = memory_search.terms(q)
        matched = [e for e in hits if any(t in memory_search.event_text(e) for t in qt)] if qt else hits
        filtered = sorted(matched, key=lambda e: e["ts"])
        st.caption(f"{len(filtered)} memories match “{q}” — keyword search over captions, objects, colors, places and "
                   "recorded details (photos themselves aren't searched; ask in ASK to re-examine them).")
    else:
        st.caption(f"{len(filtered)} memories")

    if filtered:
        st.markdown("### Rewind my day")
        idx = st.select_slider("Rewind", options=list(range(len(filtered))), value=len(filtered) - 1,
                               format_func=lambda i: f"{fmt(filtered[i]['ts'])} · {filtered[i]['place']}",
                               key="mem_rewind", label_visibility="collapsed")
        strip, sh = timeline_strip_html(filtered, filtered[idx]["id"])
        iframe(strip, sh)
        a, b = st.columns([1.7, 1], gap="large")
        with a:
            show_frame(filtered[idx], False, height=520)
        with b:
            memory_card(filtered[idx], show_image=False)
            e_ = filtered[idx]
            for k_, lab in (("scene", "Scene"), ("interactions", "Doing"), ("details", "Details")):
                if e_.get(k_):
                    val = e_[k_] if isinstance(e_[k_], str) else " · ".join(map(str, e_[k_]))
                    st.markdown(f"<div class='kv'><span>{lab}</span><span style='text-align:right;max-width:70%'>"
                                f"{html_escape(val)}</span></div>", unsafe_allow_html=True)

    st.markdown("### How things changed")
    state = compute_state(allmem)
    tracked = {}
    for e in filtered:
        for o in e.get("objects", []):
            tracked.setdefault(o["name"].lower().strip(), []).append((e, o))
    shown = 0
    for name, hist in sorted(tracked.items(), key=lambda kv: -len(kv[1])):
        if len(hist) < 2:
            continue
        shown += 1
        steps = []
        for e, o in hist:
            ev, c = o.get("event", "observed"), o.get("count")
            if ev == "observed":
                what = "empty" if c == 0 else (f"{c} seen" if c is not None else f"seen · {e['place']}")
                steps.append(f"<div class='stepx'><span class='t'>{fmt(e['ts']).upper()} · OBSERVED</span>{html_escape(what)}</div>")
            else:
                what = {"used": f"−{c} used", "added": f"+{c} added", "purchased": f"{c} bought"}.get(ev, ev)
                steps.append(f"<div class='stepx inf'><span class='t'>{fmt(e['ts']).upper()}</span>"
                             f"<span class='k'>CHANGE · </span>{html_escape(what)}</div>")
        s = state.get(name, {})
        where = f"{s.get('place')}" + (f", {fmt(s['last_observed'])}" if s.get("last_observed") else "")
        if s.get("count") is None or name in ("keys", "laptop", "car", "medication bottle", "laptop charger"):
            latest = f"last seen at {where}"
        elif s["basis"] == "observed":
            latest = f"{s['count']} observed ({where})" if s["count"] else f"none observed remaining ({where})"
        else:
            latest = (f"about {s['count']} — inferred from changes since the last look" if s["count"]
                      else "none left — inferred from changes since the last look")
        st.markdown(f"**{html_escape(name.capitalize())}**<div class='chain'>" + "<span class='arrow'>→</span>".join(steps)
                    + f"</div><div class='latest'>Latest known state: {html_escape(latest)}</div>", unsafe_allow_html=True)
    if not shown:
        st.caption("Changes appear once something has been seen more than once.")

    if len(filtered) >= 2:
        with st.expander("Compare two moments"):
            labels = {e["id"]: f"{fmt(e['ts'])} · {e['place']}" for e in filtered}
            ids = list(labels)
            c1, c2 = st.columns(2)
            ia = c1.selectbox("Earlier", ids, index=max(0, len(ids) - 2), format_func=labels.get, key="cmp_a")
            ib = c2.selectbox("Later", ids, index=len(ids) - 1, format_func=labels.get, key="cmp_b")
            ea, eb = next(e for e in filtered if e["id"] == ia), next(e for e in filtered if e["id"] == ib)
            with c1:
                memory_card(ea, compact=True)
            with c2:
                memory_card(eb, compact=True)
            oa = {o["name"].lower(): o.get("count") for o in ea.get("objects", [])}
            obb = {o["name"].lower(): o.get("count") for o in eb.get("objects", [])}
            diff = ([f"＋ {n}" for n in obb if n not in oa] + [f"－ {n}" for n in oa if n not in obb]
                    + [f"{n}: {oa[n]} → {obb[n]}" for n in oa if n in obb and oa[n] != obb[n] and None not in (oa[n], obb[n])])
            st.markdown("**Differences in what was recorded:** " + (", ".join(diff) if diff else "none"))

    st.markdown("### All memories")
    cols = st.columns(3, gap="medium")
    for i, e in enumerate(reversed(filtered)):
        with cols[i % 3]:
            memory_card(e)

# ================================================================= ASK
def handle_question(q, voice_in=False):
    st.session_state.chat.append({"role": "user", "text": q, "voice": voice_in})
    try:
        with st.spinner("Retrieving memories → reasoning…"):
            out = ask(q, st.session_state.events)
        msg = {"role": "assistant", "text": out.get("answer", ""), "status": out.get("status", "unknown"),
               "evidence_ids": out.get("evidence_ids", []), "source": out["source"],
               "reexamined": out.get("reexamined", []), "hydra_hits": out["hits"][:4], "actions": []}
        for p in out.get("propose_shopping", []):
            propose(p["item"], p["reason"])
            a = next((a for a in actions() if a["item"] == p["item"] and a["status"] == "pending"), None)
            if a:
                msg["actions"].append(a["id"])
        if voice_in and voice.available() and msg["text"]:
            try:
                st.session_state.tts = {"audio": voice.speak(msg["text"]), "for": len(st.session_state.chat)}
            except Exception as ex:
                msg["tts_error"] = str(ex)
        st.session_state.chat.append(msg)
    except Exception as ex:
        st.session_state.chat.append({"role": "assistant", "text": f"Something went wrong: {ex}", "status": "unknown"})


if NAV == "Ask":
    st.markdown("<div class='hero-h' style='font-size:clamp(30px,3.6vw,52px)'>Ask anything about what you've seen.</div>"
                "<div class='hero-sub' style='margin-bottom:14px'>Answers come from your memories — with the evidence.</div>",
                unsafe_allow_html=True)
    vstat = ("" if voice.available() else "<span style='color:#95A5A3'>Voice needs OPENAI_API_KEY in .env — "
             "type your question instead.</span>")
    vres = _voice(status=vstat or None, disabled=not voice.available(), key="voice_ptt", default=None)
    if vres and vres.get("id") and vres["id"] != st.session_state.get("voice_done"):
        st.session_state.voice_done = vres["id"]
        try:
            text = voice.transcribe(base64.b64decode(vres["audio"]), vres.get("mime", "audio/webm"))
        except Exception as ex:
            text, err = "", str(ex)
        else:
            err = ""
        if text:
            handle_question(text, voice_in=True)
        else:
            st.session_state.chat.append({"role": "assistant", "status": "unknown",
                                          "text": f"I couldn't hear a question{': ' + err if err else ''}. Try again or type it."})
        st.rerun()

    if not st.session_state.chat:
        sugg = ["What color was the object I was holding?", "What was the last thing I captured?",
                "Where did I leave my keys?", "What does the sticky note on my monitor say?",
                "What changed in my refrigerator?", "What should I buy?"]
        st.markdown("<div class='eyebrow' style='color:#95A5A3;margin-top:10px'>Try</div>", unsafe_allow_html=True)
        cols = st.columns(3)
        for i, sq in enumerate(sugg):
            if cols[i % 3].button(sq, width="stretch", key=f"sg{i}"):
                handle_question(sq)
                st.rerun()

    by_id = {e["id"]: e for e in EVENTS}
    act_by_id = {a["id"]: a for a in actions()}
    for n, m in enumerate(st.session_state.chat):
        if m["role"] == "user":
            st.markdown(f"<div class='qbubble'><span>{'🎙 ' if m.get('voice') else ''}{html_escape(m['text'])}</span></div>",
                        unsafe_allow_html=True)
            continue
        stt = m.get("status") if m.get("status") in ("observed", "inferred", "unknown") else "unknown"
        with st.container():
            st.markdown(f"<div class='acard'><span class='badge b-{stt}'>{stt.upper()}</span>"
                        f"<span class='srcline'>{html_escape(m.get('source', ''))}</span>"
                        f"<div class='answer'>{html_escape(m['text'])}</div>"
                        + ("<span class='chip'>re-examined retained photo</span>" if m.get("reexamined") else "")
                        + "</div>", unsafe_allow_html=True)
            tts = st.session_state.get("tts")
            if tts and tts.get("for") == n:
                st.audio(tts["audio"], format="audio/mp3", autoplay=(n == len(st.session_state.chat) - 1))
            if m.get("tts_error"):
                st.caption(f"Spoken answer unavailable: {m['tts_error']}")
            ev_ids = [i for i in m.get("evidence_ids", []) if i in by_id]
            if ev_ids:
                cols = st.columns(4)
                for c, i in zip(cols, ev_ids[:4]):
                    with c:
                        e = by_id[i]
                        t = thumb(e)
                        if t:
                            st.image(t, width="stretch")
                        st.markdown(f"<div class='meta muted' style='font-size:12.5px'>{fmt(e['ts'])} · "
                                    f"{html_escape(e['place'])}{' · re-examined' if i in m.get('reexamined', []) else ''}"
                                    "</div>", unsafe_allow_html=True)
            if ev_ids or m.get("hydra_hits"):
                with st.expander("Evidence details"):
                    for i in ev_ids:
                        st.markdown(f"<span class='mono'>{i}</span> · {html_escape(mem_text(by_id[i]))}",
                                    unsafe_allow_html=True)
                    for h in m.get("hydra_hits") or []:
                        st.caption(f"HydraDB ({h.get('src')}): {h['text'][:260]}")
            for aid in m.get("actions", []):
                a = act_by_id.get(aid)
                if a and a["status"] == "pending":
                    action_card(a, f"ask{n}")
                elif a:
                    st.caption(f"{'✓ Added' if a['status'] == 'approved' else '✕ Dismissed'}: {a['item']}")

    q = st.chat_input("Ask anything about what you've seen…")
    if q:
        handle_question(q)
        st.rerun()
    if st.session_state.chat:
        if st.button("Clear conversation"):
            st.session_state.chat = []
            st.session_state.pop("tts", None)
            st.rerun()

# ================================================================= ACTIVITY
if NAV == "Activity":
    acts = actions()
    pend = [a for a in acts if a["status"] == "pending"]
    k1, k2, k3, k4 = st.columns(4)
    for col, (v, l) in zip((k1, k2, k3, k4), [(len(pend), "waiting for you"),
                                              (sum(a["status"] == "approved" for a in acts), "approved"),
                                              (sum(a["status"] == "dismissed" for a in acts), "dismissed"),
                                              (len(st.session_state.shopping), "on your list")]):
        col.markdown(f"<div class='stat'><div class='v'>{v}</div><div class='l'>{l}</div></div>", unsafe_allow_html=True)
    left, right = st.columns([1.4, 1], gap="large")
    with left:
        st.markdown("### Waiting for your approval")
        if pend:
            for a in pend:
                action_card(a, "act")
        else:
            st.caption("Nothing pending. When your memories suggest you're running low, a recommendation appears here "
                       "and in Ask — nothing happens until you approve it.")
        st.markdown("### History")
        done = [a for a in acts if a["status"] != "pending"]
        if done:
            for a in reversed(done):
                icon, cls = ("✓", "ok") if a["status"] == "approved" else ("✕", "muted")
                st.markdown(f"<div class='kv'><span><span class='{cls}'>{icon}</span> {html_escape(a['status'].capitalize())}: "
                            f"add {html_escape(a['item'])}</span><span>{fmt(a['decided'] or a['ts'])}</span></div>",
                            unsafe_allow_html=True)
        else:
            st.caption("No decisions yet.")
    with right:
        st.markdown("### 🛒 Shopping list")
        if st.session_state.shopping:
            for it in list(st.session_state.shopping):
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"<div class='kv'><span style='color:#F3F6F5'>{html_escape(it)}</span><span></span></div>",
                            unsafe_allow_html=True)
                if c2.button("✕", key=f"rm-{it}", help="Remove from list"):
                    st.session_state.shopping.remove(it)
                    persist()
                    st.rerun()
        else:
            st.caption("Empty.")

# ================================================================= SETTINGS / SYSTEM
if NAV == "Settings":
    st.markdown("### System")
    st.caption("Engineering transparency — every status below comes from a live check, not a default.")
    synced = sum(1 for e in EVENTS if e.get("hydra"))
    rows = [
        ("HydraDB", ("<span class='ok'>connected</span>" if hydra_ok else "<span class='bad'>offline</span>") + f" · {html_escape(hstatus)}"),
        ("Storage backend", ("self-hosted graph (OpenCypher)" if _g_ok else "") + (" + " if _g_ok and client else "")
         + ("managed HydraDB" if client else "") or "local event log only"),
        ("Fallback", "local event log is always written; used for retrieval alongside HydraDB"
         if hydra_ok else "<span class='warnc'>ACTIVE — local event log only</span>"),
        ("Reasoning + vision model", f"<span class='ok'>{provider()}</span>" if provider() else "<span class='bad'>no key</span>"),
        ("Voice (OpenAI STT/TTS)", "<span class='ok'>available</span>" if voice.available() else "<span class='bad'>needs OPENAI_API_KEY</span>"),
        ("Memories in HydraDB", f"{synced} / {len(EVENTS)}"),
        ("Last retrieval", "—" if st.session_state.diag["last_query_hits"] is None else f"{st.session_state.diag['last_query_hits']} HydraDB hits"),
        ("Auto Memory", "on" if st.session_state.auto_on else "off"),
    ]
    for k_, v_ in rows:
        st.markdown(f"<div class='kv'><span>{k_}</span><span style='text-align:right'>{v_}</span></div>", unsafe_allow_html=True)
    if st.session_state.diag["last_error"]:
        st.error(st.session_state.diag["last_error"])
    b1, b2, b3, b4 = st.columns(4)
    if b1.button("Sync unsynced → HydraDB", disabled=not hydra_ok, width="stretch"):
        todo = [e for e in EVENTS if not e.get("hydra")]
        if hydra_ingest(todo):
            st.success(f"Ingested {len(todo)}")
        else:
            st.warning("Nothing ingested")
    if b2.button("Check indexing", disabled=client is None, width="stretch"):
        try:
            ids = [e["id"] for e in EVENTS if e.get("hydra")]
            s = client.context.status(database=DB, collection=COLL, ids=ids)
            states = [x.indexing_status for x in s.data.statuses]
            st.write({k: states.count(k) for k in set(states)})
        except Exception as ex:
            st.error(f"status: {ex}")
    if b3.button("Reconnect HydraDB", width="stretch"):
        hydra.clear()
        graph.clear()
        st.rerun()
    if b4.button("↺ Reset demo data", width="stretch"):
        st.session_state.events = [dict(e) for e in SEED]
        st.session_state.shopping, st.session_state.chat, st.session_state.pending = [], [], []
        st.session_state.actions = []
        persist()
        st.rerun()
    st.divider()
    st.markdown("#### State engine tests (deterministic, no LLM)")

    def ev(objs, place="kitchen fridge", caption="test"):
        return dict(id=f"t-{uuid.uuid4().hex[:5]}", ts=(NOW + timedelta(hours=1)).isoformat(),
                    place=place, caption=caption, objects=objs)

    tests = [
        ("Latest observation wins: 2 eggs at 6:05 PM", lambda: compute_state(SEED)["eggs"]["count"] == 2),
        ("Estimate (6−2=4) corrected by later observation",
         lambda: any("corrected" in n for n in compute_state(SEED)["eggs"]["notes"])),
        ("Using 2 more eggs → 0, marked estimated",
         lambda: (lambda s: s["count"] == 0 and s["basis"] == "estimated")(
             compute_state(SEED + [ev([ob("eggs", "used", 2)])])["eggs"])),
        ("Purchase does NOT change home inventory",
         lambda: compute_state(SEED + [ev([ob("eggs", "purchased", 12)], "store")])["eggs"]["count"] == 2),
        ("Keys: latest place is the hallway table", lambda: "hallway" in compute_state(SEED)["keys"]["place"]),
        ("Laptop last seen at the office", lambda: "office" in compute_state(SEED)["laptop"]["place"]),
    ]
    passed = 0
    for name, fn in tests:
        try:
            ok = bool(fn())
        except Exception:
            ok = False
        passed += ok
        st.write(("✅ " if ok else "❌ ") + name)
    st.metric("State engine", f"{passed}/{len(tests)} passed")

    st.divider()
    st.markdown("#### Agent eval (LLM over seed memories)")
    cases = [
        ("Do I have eggs?", ["2"]),
        ("Where are my keys?", ["hallway"]),
        ("Did I take my medication?", ["can't", "cannot", "not sure", "didn't see", "did not see", "no"]),
        ("Where did I park?", ["3"]),
        ("Is my laptop at home?", ["office"]),
    ]
    if st.button("Run agent eval", disabled=not provider()):
        score = 0
        for qq, needles in cases:
            try:
                a = ask(qq, [dict(e) for e in SEED], use_hydra=False).get("answer", "")
            except Exception as ex:
                a = f"error {ex}"
            ok = any(n.lower() in a.lower() for n in needles)
            score += ok
            st.write(("✅ " if ok else "❌ ") + f"**{qq}** → {a}")
        st.metric("Agent eval", f"{score}/{len(cases)} passed")

