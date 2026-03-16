"""
Backend API tests.

Run: cd backend && pytest -v

All external services (Firestore, Gemini, ElevenLabs) are mocked.
"""

import os
import sys
import pytest

# Set TESTING before importing app so Firestore uses in-memory store
os.environ["TESTING"] = "true"
os.environ["GEMINI_API_KEY"] = "test-key"
os.environ["ELEVENLABS_API_KEY"] = "test-key"
os.environ["DEMO_MODE"] = "true"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents"))

import httpx
from httpx import AsyncClient, ASGITransport

from main import app, _sessions, _tokens


# ── Helpers ────────────────────────────────────────────────────────────────────

AUTH = {"Authorization": "Bearer test-jwt-token"}


@pytest.fixture(autouse=True)
def clear_state():
    """Clear in-memory state before each test."""
    _sessions.clear()
    _tokens.clear()
    yield
    _sessions.clear()
    _tokens.clear()


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── /health ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client):
    async with client as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


# ── POST /api/incident ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_incident_returns_session_and_token(client):
    async with client as c:
        resp = await c.post(
            "/api/incident",
            json={"caller_name": "Jane Doe", "location": "Market & 5th"},
            headers=AUTH,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert "token" in data
    assert len(data["session_id"]) > 0
    assert len(data["token"]) > 0


@pytest.mark.asyncio
async def test_create_incident_requires_auth(client):
    async with client as c:
        resp = await c.post("/api/incident", json={})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_incident_scrubs_pii(client):
    async with client as c:
        resp = await c.post(
            "/api/incident",
            json={"notes": "SSN 123-45-6789 caller"},
            headers=AUTH,
        )
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]
    stored = _sessions[session_id]
    assert "123-45-6789" not in stored["notes"]
    assert "[REDACTED]" in stored["notes"]


# ── GET /api/incident/{session_id} ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_incident_found(client):
    async with client as c:
        # Create first
        create_resp = await c.post(
            "/api/incident",
            json={"caller_name": "Bob"},
            headers=AUTH,
        )
        session_id = create_resp.json()["session_id"]

        # Fetch
        resp = await c.get(f"/api/incident/{session_id}", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["session_id"] == session_id


@pytest.mark.asyncio
async def test_get_incident_not_found(client):
    async with client as c:
        resp = await c.get("/api/incident/does-not-exist", headers=AUTH)
    assert resp.status_code == 404


# ── POST /api/hospital/{id}/notify ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hospital_notify(client):
    async with client as c:
        resp = await c.post(
            "/api/hospital/SFGH-001/notify",
            json={"hospital_id": "SFGH-001", "summary": {"victims": 2}},
            headers=AUTH,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "notified"
    assert data["hospital_id"] == "SFGH-001"


# ── POST /demo/simulate ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_demo_simulate(client):
    async with client as c:
        resp = await c.post("/demo/simulate", json={"speed": 1.5})
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert data["message"] == "Simulation started"


@pytest.mark.asyncio
async def test_demo_simulate_with_session_id(client):
    async with client as c:
        resp = await c.post(
            "/demo/simulate",
            json={"session_id": "my-demo-session", "speed": 1.0},
        )
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "my-demo-session"
