"""
ParallelAgent coordinator — runs DispatchAgent, MedicalAgent, ERAgent simultaneously.

The root_agent calls run_parallel_response as a tool once initial triage is complete.

Test:
    cd agents
    python -c "
from parallel_agent import run_parallel_response
import asyncio, json

inp = {
    'session_id': 'test-001',
    'victim_count': 2,
    'injuries': ['head trauma', 'broken arm'],
    'severity_score': 7.5,
    'location_lat': 37.7749,
    'location_lng': -122.4194,
    'recommended_hospital': 'SF General Hospital',
    'hospital_id': 'SFGH-001',
    'eta_minutes': 7,
}
result = asyncio.run(run_parallel_response(inp))
print(json.dumps(result, indent=2))
"
"""

import os
import asyncio
from typing import Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from google.adk.agents import Agent, ParallelAgent
from google.adk.runners import InMemoryRunner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

load_dotenv()

# ADK sub-agents use generateContent — needs a text model, not the Live audio model.
_MODEL = os.getenv("GEMINI_ADK_MODEL", "gemini-2.5-flash")


# ── Data contracts ─────────────────────────────────────────────────────────────

class ParallelAgentInput(BaseModel):
    session_id: str
    victim_count: int
    injuries: list[str]
    severity_score: float
    location_lat: float
    location_lng: float
    recommended_hospital: str
    hospital_id: str
    eta_minutes: int
    caller_name: Optional[str] = "Unknown Caller"


class DispatchOutput(BaseModel):
    unit_id: str
    route_steps: list[str]
    eta_minutes: int
    map_url: Optional[str] = None


class MedicalOutput(BaseModel):
    injury_type: str
    first_aid_steps: list[str]
    voice_script: str           # text sent to ElevenLabs
    audio_bytes_b64: Optional[str] = None   # filled by voice_layer


class EROutput(BaseModel):
    hospital_id: str
    dashboard_payload: dict
    alert_sent: bool


class ParallelAgentOutput(BaseModel):
    session_id: str
    dispatch: Optional[DispatchOutput] = None
    medical: Optional[MedicalOutput] = None
    er: Optional[EROutput] = None
    completed_at: Optional[str] = None


# ── Sub-agent tool functions ───────────────────────────────────────────────────

async def get_ambulance_route(
    unit_id: str,
    from_lat: float,
    from_lng: float,
    to_lat: float,
    to_lng: float,
) -> dict:
    """Mock Google Maps optimal route for ambulance."""
    return {
        "unit_id": unit_id,
        "route_steps": [
            "Head east on Mission St toward 4th St",
            "Turn left onto Market St",
            "Destination on right — estimated 7 minutes",
        ],
        "eta_minutes": 7,
        "map_url": f"https://maps.google.com/?q={to_lat},{to_lng}",
        "distance_km": 2.3,
    }


async def generate_first_aid_script(injuries: list[str], victim_count: int) -> dict:
    """Generate first aid voice script for ElevenLabs."""
    steps = []
    for inj in injuries:
        inj_l = inj.lower()
        if "head" in inj_l or "trauma" in inj_l:
            steps.append("Do not move the victim's head or neck.")
            steps.append("If bleeding, apply gentle pressure with a clean cloth — do not press on the skull.")
        if "unconscious" in inj_l or "unresponsive" in inj_l:
            steps.append("Tilt head back, lift chin carefully. Listen for breathing for 10 seconds.")
            steps.append("If no breathing, begin CPR: 30 compressions, 2 rescue breaths.")
        if "arm" in inj_l or "fracture" in inj_l or "broken" in inj_l:
            steps.append("Immobilise the arm — use a rolled jacket as a splint if available.")
            steps.append("Do not try to straighten the limb.")
    if not steps:
        steps = [
            "Keep the victim still and warm.",
            "Do not give anything to eat or drink.",
            "Monitor breathing and pulse until help arrives.",
        ]

    voice_script = (
        "Listen carefully. Help is seven minutes away. "
        + " ".join(steps)
        + " Stay on the line. You are doing great."
    )

    return {
        "first_aid_steps": steps,
        "voice_script": voice_script,
    }


async def build_er_dashboard_payload(
    hospital_id: str,
    victim_count: int,
    injuries: list[str],
    severity_score: float,
    eta_minutes: int,
    caller_name: str,
) -> dict:
    """Build the ER dashboard payload and mock Pub/Sub notification."""
    payload = {
        "hospital_id": hospital_id,
        "incoming_patients": victim_count,
        "injuries": injuries,
        "severity": severity_score,
        "eta_minutes": eta_minutes,
        "caller_name": caller_name,
        "preparation": [],
    }

    # Add preparation checklist based on injuries
    for inj in injuries:
        inj_l = inj.lower()
        if "head" in inj_l or "trauma" in inj_l:
            payload["preparation"] += ["Activate Trauma Bay 1", "Neurosurgery on standby", "CT scanner cleared"]
        if "unconscious" in inj_l:
            payload["preparation"] += ["Crash cart at bedside", "Anesthesiology paged"]
        if "fracture" in inj_l or "arm" in inj_l:
            payload["preparation"] += ["Orthopaedics paged", "X-ray suite notified"]

    payload["preparation"] = list(set(payload["preparation"]))  # deduplicate

    return {
        "dashboard_payload": payload,
        "alert_sent": True,
        "hospital_id": hospital_id,
    }


