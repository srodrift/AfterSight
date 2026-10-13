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

_cam = components.declare_component("aftersight_camera",
                                    path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera"))


def camera_component(**kw):
    return _cam(**kw)

load_dotenv()

DB = os.getenv("HYDRA_DATABASE", "aftersight")
COLL = os.getenv("HYDRA_COLLECTION", "glasses_memory")
EVENTS_F, SHOP_F = "events.json", "shopping.json"

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


def persist():
    save(EVENTS_F, st.session_state.events)
    save(SHOP_F, st.session_state.shopping)


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
    objs = ", ".join(f"{o['name']} ({o['event']}{'' if o.get('count') is None else ' ' + str(o['count'])})"
                     for o in e.get("objects", []))
    return f"[{e['ts']}] Smart glasses at {e['place']}: {e['caption']} Objects: {objs}. (memory id {e['id']})"


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


def llm(system, user, image=None):
    p = provider()
    if p == "anthropic":
        import anthropic
        content = [{"type": "text", "text": user}]
        if image:
            content.insert(0, {"type": "image", "source": {"type": "base64", "media_type": image[1], "data": image[0]}})
        r = anthropic.Anthropic().messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"), max_tokens=900,
            system=system, messages=[{"role": "user", "content": content}])
        return r.content[0].text
    if p == "openai":
        from openai import OpenAI
        content = [{"type": "text", "text": user}]
        if image:
            content.append({"type": "image_url", "image_url": {"url": f"data:{image[1]};base64,{image[0]}"}})
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


AGENT_SYSTEM = """You are AFTERSIGHT, the memory of the user's smart glasses.
Current time: {now}.
You get STATE (computed deterministically by replaying every observation in time order)
and EVIDENCE (memories retrieved from the HydraDB memory store).
Rules:
- Answer only from STATE and EVIDENCE. Trust STATE for counts and latest location.
- Always say WHEN and WHERE you last saw it, in a natural spoken style (1-3 sentences).
- Distinguish "observed" from "estimated" (estimated = inferred from usage/additions since the last look).
- Never claim something is there *now* just because it was seen earlier. Never claim an action happened that was not observed (e.g. taking medication).
- A purchase does not mean the item is at home until it is seen there.
- If the user is low on or out of a consumable (0-2 left, empty, nearly empty), or asks what to buy, propose items to add to the shopping list. Never add them yourself; the user must approve.
- If nothing is known, say so.
- "status": "observed" if the answer rests directly on the latest observation, "inferred" if it is derived from several memories (e.g. estimated after usage), "unknown" if there is insufficient evidence.
Return ONLY JSON: {{"answer": str, "status": "observed"|"inferred"|"unknown", "propose_shopping": [item names], "evidence_ids": [memory ids you relied on]}}"""


def ask(question, events, use_hydra=True):
    state = compute_state(events)
    hits, ctx = hydra_query(question) if use_hydra else ([], "")
    if not ctx:  # transparent local fallback
        ctx = "\n".join(mem_text(e) for e in sorted(events, key=lambda e: e["ts"])[-15:])
        source = "local event log (HydraDB unavailable or returned nothing)"
    else:
        n_m = sum(h.get("src") == "managed" for h in hits)
        n_g = sum(h.get("src") == "graph" for h in hits)
        parts = ([f"HydraDB managed: {n_m}"] if n_m else []) + ([f"HydraDB self-hosted graph (OpenCypher): {n_g}"] if n_g else [])
        source = " + ".join(parts)
    user = (f"STATE:\n{json.dumps(state, indent=1)}\n\nEVIDENCE ({source}):\n{ctx}\n\n"
            f"SHOPPING LIST: {st.session_state.shopping}\n\nQUESTION: {question}")
    out = parse_json(llm(AGENT_SYSTEM.format(now=NOW.strftime('%-I:%M %p, %A %b %d')), user))
    out["source"], out["hits"] = source, hits
    out["propose_shopping"] = [i for i in out.get("propose_shopping", []) if i not in st.session_state.shopping]
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
import html as _html
from PIL import Image

