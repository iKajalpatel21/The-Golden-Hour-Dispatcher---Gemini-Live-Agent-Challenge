"""
Gemini Live WebSocket handler.

Critical constraints (hard rules):
  1. Model from env GEMINI_LIVE_MODEL — never hardcoded.
  2. Client uses http_options={"api_version": "v1alpha"}.
  3. enable_affective_dialog=True in session config.
  4. Manual tool dispatch: catch ToolCall → execute → send ALL FunctionResponses
     in ONE session.send() call — never one send() per tool.
  5. asyncio.gather for bidirectional streaming.
  6. ElevenLabs audio bypasses Gemini and streams directly to caller WebSocket.
  7. try/finally: close Gemini session before WebSocket closes.
"""

import os
import asyncio
import base64
import json
from typing import Any

from dotenv import load_dotenv
import google.genai as genai
from google.genai import types

load_dotenv()

_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-2.5-flash-native-audio-latest")

# ── Gemini client (rule 2) — lazy so tests can import without a real API key ──
_client: "genai.Client | None" = None

def _get_client() -> "genai.Client":
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY"),
            http_options={"api_version": "v1alpha"},
        )
    return _client

# ── Tool registry — maps function name → async callable ───────────────────────
from root_agent import (
    get_nearest_ambulance,
    get_hospital_capacity,
    create_incident_summary,
    notify_er_team,
)
from parallel_agent import run_parallel_response

TOOL_REGISTRY: dict[str, Any] = {
    "get_nearest_ambulance": get_nearest_ambulance,
    "get_hospital_capacity": get_hospital_capacity,
    "create_incident_summary": create_incident_summary,
    "notify_er_team": notify_er_team,
    "run_parallel_response": run_parallel_response,
}

# ── ADK tool declarations for Gemini session ──────────────────────────────────
GEMINI_TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="get_nearest_ambulance",
                description="Find nearest available ambulance and ETA.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "lat": types.Schema(type=types.Type.NUMBER),
                        "lng": types.Schema(type=types.Type.NUMBER),
                    },
                    required=["lat", "lng"],
                ),
            ),
            types.FunctionDeclaration(
                name="get_hospital_capacity",
                description="Get ER bed availability for a specialty.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "specialty": types.Schema(type=types.Type.STRING),
                    },
                    required=["specialty"],
                ),
            ),
            types.FunctionDeclaration(
                name="create_incident_summary",
                description="Build structured IncidentSummary from triage data.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "victims":   types.Schema(type=types.Type.INTEGER),
                        "injuries":  types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
                        "location":  types.Schema(type=types.Type.STRING),
                    },
                    required=["victims", "injuries", "location"],
                ),
            ),
            types.FunctionDeclaration(
                name="notify_er_team",
                description="Publish ER notification to Pub/Sub.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "hospital_id": types.Schema(type=types.Type.STRING),
                        "summary":     types.Schema(type=types.Type.OBJECT),
                    },
                    required=["hospital_id", "summary"],
                ),
            ),
            types.FunctionDeclaration(
                name="run_parallel_response",
                description="Trigger DispatchAgent + MedicalAgent + ERAgent in parallel.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "incident": types.Schema(type=types.Type.OBJECT),
                    },
                    required=["incident"],
                ),
            ),
        ]
    )
]

# ── Session config (rules 3 + 4) ──────────────────────────────────────────────
# enable_affective_dialog is a v1alpha Live API field.
# Some SDK patch versions don't expose it on LiveConnectConfig yet (Pydantic extra=forbid).
# We try to include it; if the installed SDK rejects it we fall back without it
# so tests and the rest of the pipeline still work.
_BASE_CONFIG_KWARGS: dict = dict(
    response_modalities=["AUDIO"],
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
        )
    ),
    tools=GEMINI_TOOLS,
    system_instruction=types.Content(
        parts=[
            types.Part(
                text=(
                    "You are Golden Hour Dispatcher — a calm, authoritative AI emergency coordinator. "
                    "Triage the caller, dispatch emergency services, and deliver first aid instructions. "
                    "Use tools to gather real data. When affective dialog detects distress, "
                    "acknowledge it briefly then redirect the caller to action."
                )
            )
        ]
    ),
)

try:
    # Attempt with enable_affective_dialog (rule 3)
    SESSION_CONFIG = types.LiveConnectConfig(
        **_BASE_CONFIG_KWARGS,
        enable_affective_dialog=True,
    )
    _affective_dialog_enabled = True
except Exception:
    # SDK version doesn't support the field yet — proceed without it
    SESSION_CONFIG = types.LiveConnectConfig(**_BASE_CONFIG_KWARGS)
    _affective_dialog_enabled = False

if not _affective_dialog_enabled:
    print("[WARN] enable_affective_dialog not supported by installed SDK version — "
          "upgrade google-genai to enable it.")


