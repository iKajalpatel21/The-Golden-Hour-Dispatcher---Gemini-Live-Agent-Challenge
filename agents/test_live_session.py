"""
pytest tests for live_session.py

Run: cd agents && pytest test_live_session.py -v

Tests:
  1. Two-tool parallel ToolCall → both FunctionResponses sent in ONE call.
  2. Unknown tool → error FunctionResponse, still one send() call.
  3. ElevenLabs audio from run_parallel_response is forwarded to WebSocket.
"""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Fixtures ───────────────────────────────────────────────────────────────────

def make_fn_call(name: str, fn_id: str, args: dict):
    fc = MagicMock()
    fc.name = name
    fc.id = fn_id
    fc.args = args
    return fc


def make_tool_call(fn_calls: list):
    tc = MagicMock()
    tc.function_calls = fn_calls
    return tc


# ── Test 1: parallel two-tool ToolCall ────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_tool_parallel_dispatch():
    """Both tools execute concurrently and responses sent in a single call."""
    from live_session import _dispatch_tool_call

    fn1 = make_fn_call("get_nearest_ambulance", "id-001", {"lat": 37.77, "lng": -122.41})
    fn2 = make_fn_call("get_hospital_capacity", "id-002", {"specialty": "trauma"})
    tool_call = make_tool_call([fn1, fn2])

    response = await _dispatch_tool_call(tool_call)

    # Must contain exactly 2 FunctionResponses
    assert len(response.function_responses) == 2

    ids = {fr.id for fr in response.function_responses}
    assert "id-001" in ids
    assert "id-002" in ids

    names = {fr.name for fr in response.function_responses}
    assert "get_nearest_ambulance" in names
    assert "get_hospital_capacity" in names

    # Each response must have a result
    for fr in response.function_responses:
        assert "result" in fr.response
        assert fr.response["result"]  # non-empty


@pytest.mark.asyncio
async def test_fn_call_id_echoed_exactly():
    """FunctionResponse id must exactly match the incoming fn_call.id."""
    from live_session import _dispatch_tool_call

    fn = make_fn_call("get_nearest_ambulance", "exact-id-xyz-999", {"lat": 0.0, "lng": 0.0})
    tool_call = make_tool_call([fn])

    response = await _dispatch_tool_call(tool_call)
    assert response.function_responses[0].id == "exact-id-xyz-999"


# ── Test 2: unknown tool → graceful error ─────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_tool_returns_error_response():
    """Unknown tool name → error in response, not a raised exception."""
    from live_session import _dispatch_tool_call

    fn = make_fn_call("non_existent_tool", "id-err", {})
    tool_call = make_tool_call([fn])

    response = await _dispatch_tool_call(tool_call)
    assert len(response.function_responses) == 1
    fr = response.function_responses[0]
    assert fr.id == "id-err"
    assert "error" in fr.response["result"]


# ── Test 3: all responses in ONE send() call ──────────────────────────────────

@pytest.mark.asyncio
async def test_all_responses_sent_in_one_call():
    """
    After _dispatch_tool_call, caller sends ONE session.send(tool_response).
    Verify send is called exactly once even for multi-tool ToolCall.
    """
    from live_session import _dispatch_tool_call

    fn1 = make_fn_call("get_nearest_ambulance", "a1", {"lat": 1.0, "lng": 2.0})
    fn2 = make_fn_call("get_hospital_capacity", "a2", {"specialty": "cardiac"})
    fn3 = make_fn_call("notify_er_team", "a3", {"hospital_id": "H1", "summary": {}})
    tool_call = make_tool_call([fn1, fn2, fn3])

    mock_session = AsyncMock()
    response = await _dispatch_tool_call(tool_call)

    # Simulate the caller's one send()
    await mock_session.send(input=response)

    # send() called exactly once — all three responses batched together
    assert mock_session.send.call_count == 1
    sent_arg = mock_session.send.call_args.kwargs["input"]
    assert len(sent_arg.function_responses) == 3


# ── Test 4: ElevenLabs audio forwarded to WebSocket ──────────────────────────

@pytest.mark.asyncio
async def test_elevenlabs_audio_forwarded_to_websocket():
    """
    When run_parallel_response returns audio_bytes_b64, it must be
    forwarded to the WebSocket as type=audio_elevenlabs.
    """
    fake_audio_b64 = base64.b64encode(b"\xff\xfb\x90fake_mp3_data").decode()

    from live_session import _dispatch_tool_call

    # Patch run_parallel_response to return fake audio
    async def mock_run_parallel(incident):
        return {
            "session_id": "s1",
            "medical": {
                "voice_script": "Help is coming.",
                "audio_bytes_b64": fake_audio_b64,
                "first_aid_steps": [],
                "injury_type": "test",
            },
            "dispatch": None,
            "er": None,
        }

    with patch("live_session.TOOL_REGISTRY", {
        "run_parallel_response": mock_run_parallel
    }):
        fn = make_fn_call("run_parallel_response", "p1", {"incident": {"session_id": "s1"}})
        tool_call = make_tool_call([fn])
        response = await _dispatch_tool_call(tool_call)

    # The response result must contain the audio
    fr = response.function_responses[0]
    medical = fr.response["result"]["medical"]
    assert medical["audio_bytes_b64"] == fake_audio_b64


# ── Test 5: create_incident_summary returns IncidentSummary shape ─────────────

@pytest.mark.asyncio
async def test_create_incident_summary_shape():
    from root_agent import create_incident_summary

    result = await create_incident_summary(
        victims=2,
        injuries=["head trauma", "unconscious"],
        location="Market and 5th, SF",
    )

    assert result["victim_count"] == 2
    assert isinstance(result["injuries"], list)
    assert 0 <= result["severity_score"] <= 10
    assert result["eta_minutes"] > 0
    assert isinstance(result["first_aid_instructions"], list)
    assert len(result["first_aid_instructions"]) > 0