html_escape = _html.escape

IMG_DIR = "images"
os.makedirs(IMG_DIR, exist_ok=True)

st.markdown("""
<style>
.stApp {background: radial-gradient(1200px 600px at 50% -10%, #12222b 0%, #07090b 55%) fixed;}
[data-testid="stSidebar"] {background: #0b0f12; border-right: 1px solid #1a2328;}
h1, h2, h3, p, li, label, .stMarkdown {letter-spacing: .01em}
.hero {text-align:center; padding: 6px 0 2px}
.hero .name {font: 600 44px/1.05 -apple-system, "SF Pro Display", Inter, sans-serif; letter-spacing: .32em; color:#eef6f8}
.hero .tag {color:#7CFFCB; font-size: 15px; letter-spacing:.08em; margin-top:8px}
.hero .sub {color:#7d8a91; font-size: 13px; margin-top:4px}
.card {background: rgba(255,255,255,.035); border:1px solid rgba(124,255,203,.14); border-radius:16px; padding:14px 16px; margin:8px 0}
.muted {color:#7d8a91; font-size:13px}
.chain {display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin:6px 0 4px}
.step {background: rgba(124,255,203,.06); border:1px solid rgba(124,255,203,.22); border-radius:12px; padding:8px 12px; color:#dfeef2; font-size:14px}
.step .t {color:#7d8a91; font-size:11px; letter-spacing:.08em; display:block}
.step.est {border-style:dashed}
.arrow {color:#3d5a63}
.latest {color:#7CFFCB; font-size:14px; margin: 2px 0 10px}
.badge {display:inline-block; border-radius:999px; padding:2px 10px; font-size:11px; letter-spacing:.12em; font-weight:600}
.b-observed {background:#0f3a2c; color:#7CFFCB} .b-inferred {background:#3a320f; color:#ffd34d} .b-unknown {background:#2a2f33; color:#b8c3c8}
.stage {display:flex; gap:10px; margin:6px 0 10px}
.stage span {border:1px solid #23343b; border-radius:999px; padding:4px 12px; font-size:12px; letter-spacing:.12em; color:#56656c}
.stage span.on {border-color:#7CFFCB; color:#7CFFCB}
.stage span.err {border-color:#ff6b6b; color:#ff6b6b}
@media (max-width: 640px){ .hero .name{font-size:30px} }
</style>""", unsafe_allow_html=True)

st.markdown("""<style>
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], footer,
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], #MainMenu {display:none !important}
.block-container {padding-top: 1.2rem !important; max-width: min(85vw, 1500px) !important; width: 100% !important}
[data-testid='stAppDeployButton'], [data-testid='stMainMenu'], [data-testid='stStatusWidget'] {display:none !important}
.brand {display:flex; align-items:baseline; gap:14px; margin-bottom:6px}
.brand .n {font: 600 20px/1 -apple-system, "SF Pro Display", Inter, sans-serif; letter-spacing:.32em; color:#eef6f8}
.brand .t {color:#7d8a91; font-size:12px; letter-spacing:.06em}
div[data-testid="stSegmentedControl"] button, div[data-testid="stButtonGroup"] button {letter-spacing:.14em; font-size:12px}
.home-hero {text-align:center; padding: 42px 10px 18px}
.home-hero .name {font: 600 76px/1.05 -apple-system, "SF Pro Display", Inter, sans-serif; letter-spacing:.34em; color:#eef6f8}
.home-hero .tag {color:#7CFFCB; font-size:23px; letter-spacing:.04em; margin-top:14px}
.home-hero .sub {color:#8b989f; font-size:15px; margin-top:6px}
.cap {background: rgba(255,255,255,.03); border:1px solid rgba(124,255,203,.12); border-radius:20px; padding:20px; height:100%}
.cap .k {color:#7CFFCB; font: 600 13px Menlo, monospace; letter-spacing:.3em}
.cap .h {color:#eef6f8; font-size:17px; margin:8px 0 4px}
.cap .d {color:#8b989f; font-size:14px; line-height:1.45}
.stat {text-align:center; padding:10px 4px}
.stat .v {font: 600 44px/1 -apple-system, Inter, sans-serif; color:#eef6f8}
.stat .l {color:#7d8a91; font-size:11px; letter-spacing:.18em; margin-top:6px}
button[kind="primary"], button[kind="primary"] p {color:#04140e !important; font-weight:600}
.item {display:flex; justify-content:space-between; align-items:center; padding:12px 16px; margin:6px 0;
       background: rgba(255,255,255,.035); border-radius:14px; color:#e6eef1}
@media (max-width: 640px){ .home-hero .name{font-size:34px; letter-spacing:.22em} .home-hero{padding-top:20px} }
</style>""", unsafe_allow_html=True)
if st.session_state.get("nav", "HOME") not in (None, "HOME"):
    st.markdown("<div class='brand'><span class='n'>AFTERSIGHT</span>"
                "<span class='t'>Your eyes see it. Your AI remembers it.</span></div>", unsafe_allow_html=True)