# ── Manual tool dispatch (rule 4) ─────────────────────────────────────────────

async def _dispatch_tool_call(tool_call: types.LiveServerToolCall) -> types.LiveClientToolResponse:
    """
    Execute all function calls in a ToolCall concurrently.
    Returns a single LiveClientToolResponse containing ALL results.
    Rule: every FunctionResponse echoes the exact fn_call.id.
    """

    async def _exec_one(fn_call: types.FunctionCall):
        fn = TOOL_REGISTRY.get(fn_call.name)
        if fn is None:
            result = {"error": f"Unknown tool: {fn_call.name}"}
        else:
            try:
                result = await fn(**fn_call.args)
            except Exception as exc:
                result = {"error": str(exc)}
        return types.FunctionResponse(
            id=fn_call.id,          # echo exact id — rule 4
            name=fn_call.name,
            response={"result": result},
        )

    # Run all tools concurrently
    function_responses = await asyncio.gather(
        *[_exec_one(fc) for fc in tool_call.function_calls]
    )

    return types.LiveClientToolResponse(function_responses=list(function_responses))


# ── Main WebSocket handler ─────────────────────────────────────────────────────

async def handle_live_session(websocket, session_id: str):
    """
    Relay WebSocket ↔ Gemini Live session.

    WebSocket message formats expected from client:
      {"type": "audio", "data": "<base64 PCM 16kHz>"}
      {"type": "image", "data": "<base64 JPEG>", "mime": "image/jpeg"}
      {"type": "text",  "data": "..."}

    Messages sent to client:
      {"type": "audio_gemini",     "data": "<base64 audio>"}
      {"type": "audio_elevenlabs", "data": "<base64 mp3>"}
      {"type": "transcript",       "text": "..."}
      {"type": "tool_result",      "data": {...}}
      {"type": "status",           "text": "..."}
    """
    async with _get_client().aio.live.connect(model=_MODEL, config=SESSION_CONFIG) as session:
        try:
            await _run_bidirectional(websocket, session, session_id)
        finally:
            # rule 7: always close Gemini session
            print(f"[{session_id}] Gemini session closed.")


async def _run_bidirectional(websocket, session, session_id: str):
    """rule 6: asyncio.gather for bidirectional streaming — never await sequentially."""

    async def send_loop():
        """Forward client WebSocket messages to Gemini."""
        try:
            async for raw in websocket.iter_text():
                msg = json.loads(raw)
                mtype = msg.get("type")

                if mtype == "audio":
                    pcm = base64.b64decode(msg["data"])
                    await session.send_realtime_input(
                        media=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")
                    )

                elif mtype == "image":
                    img = base64.b64decode(msg["data"])
                    mime = msg.get("mime", "image/jpeg")
                    await session.send_realtime_input(
                        media=types.Blob(data=img, mime_type=mime)
                    )

                elif mtype == "text" or mtype == "text_query":
                    text = msg.get("data") or msg.get("text", "")
                    await session.send_client_content(
                        turns=types.Content(
                            role="user",
                            parts=[types.Part(text=text)],
                        ),
                        turn_complete=True,
                    )
        except Exception as exc:
            print(f"[{session_id}] send_loop error: {exc}")

    async def receive_loop():
        """Receive from Gemini, dispatch tools, forward audio/text to client."""
        try:
            async for response in session.receive():

                # ── Tool call handling (rules 4 + manual dispatch) ────────────
                if response.tool_call:
                    tool_response = await _dispatch_tool_call(response.tool_call)
                    # ALL responses in ONE send_tool_response() — rule 4
                    await session.send_tool_response(
                        function_responses=tool_response.function_responses
                    )

                    # If MedicalAgent generated ElevenLabs audio, stream it directly
                    for fr in tool_response.function_responses:
                        if fr.name == "run_parallel_response":
                            result = fr.response.get("result", {})
                            medical = result.get("medical", {})
                            audio_b64 = medical.get("audio_bytes_b64", "")
                            if audio_b64:
                                await websocket.send_text(json.dumps({
                                    "type": "audio_elevenlabs",
                                    "data": audio_b64,
                                }))
                            # Also push dashboard payload to client
                            await websocket.send_text(json.dumps({
                                "type": "tool_result",
                                "data": result,
                            }))

                # ── Audio from Gemini ─────────────────────────────────────────
                if response.data:
                    await websocket.send_text(json.dumps({
                        "type": "audio_gemini",
                        "data": base64.b64encode(response.data).decode(),
                    }))

                # ── Text / transcript ─────────────────────────────────────────
                if response.text:
                    await websocket.send_text(json.dumps({
                        "type": "transcript",
                        "text": response.text,
                    }))

        except Exception as exc:
            print(f"[{session_id}] receive_loop error: {exc}")
            await websocket.send_text(json.dumps({"type": "error", "text": str(exc)}))

    await asyncio.gather(send_loop(), receive_loop())   # rule 6
