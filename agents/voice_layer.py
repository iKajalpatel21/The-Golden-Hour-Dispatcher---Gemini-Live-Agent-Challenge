"""
ElevenLabs voice layer via ADK McpToolset.

ElevenLabs is VOICE OUTPUT ONLY — Gemini handles all reasoning.

VOICE_MODE env var:
  "elevenlabs" → ElevenLabs HD voice (default)
  "gemini"     → Gemini Live native audio (demo fallback)

Test:
    cd agents
    python -c "
import asyncio, base64
from voice_layer import synthesize_voice

audio_b64 = asyncio.run(synthesize_voice('Help is on the way. Stay calm.'))
audio_bytes = base64.b64decode(audio_b64)
print(f'Got {len(audio_bytes)} bytes of audio')
"
"""

import os
import base64
import asyncio
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")  # default: Bella
_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
_VOICE_MODE = os.getenv("VOICE_MODE", "elevenlabs")


# ── McpToolset initialisation ──────────────────────────────────────────────────
# McpToolset connects to the ElevenLabs MCP server via stdio.
# Agents that need voice tools import `get_voice_toolset()`.

def get_voice_toolset():
    """
    Return an ADK McpToolset wired to the ElevenLabs MCP server.

    Usage inside an Agent definition:
        from voice_layer import get_voice_toolset
        agent = Agent(..., tools=[..., *get_voice_toolset().tools])

    The MCP server is the official elevenlabs-mcp package:
        pip install elevenlabs-mcp
        npx @elevenlabs/mcp  (or python -m elevenlabs_mcp)
    """
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, StdioServerParameters

    return McpToolset(
        connection_params=StdioServerParameters(
            command="python",
            args=["-m", "elevenlabs_mcp"],
            env={
                "ELEVENLABS_API_KEY": _API_KEY,
                "ELEVENLABS_VOICE_ID": _VOICE_ID,
            },
        )
    )


# ── Direct ElevenLabs HTTP tools (used when MCP server is unavailable) ─────────

async def text_to_speech(
    text: str,
    voice_id: Optional[str] = None,
    stability: float = 0.4,
    style: float = 0.6,
) -> bytes:
    """
    Convert text to speech via ElevenLabs REST API.

    Args:
        text:       The text to synthesise.
        voice_id:   ElevenLabs voice ID (defaults to ELEVENLABS_VOICE_ID env var).
        stability:  Voice stability 0-1 (0.4 = more expressive for urgency).
        style:      Style exaggeration 0-1 (0.6 = slight dramatic weight).

    Returns:
        Raw MP3 audio bytes.
    """
    if _VOICE_MODE == "gemini":
        # Fallback: caller should use Gemini native audio
        raise NotImplementedError("VOICE_MODE=gemini — skip ElevenLabs")

    vid = voice_id or _VOICE_ID

    import httpx

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
    headers = {
        "xi-api-key": _API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {
            "stability": stability,
            "similarity_boost": 0.75,
            "style": style,
            "use_speaker_boost": True,
        },
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.content


async def speech_to_speech(
    audio_bytes: bytes,
    voice_id: Optional[str] = None,
) -> bytes:
    """
    Voice conversion via ElevenLabs speech-to-speech endpoint.

    Args:
        audio_bytes: Input audio (PCM or MP3).
        voice_id:    Target voice ID.

    Returns:
        Converted MP3 audio bytes.
    """
    if _VOICE_MODE == "gemini":
        raise NotImplementedError("VOICE_MODE=gemini — skip ElevenLabs")

    vid = voice_id or _VOICE_ID

    import httpx

    url = f"https://api.elevenlabs.io/v1/speech-to-speech/{vid}"
    headers = {"xi-api-key": _API_KEY}

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            url,
            headers=headers,
            files={"audio": ("input.mp3", audio_bytes, "audio/mpeg")},
            data={"model_id": "eleven_turbo_v2_5"},
        )
        resp.raise_for_status()
        return resp.content


# ── Convenience wrapper used by parallel_agent.py ─────────────────────────────

async def synthesize_voice(
    text: str,
    voice_id: Optional[str] = None,
    stability: float = 0.4,
    style: float = 0.6,
) -> str:
    """
    Synthesise voice and return base64-encoded audio string.
    Falls back gracefully if ElevenLabs is unavailable.
    """
    try:
        audio_bytes = await text_to_speech(text, voice_id, stability, style)
        return base64.b64encode(audio_bytes).decode("utf-8")
    except NotImplementedError:
        return ""  # Gemini mode — no bytes needed
    except Exception as exc:
        print(f"[WARN] ElevenLabs TTS failed: {exc}")
        return ""


# ── Standalone demo ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    test_text = (
        "Help is seven minutes away. "
        "Apply firm pressure to the wound. "
        "Do not move the patient. "
        "You are doing great — stay on the line."
    )

    print(f"VOICE_MODE = {_VOICE_MODE}")

    if _VOICE_MODE == "gemini":
        print("Gemini mode active — no ElevenLabs call made.")
        sys.exit(0)

    audio_b64 = asyncio.run(synthesize_voice(test_text))
    if audio_b64:
        audio_bytes = base64.b64decode(audio_b64)
        out = "/tmp/golden_hour_test.mp3"
        with open(out, "wb") as f:
            f.write(audio_bytes)
        print(f"Wrote {len(audio_bytes):,} bytes → {out}")
        print("Play with: afplay /tmp/golden_hour_test.mp3  (macOS)")
    else:
        print("No audio returned.")
