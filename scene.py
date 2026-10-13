"""Renders a simulated smart-glasses camera frame (SVG) for one memory event:
a drawn scene + object-detection boxes + HUD. No real camera needed."""
import html

W, H = 960, 520
CY = "#7CFFCB"


def _bg(place):
    p = place.lower()
    if "fridge" in p:
        return ("fridge", '<rect width="960" height="520" fill="#dfe8ec"/>'
                '<rect x="70" y="30" width="820" height="470" rx="18" fill="#f4f8fa" stroke="#b9c7cf" stroke-width="6"/>'
                '<rect x="90" y="190" width="780" height="10" fill="#c9d6dd"/><rect x="90" y="350" width="780" height="10" fill="#c9d6dd"/>'
                '<rect x="70" y="30" width="820" height="470" rx="18" fill="url(#cold)"/>')
    if "stove" in p or "pan" in p:
        return ("stove", '<rect width="960" height="520" fill="#2a2522"/><rect y="300" width="960" height="220" fill="#3b3430"/>'
                '<circle cx="480" cy="360" r="150" fill="#1b1b1b" stroke="#555" stroke-width="10"/>'
                '<rect x="620" y="340" width="260" height="34" rx="16" fill="#1b1b1b"/>')
    if "store" in p or "checkout" in p:
        return ("store", '<rect width="960" height="520" fill="#eef0f2"/>'
                + "".join(f'<rect x="{x}" y="40" width="150" height="230" fill="#d6dde3"/>' for x in (40, 220, 400, 580, 760))
                + '<rect y="330" width="960" height="190" fill="#9aa5ad"/><rect y="320" width="960" height="14" fill="#6b757c"/>')
    if "garage" in p or "car" in p or "park" in p:
        return ("garage", '<rect width="960" height="520" fill="#4a4f55"/><rect y="380" width="960" height="140" fill="#3a3e42"/>'
                '<rect x="60" y="60" width="90" height="320" fill="#6e747a"/><text x="105" y="200" fill="#ffd34d" font-size="64" '
                'font-family="Arial" font-weight="bold" text-anchor="middle">3C</text>'
                '<line x1="0" y1="470" x2="960" y2="470" stroke="#e8e8e8" stroke-width="6" stroke-dasharray="60 40"/>')
    if "office" in p or "desk" in p:
        return ("desk", '<rect width="960" height="520" fill="#cfd5da"/><rect y="300" width="960" height="220" fill="#8a6b4f"/>'
                '<rect x="700" y="40" width="200" height="140" fill="#e6ebef" stroke="#b5bec5" stroke-width="4"/>')
    if "agent" in p:
        return ("agent", '<rect width="960" height="520" fill="#0d1418"/>')
    # counter / table / hallway / pocket
    return ("table", '<rect width="960" height="520" fill="#d9d2c7"/><rect y="280" width="960" height="240" fill="#a07a55"/>'
            '<rect y="280" width="960" height="8" fill="#7d5d40"/>')


def _box(x, y, w, h, label):
    t = html.escape(label)
    tw = max(90, 11 * len(label) + 20)
    return (f'<g class="det"><rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="{CY}" stroke-width="3" rx="6"/>'
            f'<rect x="{x}" y="{y - 30}" width="{tw}" height="26" rx="5" fill="{CY}"/>'
            f'<text x="{x + 10}" y="{y - 11}" font-size="17" font-family="Menlo,monospace" fill="#04140e" font-weight="bold">{t}</text></g>')