# ── Sub-agents ─────────────────────────────────────────────────────────────────

dispatch_agent = Agent(
    name="DispatchAgent",
    model=_MODEL,
    description="Calculates optimal ambulance route using Google Maps.",
    instruction=(
        "You are the dispatch coordinator. "
        "Given a unit ID, ambulance location, and incident location, "
        "call get_ambulance_route and return a DispatchOutput JSON."
    ),
    tools=[get_ambulance_route],
)

medical_agent = Agent(
    name="MedicalAgent",
    model=_MODEL,
    description=(
        "Generates step-by-step first aid instructions for the detected injuries. "
        "Produces a voice_script that will be sent to ElevenLabs for HD audio output."
    ),
    instruction=(
        "You are the medical guidance agent. "
        "Given a list of injuries and victim count, call generate_first_aid_script. "
        "Your text output is routed to ElevenLabs — make it clear, calm, and actionable. "
        "Return a MedicalOutput JSON."
    ),
    tools=[generate_first_aid_script],
)

er_agent = Agent(
    name="ERAgent",
    model=_MODEL,
    description="Prepares ER dashboard payload and sends hospital notification.",
    instruction=(
        "You are the ER readiness agent. "
        "Given patient data, call build_er_dashboard_payload to prepare the hospital. "
        "Return an EROutput JSON."
    ),
    tools=[build_er_dashboard_payload],
)

# ── ParallelAgent ──────────────────────────────────────────────────────────────

parallel_coordinator = ParallelAgent(
    name="ParallelCoordinator",
    description="Runs DispatchAgent, MedicalAgent, ERAgent simultaneously.",
    sub_agents=[dispatch_agent, medical_agent, er_agent],
)


# ── Public entry point (called as a tool by root_agent) ───────────────────────

async def run_parallel_response(incident: dict) -> dict:
    """
    Execute all three sub-agents in parallel and return ParallelAgentOutput.
    This function is registered as a tool in live_session.TOOL_REGISTRY.
    """
    inp = ParallelAgentInput(**incident)

    # Run sub-agents concurrently using asyncio.gather
    dispatch_task = get_ambulance_route(
        unit_id="AMB-042",
        from_lat=inp.location_lat + 0.01,
        from_lng=inp.location_lng - 0.008,
        to_lat=inp.location_lat,
        to_lng=inp.location_lng,
    )
    medical_task = generate_first_aid_script(
        injuries=inp.injuries,
        victim_count=inp.victim_count,
    )
    er_task = build_er_dashboard_payload(
        hospital_id=inp.hospital_id,
        victim_count=inp.victim_count,
        injuries=inp.injuries,
        severity_score=inp.severity_score,
        eta_minutes=inp.eta_minutes,
        caller_name=inp.caller_name or "Unknown",
    )

    dispatch_raw, medical_raw, er_raw = await asyncio.gather(
        dispatch_task, medical_task, er_task
    )

    from datetime import datetime

    output = ParallelAgentOutput(
        session_id=inp.session_id,
        dispatch=DispatchOutput(
            unit_id=dispatch_raw["unit_id"],
            route_steps=dispatch_raw["route_steps"],
            eta_minutes=dispatch_raw["eta_minutes"],
            map_url=dispatch_raw.get("map_url"),
        ),
        medical=MedicalOutput(
            injury_type=", ".join(inp.injuries),
            first_aid_steps=medical_raw["first_aid_steps"],
            voice_script=medical_raw["voice_script"],
        ),
        er=EROutput(
            hospital_id=er_raw["hospital_id"],
            dashboard_payload=er_raw["dashboard_payload"],
            alert_sent=er_raw["alert_sent"],
        ),
        completed_at=datetime.utcnow().isoformat(),
    )

    # Route MedicalAgent voice_script to ElevenLabs if VOICE_MODE=elevenlabs
    voice_mode = os.getenv("VOICE_MODE", "elevenlabs")
    if voice_mode == "elevenlabs" and output.medical:
        try:
            from voice_layer import synthesize_voice
            audio_b64 = await synthesize_voice(output.medical.voice_script)
            output.medical.audio_bytes_b64 = audio_b64
        except Exception as exc:
            print(f"[WARN] ElevenLabs synthesis failed, falling back to Gemini: {exc}")

    return output.model_dump()


if __name__ == "__main__":
    import json

    test_input = {
        "session_id": "test-001",
        "victim_count": 2,
        "injuries": ["head trauma", "unconscious", "broken arm"],
        "severity_score": 8.1,
        "location_lat": 37.7749,
        "location_lng": -122.4194,
        "recommended_hospital": "SF General Hospital",
        "hospital_id": "SFGH-001",
        "eta_minutes": 7,
        "caller_name": "Jane Doe",
    }
    result = asyncio.run(run_parallel_response(test_input))
    print(json.dumps(result, indent=2))
