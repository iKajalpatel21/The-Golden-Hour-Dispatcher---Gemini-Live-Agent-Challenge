"""
Groq Whisper STT → Gemini ADK dispatcher → ElevenLabs TTS pipeline.

Replaces Gemini Live WebSocket for reliable voice interaction.

Flow per turn:
  1. Client sends {"type": "audio_blob", "data": "<base64>", "mime": "audio/webm"}
  2. Groq Whisper transcribes audio → text
  3. Gemini dispatcher agent processes text → structured result
  4. ElevenLabs synthesises spoken response → base64 MP3
  5. Server sends transcript + audio_elevenlabs + tool_result back to client
"""

import os
import base64
import json
import asyncio
from dotenv import load_dotenv

import httpx

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")


# ── Groq Whisper STT ──────────────────────────────────────────────────────────

async def _transcribe(audio_bytes: bytes, mime: str) -> str:
    """Transcribe audio bytes using Groq Whisper."""
    # Map MIME to file extension Groq accepts
    ext = "webm"
    if "wav" in mime:
        ext = "wav"
    elif "mp4" in mime or "m4a" in mime:
        ext = "mp4"
    elif "ogg" in mime:
        ext = "ogg"
    elif "mpeg" in mime or "mp3" in mime:
        ext = "mp3"

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": (f"audio.{ext}", audio_bytes, mime)},
            data={"model": "whisper-large-v3-turbo", "response_format": "text"},
        )
        r.raise_for_status()
        return r.text.strip()


# ── Response formatter ────────────────────────────────────────────────────────

def _spoken_response(result: dict) -> str:
    """Convert agent result dict to a short spoken sentence for TTS."""
    if not result:
        return "Emergency services have been notified. Stay calm and stay on the line."

    raw = result.get("raw_response")
    if raw and isinstance(raw, str):
        # Agent returned prose — use first 2 sentences
        sentences = raw.replace("\n", " ").split(".")
        return ". ".join(s.strip() for s in sentences[:2] if s.strip()) + "."

    parts = []
    eta = result.get("eta_minutes")
    hospital = result.get("recommended_hospital")
    instructions = result.get("first_aid_instructions", [])

    if eta:
        parts.append(f"Help is {eta} minutes away.")
    if hospital:
        parts.append(f"Routing to {hospital}.")
    if instructions:
        parts.append(instructions[0])

    return " ".join(parts) if parts else "Emergency services are on their way. Stay calm."


# ── Main WebSocket handler ────────────────────────────────────────────────────

async def handle_groq_session(websocket, session_id: str):
    """
    Handle one caller WebSocket session using Groq + Gemini + ElevenLabs.

    Client message:
      {"type": "audio_blob", "data": "<base64 webm>", "mime": "audio/webm"}

    Server messages:
      {"type": "status",           "text": "..."}
      {"type": "transcript",       "text": "You: ..." | "Dispatcher: ..."}
      {"type": "audio_elevenlabs", "data": "<base64 mp3>"}
      {"type": "tool_result",      "data": {...}}
      {"type": "error",            "text": "..."}
    """
    from root_agent import dispatcher_agent
    from voice_layer import synthesize_voice
    from google.adk.runners import InMemoryRunner
    from google.genai import types as genai_types

    # One runner per WebSocket connection — preserves multi-turn context
    runner = InMemoryRunner(agent=dispatcher_agent, app_name="golden-hour")
    session = await runner.session_service.create_session(
        app_name="golden-hour",
        user_id="caller",
        session_id=session_id,
    )
    print(f"[{session_id}] groq_session started")

    async def _send(msg: dict):
        await websocket.send_text(json.dumps(msg))

    try:
        async for raw in websocket.iter_text():
            msg = json.loads(raw)
            mtype = msg.get("type")

            if mtype == "text_query":
                # Direct text input — skip STT entirely
                transcript = msg.get("text", "").strip()
                if not transcript:
                    continue
                print(f"[{session_id}] Text query: {transcript[:100]}")
                await _send({"type": "transcript", "text": f"Caller: {transcript}"})

            elif mtype == "audio_blob":
                # Groq Whisper STT
                audio_bytes = base64.b64decode(msg["data"])
                mime = msg.get("mime", "audio/webm;codecs=opus")
                await _send({"type": "status", "text": "Listening…"})
                try:
                    transcript = await _transcribe(audio_bytes, mime)
                except Exception as e:
                    print(f"[{session_id}] STT error: {e}")
                    await _send({"type": "error", "text": f"Could not transcribe audio: {e}"})
                    continue
                if not transcript or len(transcript.strip()) < 3:
                    await _send({"type": "status", "text": "Connected — speak now"})
                    continue
                print(f"[{session_id}] STT: {transcript[:100]}")
                await _send({"type": "transcript", "text": f"Caller: {transcript}"})
            else:
                continue
            await _send({"type": "status", "text": "Dispatching…"})

            # ── 2. Gemini dispatcher agent ───────────────────────────────────
            result = {}
            try:
                events = runner.run_async(
                    user_id="caller",
                    session_id=session.id,
                    new_message=genai_types.Content(
                        role="user",
                        parts=[genai_types.Part(text=transcript)],
                    ),
                )
                async for event in events:
                    if event.is_final_response() and event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text:
                                try:
                                    result = json.loads(part.text)
                                except json.JSONDecodeError:
                                    result = {"raw_response": part.text}
            except Exception as e:
                print(f"[{session_id}] agent error: {e}")
                result = {"raw_response": "Emergency services have been notified. Help is on the way."}

            await _send({"type": "tool_result", "data": result})

            # ── 3. Build spoken response ─────────────────────────────────────
            spoken = _spoken_response(result)
            print(f"[{session_id}] TTS: {spoken[:100]}")
            await _send({"type": "transcript", "text": f"Dispatcher: {spoken}"})

            # ── 4. ElevenLabs TTS ────────────────────────────────────────────
            await _send({"type": "status", "text": "Speaking…"})
            try:
                audio_b64 = await synthesize_voice(spoken)
                if audio_b64:
                    await _send({"type": "audio_elevenlabs", "data": audio_b64})
            except Exception as e:
                print(f"[{session_id}] TTS error: {e}")

            eta = result.get("eta_minutes")
            status = f"Help is {eta} min away — stay on the line" if eta else "Connected — speak now"
            await _send({"type": "status", "text": status})

    except Exception as exc:
        print(f"[{session_id}] groq_session error: {exc}")
        try:
            await _send({"type": "error", "text": str(exc)})
        except Exception:
            pass
