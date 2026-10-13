"""Test double for the openai SDK surface AFTERSIGHT uses. Logs every call to $STUB_LOG."""
import json, os, re, types
LOG = os.environ.get("STUB_LOG", "stub_calls.log")
def _log(kind, **kw):
    with open(LOG, "a") as f: f.write(json.dumps({"kind": kind, **kw}) + "\n")
def _msg(obj):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps(obj)))])
class _Chat:
    def create(self, model, messages):
        sysm = messages[0]["content"]; user = messages[1]["content"]
        text = next(c["text"] for c in user if c["type"] == "text")
        n_img = sum(1 for c in user if c["type"] == "image_url")
        if "vision system" in sysm:
            nv = sum(1 for l in open(LOG) if '"vision"' in l) if os.path.exists(LOG) else 0
            _log("vision", images=n_img)
            eggs = 4 if nv < 2 else 3
            return _msg({"caption": "A person holding a yellow ceramic mug in their right hand at a wooden desk",
                         "scene": "Home office, daylight", "place_guess": "office desk",
                         "objects": [{"name": "mug", "count": 1, "attributes": "yellow, ceramic", "position": "held in right hand"},
                                     {"name": "eggs", "count": eggs, "attributes": "white, in carton", "position": "on the desk"}],
                         "interactions": ["holding a yellow mug in the right hand"], "details": ["laptop screen shows a calendar"]})
        if "re-examining" in sysm:
            ids = re.findall(r"memory (live-[0-9a-f]+|seed-\d+)", text)
            _log("look", images=n_img, ids=ids)
            return _msg({"answer": "In the photo from earlier, the object in your right hand is a yellow mug.",
                         "status": "observed", "evidence_ids": ids[:1]})
        q = text.split("QUESTION:")[-1].lower()
        photo_ids = re.findall(r"\[photo retained\] \(memory id ([\w-]+)\)", text)
        ev_ids = re.findall(r"\(memory id ([\w-]+)\)", text)
        _log("agent", q=q.strip(), photos=photo_ids, evidence=ev_ids[:5])
        if "color" in q and photo_ids:
            return _msg({"answer": "The note doesn't record the color.", "status": "unknown", "propose_shopping": [],
                         "evidence_ids": [], "look_again": photo_ids[:1]})
        prop = [{"item": "eggs", "reason": "Last observation showed 2 eggs at 6:05 PM."}] if ("buy" in q or "egg" in q) else []
        return _msg({"answer": f"Stub answer grounded in {ev_ids[0] if ev_ids else 'nothing'}.", "status": "observed",
                     "propose_shopping": prop, "evidence_ids": ev_ids[:2], "look_again": []})
class _Trans:
    def create(self, model, file):
        _log("stt", model=model, name=file[0], bytes=len(file[1]), mime=file[2])
        return types.SimpleNamespace(text="What color was the object I was holding?")
class _Speech:
    def create(self, model, voice, input, response_format):
        _log("tts", model=model, voice=voice, chars=len(input))
        return types.SimpleNamespace(read=lambda: b"ID3\x03\x00fake-mp3")
class OpenAI:
    def __init__(self, *a, **k):
        self.chat = types.SimpleNamespace(completions=_Chat())
        self.audio = types.SimpleNamespace(transcriptions=_Trans(), speech=_Speech())
