"""ElevenLabs TTS / STT with text-only fallback."""
import base64

import httpx

from .config import env, warn_once

DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"


async def tts(text: str) -> str | None:
    key = env("ELEVENLABS_API_KEY")
    if not key:
        warn_once("el_tts", "ELEVENLABS_API_KEY not set; phone will use browser speechSynthesis")
        return None
    try:
        vid = env("ELEVENLABS_VOICE_ID", DEFAULT_VOICE)
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
                headers={"xi-api-key": key, "accept": "audio/mpeg"},
                json={"text": text[:1500], "model_id": "eleven_flash_v2_5"},
            )
            r.raise_for_status()
            return base64.b64encode(r.content).decode()
    except Exception as e:  # noqa: BLE001
        warn_once("el_tts_err", f"ElevenLabs TTS failed: {e}")
        return None


async def stt(audio_b64: str, mime: str = "audio/webm") -> str | None:
    key = env("ELEVENLABS_API_KEY")
    if not key:
        warn_once("el_stt", "ELEVENLABS_API_KEY not set; audio messages cannot be transcribed (send text)")
        return None
    try:
        raw = base64.b64decode(audio_b64)
        ext = "mp4" if "mp4" in mime else ("wav" if "wav" in mime else "webm")
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers={"xi-api-key": key},
                data={"model_id": "scribe_v1"},
                files={"file": (f"utt.{ext}", raw, mime)},
            )
            r.raise_for_status()
            return (r.json().get("text") or "").strip() or None
    except Exception as e:  # noqa: BLE001
        warn_once("el_stt_err", f"ElevenLabs STT failed: {e}")
        return None