def _draw(name, count, slot, kind, event):
    """Return (svg, label) for one object at slot (x, y)."""
    x, y = slot
    n = name.lower()
    c = 1 if count is None else count
    lbl = f"{name}{'' if count is None else f' ×{count}'}"
    if event in ("used",):
        lbl = f"{name} −{c} (used)"
    elif event == "purchased":
        lbl = f"{name} ×{c} purchased"
    elif event == "added":
        lbl = f"{name} +{c} added"

    if "egg" in n:
        if event == "used":  # cracked eggs in a pan
            s = "".join(f'<ellipse cx="{410 + i * 70}" cy="360" rx="44" ry="36" fill="#fffaf0"/>'
                        f'<circle cx="{410 + i * 70}" cy="360" r="17" fill="#ffb800"/>' for i in range(min(c, 3)))
            return s + _box(350, 300, 80 + 70 * min(c, 3), 120, lbl), lbl
        slots = 12
        s = f'<rect x="{x}" y="{y}" width="330" height="110" rx="10" fill="#c8b48e" stroke="#9e8a66" stroke-width="3"/>'
        for i in range(slots):
            cx, cy = x + 32 + (i % 6) * 53, y + 30 + (i // 6) * 50
            s += f'<circle cx="{cx}" cy="{cy}" r="21" fill="#a8946f"/>'
            if i < c:
                s += f'<ellipse cx="{cx}" cy="{cy - 3}" rx="19" ry="23" fill="#fbf3e4" stroke="#e2d4ba" stroke-width="2"/>'
        return s + _box(x - 8, y - 8, 346, 126, lbl), lbl
    if "milk" in n:
        s = (f'<rect x="{x}" y="{y}" width="90" height="140" rx="6" fill="#ffffff" stroke="#9ab" stroke-width="3"/>'
             f'<polygon points="{x},{y} {x + 45},{y - 40} {x + 90},{y}" fill="#e9f1f6" stroke="#9ab" stroke-width="3"/>'
             f'<rect x="{x + 10}" y="{y + 40}" width="70" height="40" fill="#3d7bd9"/>'
             f'<text x="{x + 45}" y="{y + 67}" fill="#fff" font-size="20" font-family="Arial" text-anchor="middle">MILK</text>')
        return s + _box(x - 10, y - 50, 110, 200, lbl), lbl
    if "apple" in n:
        s = "".join(f'<circle cx="{x + 35 + i * 62}" cy="{y + 40}" r="30" fill="#d3263a"/>'
                    f'<rect x="{x + 33 + i * 62}" y="{y + 2}" width="4" height="14" fill="#5a3b1d"/>' for i in range(min(c, 5)))
        return s + _box(x - 6, y - 6, 20 + 62 * max(1, min(c, 5)), 92, lbl), lbl
    if "spinach" in n:
        s = f'<ellipse cx="{x + 70}" cy="{y + 40}" rx="70" ry="40" fill="#2f8f3a"/><ellipse cx="{x + 50}" cy="{y + 30}" rx="35" ry="22" fill="#3fae4b"/>'
        return s + _box(x - 6, y - 6, 152, 92, lbl), lbl
    if "key" in n:
        s = (f'<circle cx="{x + 30}" cy="{y + 30}" r="22" fill="none" stroke="#c9a227" stroke-width="7"/>'
             f'<rect x="{x + 50}" y="{y + 24}" width="90" height="12" rx="3" fill="#b8bec4"/>'
             f'<rect x="{x + 115}" y="{y + 36}" width="8" height="16" fill="#b8bec4"/><rect x="{x + 98}" y="{y + 36}" width="8" height="12" fill="#b8bec4"/>')
        return s + _box(x - 8, y - 8, 160, 76, lbl), lbl
    if "medication" in n or "pill" in n:
        s = (f'<rect x="{x}" y="{y + 20}" width="70" height="110" rx="10" fill="#e8822a" opacity=".9"/>'
             f'<rect x="{x - 4}" y="{y}" width="78" height="28" rx="6" fill="#fff"/>'
             f'<rect x="{x + 10}" y="{y + 55}" width="50" height="40" fill="#fff"/>')
        return s + _box(x - 12, y - 12, 94, 154, lbl), lbl
    if "car" in n:
        s = (f'<rect x="{x}" y="{y + 40}" width="420" height="110" rx="40" fill="#2b5fa8"/>'
             f'<path d="M{x + 80},{y + 40} L{x + 130},{y - 20} L{x + 300},{y - 20} L{x + 350},{y + 40} Z" fill="#244f8c"/>'
             f'<rect x="{x + 145}" y="{y - 8}" width="65" height="44" fill="#9cc3e6"/><rect x="{x + 220}" y="{y - 8}" width="65" height="44" fill="#9cc3e6"/>'
             f'<circle cx="{x + 90}" cy="{y + 150}" r="38" fill="#111"/><circle cx="{x + 330}" cy="{y + 150}" r="38" fill="#111"/>')
        return s + _box(x - 10, y - 30, 440, 230, lbl), lbl
    if "laptop" in n and "charger" not in n:
        s = (f'<rect x="{x + 20}" y="{y}" width="260" height="160" rx="8" fill="#2d3238"/>'
             f'<rect x="{x + 32}" y="{y + 12}" width="236" height="136" fill="#4b8bd6"/>'
             f'<polygon points="{x},{y + 170} {x + 300},{y + 170} {x + 280},{y + 160} {x + 20},{y + 160}" fill="#9aa2aa"/>')
        return s + _box(x - 10, y - 10, 320, 192, lbl), lbl
    if "charger" in n:
        s = (f'<rect x="{x}" y="{y + 20}" width="60" height="40" rx="6" fill="#eee" stroke="#aaa" stroke-width="2"/>'
             f'<path d="M{x + 60},{y + 40} C{x + 120},{y + 40} {x + 120},{y + 100} {x + 180},{y + 100}" stroke="#eee" stroke-width="6" fill="none"/>')
        return s + _box(x - 8, y + 8, 200, 104, lbl), lbl
    s = f'<rect x="{x}" y="{y}" width="120" height="90" rx="10" fill="#8aa"/>'
    return s + _box(x - 8, y - 8, 136, 106, lbl), lbl


SLOTS = {
    "fridge": [(130, 240), (560, 90), (130, 60), (500, 260), (700, 410)],
    "stove": [(400, 300)],
    "store": [(315, 300), (60, 300), (620, 300)],
    "garage": [(270, 230)],
    "desk": [(200, 140), (560, 210)],
    "table": [(300, 330), (560, 330), (120, 330)],
    "agent": [(380, 220)],
}


def render(event, fmt_ts, saved_to_hydra, live=True):
    kind, bg = _bg(event.get("place", ""))
    slots = SLOTS.get(kind, SLOTS["table"])
    body = []
    objs = event.get("objects", [])
    for i, o in enumerate(objs):
        name = o.get("name", "object")
        n = name.lower()
        slot = slots[i % len(slots)]
        if kind == "fridge":  # fixed positions per item for a believable fridge
            slot = {"eggs": (130, 240), "milk": (660, 200), "apples": (130, 112), "spinach": (420, 112)}.get(n, slot)
        svg, _ = _draw(name, o.get("count"), slot, kind, o.get("event", "observed"))
        body.append(svg)
    if kind == "agent":
        body.append(f'<text x="480" y="270" fill="{CY}" font-size="30" font-family="Menlo,monospace" text-anchor="middle">'
                    f'{html.escape(event.get("caption", ""))[:60]}</text>')

    # HUD overlay
    br = "".join(f'<path d="{d}" stroke="{CY}" stroke-width="4" fill="none" opacity=".8"/>' for d in (
        "M30,80 L30,30 L80,30", "M880,30 L930,30 L930,80", "M30,440 L30,490 L80,490", "M880,490 L930,490 L930,440"))
    rec = (f'<circle cx="62" cy="62" r="9" fill="#ff3b3b"><animate attributeName="opacity" values="1;.2;1" dur="1.4s" repeatCount="indefinite"/></circle>'
           if live else f'<text x="52" y="69" fill="#ffd34d" font-size="18" font-family="Menlo,monospace">⏪</text>')
    mode = "REC" if live else "REWIND"
    hud = (f'{br}{rec}<text x="80" y="68" fill="#fff" font-size="17" font-family="Menlo,monospace" font-weight="bold">'
           f'{mode} · {html.escape(fmt_ts(event["ts"]).upper())}</text>'
           f'<text x="900" y="68" fill="#fff" font-size="15" font-family="Menlo,monospace" text-anchor="end">'
           f'📍 {html.escape(event.get("place", "").upper())[:42]}</text>'
           f'<rect x="0" y="444" width="960" height="76" fill="rgba(0,0,0,.55)"/>'
           f'<text x="40" y="478" fill="#fff" font-size="19" font-family="Menlo,monospace">{html.escape(event.get("caption", ""))[:78]}</text>'
           f'<text x="40" y="505" fill="{CY}" font-size="14" font-family="Menlo,monospace">'
           f'◉ {len(objs)} OBJECTS DETECTED · MEMORY {html.escape(event["id"])} · '
           f'{"SAVED TO HYDRADB ✓" if saved_to_hydra else "SAVED LOCALLY"}</text>')
    svg = (f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;display:block">'
           f'<defs><radialGradient id="vig" cx="50%" cy="50%" r="70%"><stop offset="60%" stop-color="#000" stop-opacity="0"/>'
           f'<stop offset="100%" stop-color="#000" stop-opacity=".55"/></radialGradient>'
           f'<linearGradient id="cold" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#bfe6ff" stop-opacity=".25"/>'
           f'<stop offset="1" stop-color="#bfe6ff" stop-opacity="0"/></linearGradient></defs>'
           f'{bg}{"".join(body)}<rect width="{W}" height="{H}" fill="url(#vig)"/>{hud}</svg>')
    # glasses frame around the "lens"
    return (f'<div style="background:#0b0b0c;padding:26px 30px 34px;border-radius:46px;'
            f'box-shadow:0 18px 50px rgba(0,0,0,.45), inset 0 0 0 3px #222;">'
            f'<div style="border-radius:34px 34px 120px 120px / 34px 34px 70px 70px;overflow:hidden;'
            f'box-shadow:inset 0 0 40px rgba(0,0,0,.6);border:3px solid #1d1d1f">{svg}</div>'
            f'<div style="text-align:center;color:#666;font:12px Menlo,monospace;margin-top:10px;letter-spacing:.3em">'
            f'AFTERSIGHT · SIMULATED SMART-GLASSES CAMERA</div></div>')


def render_photo(event, b64, fmt_ts, saved_to_hydra, live=True):
    """A real captured photo inside the glasses frame, with the HUD and the objects the vision model found."""
    tags = "".join(
        f"<span style='display:inline-block;border:1px solid {CY};color:{CY};border-radius:6px;padding:2px 8px;"
        f"margin:3px;font:13px Menlo,monospace;background:rgba(0,0,0,.45)'>{html.escape(str(o.get('name','')))}"
        f"{'' if o.get('count') is None else ' ×' + html.escape(str(o.get('count')))}</span>"
        for o in event.get("objects", []))
    mode = "REC" if live else "REWIND"
    dot = ("<span style='display:inline-block;width:12px;height:12px;border-radius:50%;background:#ff3b3b;"
           "margin-right:8px;animation:b 1.4s infinite'></span>" if live else "⏪ ")
    hud = (f"<style>@keyframes b{{50%{{opacity:.2}}}}</style>"
           f"<div style='position:absolute;top:18px;left:22px;color:#fff;font:bold 15px Menlo,monospace;"
           f"text-shadow:0 1px 3px #000'>{dot}{mode} · {html.escape(fmt_ts(event['ts']).upper())} · CAMERA ON</div>"
           f"<div style='position:absolute;top:18px;right:22px;color:#fff;font:14px Menlo,monospace;"
           f"text-shadow:0 1px 3px #000'>📍 {html.escape(event.get('place','').upper())[:40]}</div>"
           f"<div style='position:absolute;left:0;right:0;bottom:0;padding:12px 20px;background:rgba(0,0,0,.6)'>"
           f"<div style='color:#fff;font:16px Menlo,monospace;margin-bottom:6px'>{html.escape(event.get('caption',''))[:110]}</div>"
           f"{tags}<div style='color:{CY};font:12px Menlo,monospace;margin-top:6px'>◉ {len(event.get('objects', []))} "
           f"OBJECTS UNDERSTOOD · MEMORY {html.escape(event['id'])} · "
           f"{'SAVED TO HYDRADB ✓' if saved_to_hydra else 'SAVED LOCALLY'}</div></div>")
    inner = (f"<div style='position:relative;aspect-ratio:960/520;background:#000'>"
             f"<img src='data:image/jpeg;base64,{b64}' style='width:100%;height:100%;object-fit:cover;display:block'>"
             f"<div style='position:absolute;inset:0;box-shadow:inset 0 0 120px rgba(0,0,0,.65)'></div>{hud}</div>")
    return (f'<div style="background:#0b0b0c;padding:26px 30px 34px;border-radius:46px;'
            f'box-shadow:0 18px 50px rgba(0,0,0,.45), inset 0 0 0 3px #222;">'
            f'<div style="border-radius:34px 34px 120px 120px / 34px 34px 70px 70px;overflow:hidden;'
            f'border:3px solid #1d1d1f">{inner}</div>'
            f'<div style="text-align:center;color:#666;font:12px Menlo,monospace;margin-top:10px;letter-spacing:.3em">'
            f'AFTERSIGHT · LIVE CAPTURE</div></div>')
