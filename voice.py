"""Speech in/out for AFTERSIGHT via OpenAI (needs OPENAI_API_KEY, independent of the reasoning model).

transcribe(audio_bytes, mime) -> text      OpenAI speech-to-text
speak(text) -> mp3 bytes                    OpenAI text-to-speech
Model/voice are configurable: OPENAI_STT_MODEL, OPENAI_TTS_MODEL, OPENAI_TTS_VOICE.
"""
import os


def available():
    return bool(os.getenv("OPENAI_API_KEY"))


def _ext(mime):
    m = (mime or "").lower()
    for key, ext in (("webm", "webm"), ("mp4", "mp4"), ("m4a", "m4a"), ("mpeg", "mp3"), ("ogg", "ogg"), ("wav", "wav")):
        if key in m:
            return ext
    return "webm"


def transcribe(audio_bytes, mime="audio/webm"):
    from openai import OpenAI
    r = OpenAI().audio.transcriptions.create(
        model=os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe"),
        file=(f"speech.{_ext(mime)}", audio_bytes, (mime or "audio/webm").split(";")[0]),
    )
    return (getattr(r, "text", None) or "").strip()


def speak(text):
    from openai import OpenAI
    r = OpenAI().audio.speech.create(
        model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
        voice=os.getenv("OPENAI_TTS_VOICE", "alloy"),
        input=text[:3500],
        response_format="mp3",
    )
    data = r.read() if hasattr(r, "read") else getattr(r, "content", b"")
    return data
