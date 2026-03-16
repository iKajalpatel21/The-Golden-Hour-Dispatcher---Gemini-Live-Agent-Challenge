"""
Golden Hour Dispatcher — FastAPI backend.

Run:
    cd backend && uvicorn main:app --reload --port 8080

Test all endpoints:
    cd backend && pytest -v
"""

import os
import sys
import json
import uuid
import base64
import asyncio
import re
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    HTTPException, Depends, Header, Request
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Allow importing from agents/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))

load_dotenv()

app = FastAPI(title="Golden Hour Dispatcher", version="1.0.0")

# ── CORS (localhost:5173 = Vite dev server) ───────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory stores (replace with Firestore in prod) ─────────────────────────
_sessions: dict[str, dict] = {}   # session_id → state
_tokens:   dict[str, str]  = {}   # ephemeral_token → session_id


# ── PII filter regex (Model Armor placeholder) ────────────────────────────────
_PII_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),          # SSN
    re.compile(r"\b\d{16}\b"),                       # credit card
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),  # email
]

def _scrub_pii(text: str) -> str:
    """Regex PII filter — production would use Cloud Model Armor API here."""
    for pat in _PII_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


# ── Auth helpers ───────────────────────────────────────────────────────────────

def _verify_bearer(authorization: Optional[str] = Header(None)) -> str:
    """Stub JWT validation — replace with real verify in production."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1]
    # TODO: validate JWT signature with google-auth or python-jose
    return token


def _verify_ws_token(token: str, session_id: str) -> bool:
    """Validate ephemeral WebSocket token."""
    expected = _tokens.get(token)
    return expected == session_id


# ── Firestore helpers ──────────────────────────────────────────────────────────

async def _firestore_get(session_id: str) -> Optional[dict]:
    if os.getenv("DEMO_MODE", "false").lower() == "true" or os.getenv("TESTING"):
        return _sessions.get(session_id)
    try:
        from google.cloud import firestore
        db = firestore.AsyncClient(project=os.getenv("GOOGLE_CLOUD_PROJECT"))
        col = os.getenv("FIRESTORE_COLLECTION", "incidents")
        doc = await db.collection(col).document(session_id).get()
        return doc.to_dict() if doc.exists else None
    except Exception:
        return _sessions.get(session_id)


async def _firestore_set(session_id: str, data: dict):
    _sessions[session_id] = data
    if os.getenv("DEMO_MODE", "false").lower() == "true" or os.getenv("TESTING"):
        return
    try:
        from google.cloud import firestore
        db = firestore.AsyncClient(project=os.getenv("GOOGLE_CLOUD_PROJECT"))
        col = os.getenv("FIRESTORE_COLLECTION", "incidents")
        await db.collection(col).document(session_id).set(data, merge=True)
    except Exception as exc:
        print(f"[WARN] Firestore write failed: {exc}")


# ── Request / Response models ──────────────────────────────────────────────────

class CreateIncidentRequest(BaseModel):
    caller_name: Optional[str] = "Unknown"
    location: Optional[str] = None
    notes: Optional[str] = None


class HospitalNotifyRequest(BaseModel):
    hospital_id: str
    summary: dict


class SimulateRequest(BaseModel):
    session_id: Optional[str] = None
    speed: float = 1.0


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "model": os.getenv("GEMINI_LIVE_MODEL", "not set")}


@app.post("/api/incident")
async def create_incident(
    body: CreateIncidentRequest,
    _token: str = Depends(_verify_bearer),
):
    """Create a new incident session, return session_id + ephemeral WebSocket token."""
    session_id = str(uuid.uuid4())
    ephemeral_token = str(uuid.uuid4())

    data = {
        "session_id": session_id,
        "caller_name": body.caller_name,
        "location": body.location,
        "notes": _scrub_pii(body.notes or ""),
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await _firestore_set(session_id, data)
    _tokens[ephemeral_token] = session_id

    return {"session_id": session_id, "token": ephemeral_token}


@app.get("/api/incident/{session_id}")
async def get_incident(
    session_id: str,
    _token: str = Depends(_verify_bearer),
):
    """Fetch current incident state from Firestore."""
    data = await _firestore_get(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Incident not found")
    return data


@app.post("/api/hospital/{hospital_id}/notify")
async def notify_hospital(
    hospital_id: str,
    body: HospitalNotifyRequest,
    _token: str = Depends(_verify_bearer),
):
    """Trigger ER dashboard update via Pub/Sub."""
    topic = os.getenv("PUBSUB_TOPIC", "er-notifications")
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "demo-project")

    if os.getenv("DEMO_MODE", "false").lower() != "true" and not os.getenv("TESTING"):
        try:
            from google.cloud import pubsub_v1
            publisher = pubsub_v1.PublisherClient()
            topic_path = publisher.topic_path(project, topic)
            payload = json.dumps({
                "hospital_id": hospital_id,
                "summary": body.summary,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }).encode()
            future = publisher.publish(topic_path, payload)
            future.result(timeout=5)
        except Exception as exc:
            print(f"[WARN] Pub/Sub publish failed: {exc}")

    return {"status": "notified", "hospital_id": hospital_id, "topic": topic}


@app.post("/demo/simulate")
async def demo_simulate(body: SimulateRequest):
    """Replay a pre-recorded incident for demo mode."""
    session_id = body.session_id or str(uuid.uuid4())
    data = {
        "session_id": session_id,
        "caller_name": "Demo Caller",
        "status": "simulating",
        "demo": True,
        "speed": body.speed,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await _firestore_set(session_id, data)
    return {"session_id": session_id, "message": "Simulation started"}


# ── WebSocket endpoint ─────────────────────────────────────────────────────────

@app.websocket("/ws/incident/{session_id}")
async def ws_incident(websocket: WebSocket, session_id: str, token: str = ""):
    """
    Gemini Live relay WebSocket.
    Client must pass ?token=<ephemeral_token> in the URL.
    """
    if not _verify_ws_token(token, session_id):
        await websocket.close(code=4001)
        return

    await websocket.accept()

    # Update session status
    session_data = await _firestore_get(session_id) or {}
    session_data["status"] = "connected"
    await _firestore_set(session_id, session_data)

    try:
        from live_session import handle_live_session
        await handle_live_session(websocket, session_id)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        print(f"[{session_id}] WebSocket error: {exc}")
        try:
            await websocket.send_text(json.dumps({"type": "error", "text": str(exc)}))
        except Exception:
            pass
    finally:
        session_data = await _firestore_get(session_id) or {}
        session_data["status"] = "closed"
        session_data["closed_at"] = datetime.now(timezone.utc).isoformat()
        await _firestore_set(session_id, session_data)
