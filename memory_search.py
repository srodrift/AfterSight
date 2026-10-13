"""
Pure retrieval + change-detection logic for AFTERSIGHT (no Streamlit, no network) — unit-testable.

search_events(question, events)   bounded keyword retrieval over stored captions, objects,
                                  places and timestamps, for questions that don't name a known object.
meaningful_change(objs, place, state)  Auto Memory: decide whether a new observation is worth storing.
"""
import re
from datetime import datetime

STOP = set("""a an the i me my mine we our you your it its is are was were be been am do did does done
have has had what which who whom where when why how that this those these there here of in on at to for
from with by about into over after before any some anything something did see saw seen show tell
last latest recent recently just ever please can could would should will shall let lets get got
thing things object objects memory memories remember remembered""".split())

TIME_WINDOWS = {
    "morning": (5, 12), "noon": (11, 14), "afternoon": (12, 17), "evening": (17, 23),
    "tonight": (17, 24), "night": (19, 24),
}
RECENT_WORDS = {"last", "latest", "recent", "recently", "just", "newest", "most"}


def _stem(w):
    w = w.lower()
    for suf in ("ies", "es", "s"):
        if w.endswith(suf) and len(w) > len(suf) + 2:
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def terms(question):
    words = re.findall(r"[a-zA-Z]+", question.lower())
    return [_stem(w) for w in words if w not in STOP and len(w) > 2]


def event_text(e):
    parts = [e.get("caption", ""), e.get("place", "")]
    for o in e.get("objects", []):
        parts.append(str(o.get("name", "")))
        if o.get("attributes"):
            parts.append(str(o["attributes"]))
    return " ".join(parts).lower()


def _hour(e):
    try:
        return datetime.fromisoformat(e["ts"]).hour
    except Exception:
        return None


def search_events(question, events, k=8):
    """Return up to k events relevant to the question, best first.
    Scores = matched query terms in caption/place/objects (+ time-of-day and recency intents).
    Falls back to the most recent memories when nothing matches."""
    q = question.lower()
    ts = terms(question)
    window = next((rng for word, rng in TIME_WINDOWS.items() if word in q), None)
    wants_recent = bool(RECENT_WORDS & set(re.findall(r"[a-z]+", q)))
    ordered = sorted(events, key=lambda e: e.get("ts", ""))
    rank = {e["id"]: i for i, e in enumerate(ordered)}
    scored = []
    for e in ordered:
        if e.get("place") == "AFTERSIGHT agent":
            continue
        words = {_stem(w) for w in re.findall(r"[a-z]+", event_text(e))}
        score = sum(1.0 for t in ts if t in words)
        score += sum(0.5 for t in ts if t not in words and any(w.startswith(t) for w in words if len(t) > 3))
        h = _hour(e)
        if window and h is not None and window[0] <= h < window[1]:
            score += 1.0
        if wants_recent:
            score += rank[e["id"]] / max(1, len(ordered)) * 1.5
        if e.get("image") and any(t in ("hold", "holding", "photo", "camera", "captur", "picture") for t in ts):
            score += 0.5
        if score > 0:
            scored.append((score, rank[e["id"]], e))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    hits = [e for _, _, e in scored[:k]]
    if not hits:
        hits = [e for e in reversed(ordered) if e.get("place") != "AFTERSIGHT agent"][: min(k, 6)]
    return hits


def _norm_place(p):
    return re.sub(r"[^a-z ]", "", (p or "").lower()).strip()


def meaningful_change(objs, place, state):
    """Compare a freshly observed object list against the latest known state.
    Returns a list of human-readable reasons; empty list = nothing new worth storing."""
    reasons = []
    p_new = _norm_place(place)
    for o in objs:
        name = str(o.get("name", "")).lower().strip()
        if not name:
            continue
        c = o.get("count")
        s = state.get(name)
        if s is None or s.get("count") is None and s.get("place") is None:
            reasons.append(f"new: {name}")
            continue
        if isinstance(c, int) and s.get("count") is not None and c != s["count"]:
            reasons.append(f"{name}: {s['count']} → {c}")
            continue
        if isinstance(c, int) and s.get("count") is None:
            reasons.append(f"{name}: now counted ({c})")
            continue
        p_old = _norm_place(s.get("place"))
        if p_new and p_old and p_new not in p_old and p_old not in p_new:
            reasons.append(f"{name} moved: {s.get('place')} → {place}")
    return reasons