client, hstatus = hydra()
hydra_ok = client is not None and hstatus == "connected"
_g_ok, _g_status = graph()
if graph_store.enabled():
    hstatus = (f"managed: {hstatus} · " if os.getenv("HYDRA_DB_API_KEY") else "") + f"self-hosted graph: {_g_status}"
    hydra_ok = hydra_ok or _g_ok


def show_frame(event, live):
    if event.get("image") and os.path.exists(event["image"]):
        with open(event["image"], "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        frame = scene.render_photo(event, b64, fmt, event.get("hydra", False), live=live)
    else:
        frame = scene.render(event, fmt, event.get("hydra", False), live=live)
    page = ("<html><body style='margin:0;background:transparent'>"
            f"<div style='max-width:900px;margin:0 auto'>{frame}</div></body></html>")
    if hasattr(st, "iframe"):
        st.iframe(page, height=600)
    else:
        components.html(page, height=600)


def stages_html(states):
    names = ["VISION", "UNDERSTANDING", "MEMORY"]
    return "<div class='stage'>" + "".join(
        f"<span class='{states.get(n, '')}'>{n}</span>" for n in names) + "</div>"


# ---- navigation
NAV_ITEMS = ["HOME", "VISION", "MEMORIES", "ASK", "ACTIVITY"]
if "nav" not in st.session_state:
    st.session_state.nav = "HOME"


def go(view):
    st.session_state.nav = view


st.segmented_control("Navigate", NAV_ITEMS + ["SYSTEM"], key="nav", label_visibility="collapsed", width="stretch",
                     format_func=lambda v: "⚙" if v == "SYSTEM" else v)
NAV = st.session_state.nav or "HOME"

# ---- HOME
if NAV == "HOME":
    st.markdown("<div class='home-hero'><div class='name'>AFTERSIGHT</div>"
                "<div class='tag'>Your eyes see it. Your AI remembers it.</div>"
                "<div class='sub'>An intelligent memory for the physical world.</div></div>", unsafe_allow_html=True)
    _, b1, b2, _ = st.columns([1, 1.2, 1.2, 1])
    b1.button("◉  Start Seeing", type="primary", width="stretch", on_click=go, args=("VISION",))
    b2.button("◷  Explore Memories", width="stretch", on_click=go, args=("MEMORIES",))
    st.write("")
    c1, c2, c3 = st.columns(3)
    for col, (k, h, d) in zip((c1, c2, c3), [
        ("SEE", "Your camera, as a wearable", "Point at your world and tap Remember This. A vision model turns the frame into objects, counts and place."),
        ("REMEMBER", "A memory that knows what changed", "Every observation is a timestamped node in HydraDB. A temporal engine works out the latest known state, not just the last photo."),
        ("ACT", "From memory to action", "Ask in plain language. Answers come with evidence and say observed, inferred or unknown — then AFTERSIGHT suggests what to do, with your approval."),
    ]):
        col.markdown(f"<div class='cap'><div class='k'>{k}</div><div class='h'>{h}</div><div class='d'>{d}</div></div>",
                     unsafe_allow_html=True)
    evs = st.session_state.events
    stats = [
        (len(evs), "MEMORIES"),
        (sum(1 for e in evs if e.get("image")), "CAMERA CAPTURES"),
        (len({o["name"].lower() for e in evs for o in e.get("objects", [])}), "THINGS TRACKED"),
        (sum(1 for e in evs if e.get("hydra")), "STORED IN HYDRADB"),
        (len(st.session_state.shopping), "ACTIONS APPROVED"),
    ]
    st.write("")
    for col, (v, l) in zip(st.columns(len(stats)), stats):
        col.markdown(f"<div class='stat'><div class='v'>{v}</div><div class='l'>{l}</div></div>", unsafe_allow_html=True)

# ---- ACTIVITY (agent actions)
if NAV == "ACTIVITY":
    st.markdown("#### 🛒 Shopping list")
    if st.session_state.shopping:
        for it in st.session_state.shopping:
            st.markdown(f"<div class='item'><span>{html_escape(it)}</span><span class='muted'>approved</span></div>",
                        unsafe_allow_html=True)
        if st.button("Clear list"):
            st.session_state.shopping = []
            persist()
            st.rerun()
    else:
        st.caption("Nothing yet. When you're running low, AFTERSIGHT suggests items in ASK — you approve each one.")
    if st.session_state.pending:
        st.markdown("#### Waiting for your approval")
        for it in st.session_state.pending:
            st.markdown(f"<div class='item'><span>Add {html_escape(it)}?</span><span class='muted'>approve in ASK</span></div>",
                        unsafe_allow_html=True)
    acts = [e for e in st.session_state.events if e.get("place") == "AFTERSIGHT agent"]
    st.markdown("#### Action history")
    if acts:
        for e in reversed(acts):
            st.markdown(f"<div class='item'><span>{html_escape(e['caption'])}</span><span class='muted'>{fmt(e['ts'])}</span></div>",
                        unsafe_allow_html=True)
    else:
        st.caption("No actions taken yet.")
    st.write("")
    st.button("⚙  Settings · System", on_click=go, args=("SYSTEM",))

# ---- VISION
def process_frame(jpeg_bytes, place):
    """Real pipeline: image -> vision model -> structured objects -> memory (local + HydraDB).
    Returns (event or None, hydra_ok, stages dict, message)."""
    stages = {"VISION": "on"}
    im = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    im.thumbnail((1280, 1280))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()
    raw = llm("You are the vision system of smart glasses. Return ONLY JSON.",
              'List the household objects visible. JSON: {"caption": str, "place_guess": str, "objects": '
              '[{"name": str (lowercase, plural for countable food like "eggs"), "event": "observed", '
              '"count": int or null}]}. Use null for count when you cannot count reliably. '
              "An empty egg carton is eggs with count 0. place_guess is the kind of place, e.g. 'kitchen fridge'.",
              image=(b64, "image/jpeg"))
    stages["UNDERSTANDING"] = "on"
    v = parse_json(raw)
    objs = [o for o in v.get("objects", []) if isinstance(o, dict) and o.get("name")]
    for o in objs:
        o["event"] = "observed"
        if not isinstance(o.get("count"), int):
            o["count"] = None
    if not objs:
        stages["UNDERSTANDING"] = "err"
        return None, False, stages, "No objects recognized. Try again closer."
    path = os.path.join(IMG_DIR, f"{uuid.uuid4().hex[:10]}.jpg")
    with open(path, "wb") as f:
        f.write(buf.getvalue())
    where = place.strip() or str(v.get("place_guess") or "unknown place")
    e, ok = add_event(where, v.get("caption", "Camera observation"), objs, image=path)
    stages["MEMORY"] = "on"
    names = ", ".join(f"{o['name']}" + ("" if o["count"] is None else f" ×{o['count']}") for o in objs[:5])
    if ok:
        where_saved = "saved to HydraDB"
    elif not any_store_connected():
        where_saved = "saved locally (HydraDB not connected)"
    else:
        where_saved = "saved locally (HydraDB write failed — see SYSTEM)"
    msg = f"✓ Remembered {names} · {where_saved}"
    return e, ok, stages, msg


if NAV == "VISION":
    mode = st.radio("Mode", ["◉ Live Vision", "⏪ Rewind my day", "🎬 Demo mode", "⇪ Upload"], horizontal=True,
                    label_visibility="collapsed")

    if mode == "◉ Live Vision":
        if not provider():
            st.warning("Add an Anthropic or OpenAI key to .env so captured frames can be understood.")
        res = st.session_state.get("cam_result", {})
        frame = camera_component(status=res.get("msg", ""), status_nonce=res.get("nonce"), key="live_cam",
                                 default=None)
        place = st.text_input("Where are you? (optional — AFTERSIGHT will guess)", key="cam_place",
                              placeholder="e.g. kitchen fridge")
        if frame and frame.get("id") and frame["id"] != st.session_state.get("cam_done"):
            st.session_state.cam_done = frame["id"]
            box = st.empty()
            try:
                with st.spinner("Vision → Understanding → Memory"):
                    e, ok, stages, msg = process_frame(base64.b64decode(frame["image"]), place)
                box.markdown(stages_html(stages), unsafe_allow_html=True)
            except Exception as ex:
                e, msg = None, f"Capture failed: {ex}"
                box.markdown(stages_html({"VISION": "err"}), unsafe_allow_html=True)
            st.session_state.cam_result = {"msg": msg, "nonce": frame["id"], "event": e["id"] if e else None}
            st.rerun()
        last = res.get("event")
        if last:
            ev_ = next((x for x in st.session_state.events if x["id"] == last), None)
            if ev_:
                st.markdown(stages_html({"VISION": "on", "UNDERSTANDING": "on", "MEMORY": "on"}), unsafe_allow_html=True)
                c1, c2 = st.columns([1, 2])
                if ev_.get("image") and os.path.exists(ev_["image"]):
                    c1.image(ev_["image"])
                c2.markdown(f"<div class='card'><span class='muted'>{fmt(ev_['ts'])} · {html_escape(ev_['place'])}"
                            f"</span><br>{html_escape(ev_['caption'])}<br><span class='muted'>"
                            + html_escape(", ".join(o["name"] + ("" if o.get("count") is None else f" ×{o['count']}")
                                                    for o in ev_["objects"]))
                            + f" · {'HydraDB ✓' if ev_.get('hydra') else 'local only'} · {ev_['id']}</span></div>",
                            unsafe_allow_html=True)
        elif res.get("msg"):
            st.error(res["msg"])

    elif mode == "⏪ Rewind my day":
        ordered = sorted(st.session_state.events, key=lambda e: e["ts"])
        idx = st.select_slider("Scrub through what your glasses saw", options=list(range(len(ordered))),
                               value=len(ordered) - 1,
                               format_func=lambda i: f"{fmt(ordered[i]['ts'])} · {ordered[i]['place']}")
        show_frame(ordered[idx], False)

    elif mode == "🎬 Demo mode":
        ordered = sorted(st.session_state.events, key=lambda e: e["ts"])
        show_frame(ordered[-1], True)
        st.caption("Simulated glasses captures — no camera needed")
        cols = st.columns(len(SCENES))
        for col, (label, sc) in zip(cols, SCENES.items()):
            if col.button(label, width="stretch"):
                add_event(sc["place"], sc["caption"], [dict(o) for o in sc["objects"]])
                st.rerun()

    else:
        up = st.file_uploader("Fallback: upload a photo", type=["jpg", "jpeg", "png", "webp"])
        place = st.text_input("Where was this?", "")
        if up is not None and st.button("◉ Remember this", type="primary", disabled=not provider()):
            try:
                e, ok, stages, msg = process_frame(up.getvalue(), place)
                st.markdown(stages_html(stages), unsafe_allow_html=True)
                if e:
                    st.success(msg)
                else:
                    st.error(msg)
            except Exception as ex:
                st.error(f"Capture failed: {ex}")

# ---- MEMORY
if NAV == "MEMORIES":
    events = sorted(st.session_state.events, key=lambda e: e["ts"])
    state = compute_state(events)
    st.markdown("#### How things changed")
    tracked = {}
    for e in events:
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
                what = "empty" if c == 0 else (f"{c} seen" if c is not None else "seen")
                if name in ("keys", "laptop", "car", "medication bottle"):
                    what = f"seen · {e['place']}"
            else:
                what = {"used": f"−{c} used", "added": f"+{c} added", "purchased": f"{c} bought"}.get(ev, ev)
            cls = "step" if ev == "observed" else "step est"
            steps.append(f"<div class='{cls}'><span class='t'>{fmt(e['ts']).upper()}</span>{html_escape(what)}</div>")
        s = state[name]
        where = f"{s['place']}" + (f", {fmt(s['last_observed'])}" if s["last_observed"] else "")
        if s["count"] is None or name in ("keys", "laptop", "car", "medication bottle", "laptop charger"):
            latest = f"last seen at {where}"
        elif s["basis"] == "observed":
            latest = f"{s['count']} observed ({where})" if s["count"] else f"none observed remaining ({where})"
        else:
            latest = (f"about {s['count']} — inferred from usage since the last look" if s["count"]
                      else "none left — inferred from usage since the last look")
        extra = [n for n in s["notes"] if "corrected" not in n]
        if extra:
            latest += f" · {extra[-1]}"
        st.markdown(f"**{name.capitalize()}**<div class='chain'>" + "<span class='arrow'>→</span>".join(steps)
                    + f"</div><div class='latest'>Latest known state: {html_escape(latest)}</div>",
                    unsafe_allow_html=True)
    if not shown:
        st.caption("Changes appear once an object has been seen more than once.")

    st.markdown("#### Every memory")
    for e in reversed(events):
        c1, c2 = st.columns([1, 5])
        if e.get("image") and os.path.exists(e["image"]):
            c1.image(e["image"])
        else:
            c1.markdown("<div class='muted' style='padding-top:10px'>simulated</div>", unsafe_allow_html=True)
        objs = ", ".join(o["name"] for o in e.get("objects", [])) or "—"
        store = "HydraDB ✓" if e.get("hydra") else "local only"
        c2.markdown(f"<div class='card'><span class='muted'>{fmt(e['ts'])} · {html_escape(e['place'])}</span><br>"
                    f"{html_escape(e['caption'])}<br><span class='muted'>objects: {html_escape(objs)} · "
                    f"{store} · {e['id']}</span></div>", unsafe_allow_html=True)

# ---- RECALL
if NAV == "ASK":
    st.caption("Ask your memory")
    sugg = ["Where did I leave my keys?", "Do I have eggs?", "What changed in my refrigerator?", "What should I buy?"]
    clicked = None
    cols = st.columns(len(sugg))
    for col, sq in zip(cols, sugg):
        if col.button(sq, width="stretch"):
            clicked = sq

    for m in st.session_state.chat:
        with st.chat_message(m["role"], avatar="👓" if m["role"] == "assistant" else None):
            if m.get("status"):
                stt = m["status"] if m["status"] in ("observed", "inferred", "unknown") else "unknown"
                st.markdown(f"<span class='badge b-{stt}'>{stt.upper()}</span>", unsafe_allow_html=True)
            st.markdown(m["text"])
            if m.get("meta"):
                meta = m["meta"]
                with st.expander("Evidence"):
                    st.caption(f"Retrieved from: {meta['retrieved_from']}")
                    ids = meta.get("evidence_ids", [])
                    by_id = {e["id"]: e for e in st.session_state.events}
                    for i in ids:
                        e = by_id.get(i)
                        if not e:
                            continue
                        if e.get("image") and os.path.exists(e["image"]):
                            st.image(e["image"], width=220)
                        st.markdown(f"`{fmt(e['ts'])}` · {e['place']} — {e['caption']}")
                    if meta.get("hydra_hits"):
                        st.caption("Top HydraDB hits")
                        for h in meta["hydra_hits"]:
                            st.code(h["text"][:300], language=None)

    for item in list(st.session_state.pending):
        st.markdown(f"<div class='card'>🛒 <b>Add {html_escape(item)} to your shopping list?</b></div>",
                    unsafe_allow_html=True)
        a, d, _ = st.columns([1, 1, 4])
        if a.button("Approve", key=f"ap-{item}", type="primary"):
            st.session_state.shopping.append(item)
            st.session_state.pending.remove(item)
            add_event("AFTERSIGHT agent", f"User approved adding {item} to the shopping list.", [])
            st.session_state.chat.append({"role": "assistant", "text": f"Done — **{item}** is on your shopping list."})
            persist()
            st.toast(f"Added {item} to shopping list")
            st.rerun()
        if d.button("Dismiss", key=f"dm-{item}"):
            st.session_state.pending.remove(item)
            st.rerun()

    q = st.chat_input("Ask about anything you've seen…") or clicked
    if q:
        st.session_state.chat.append({"role": "user", "text": q})
        try:
            with st.spinner("Recalling…"):
                out = ask(q, st.session_state.events)
            st.session_state.chat.append({"role": "assistant", "text": out.get("answer", ""),
                                          "status": out.get("status", "unknown"),
                                          "meta": {"retrieved_from": out["source"],
                                                   "evidence_ids": out.get("evidence_ids", []),
                                                   "hydra_hits": out["hits"][:3]}})
            st.session_state.pending = list(dict.fromkeys(st.session_state.pending + out.get("propose_shopping", [])))
        except Exception as ex:
            st.session_state.chat.append({"role": "assistant", "text": f"⚠️ {ex}"})
        st.rerun()

# ---- SYSTEM
if NAV == "SYSTEM":
    st.markdown(("🟢" if hydra_ok else "🔴") + f" HydraDB · <span class='muted'>{hstatus}</span>", unsafe_allow_html=True)
    st.markdown(("🟢" if provider() else "🔴") + f" Model · <span class='muted'>{provider() or 'no key'}</span>",
                unsafe_allow_html=True)
    if st.button("↺ Reset demo"):
        st.session_state.events = [dict(e) for e in SEED]
        st.session_state.shopping, st.session_state.chat, st.session_state.pending = [], [], []
        persist()
        st.rerun()
    st.markdown("#### Observability")
    synced = sum(1 for e in st.session_state.events if e.get("hydra"))
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("HydraDB", "connected" if hydra_ok else "offline")
    m2.metric("Memories", f"{synced}/{len(st.session_state.events)}", help="written to HydraDB / total")
    m3.metric("Last retrieval", "—" if st.session_state.diag["last_query_hits"] is None
              else f"{st.session_state.diag['last_query_hits']} hits")
    m4.metric("Model", provider() or "none")
    st.caption(f"HydraDB status: {hstatus} · database `{DB}` · collection `{COLL}` · "
               f"fallback: {'local event log used when HydraDB returns nothing' if hydra_ok else 'ACTIVE — local event log'}")
    if st.session_state.diag["last_error"]:
        st.error(st.session_state.diag["last_error"])
    b1, b2, b3 = st.columns(3)
    if b1.button("Sync unsynced → HydraDB", disabled=not hydra_ok):
        todo = [e for e in st.session_state.events if not e.get("hydra")]
        if hydra_ingest(todo):
            st.success(f"Ingested {len(todo)}")
        else:
            st.warning("Nothing ingested")
    if b2.button("Check indexing", disabled=client is None):
        try:
            ids = [e["id"] for e in st.session_state.events if e.get("hydra")]
            s = client.context.status(database=DB, collection=COLL, ids=ids)
            states = [x.indexing_status for x in s.data.statuses]
            st.write({k: states.count(k) for k in set(states)})
        except Exception as ex:
            st.error(f"status: {ex}")
    if b3.button("Reconnect HydraDB"):
        hydra.clear()
        graph.clear()
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
