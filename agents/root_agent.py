"""
Root Agent — emergency dispatcher AI.

Standalone test:
    cd agents
    python -c "
from root_agent import dispatcher_agent, runner
import asyncio, json

result = asyncio.run(runner('Car accident at 37.7749,-122.4194. Two victims. One unconscious.'))
print(json.dumps(result, indent=2))
"
"""

import os
import asyncio
import json
from typing import Optional
from datetime import datetime

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

load_dotenv()

# ── Output schema ─────────────────────────────────────────────────────────────

class IncidentSummary(BaseModel):
    victim_count: int = Field(..., description="Number of victims at scene")
    injuries: list[str] = Field(..., description="List of detected injury types")
    severity_score: float = Field(..., ge=0, le=10, description="Triage severity 0-10")
    recommended_hospital: str = Field(..., description="Hospital name or ID")
    eta_minutes: int = Field(..., description="Ambulance ETA in minutes")
    first_aid_instructions: list[str] = Field(..., description="Step-by-step first aid")
    session_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# ── Tool definitions ──────────────────────────────────────────────────────────

async def get_nearest_ambulance(lat: float, lng: float) -> dict:
    """Return the nearest available ambulance and its ETA."""
    # Mock data — real impl would call CAD (Computer-Aided Dispatch) system
    return {
        "unit_id": "AMB-042",
        "crew": ["Paramedic J. Reyes", "EMT S. Park"],
        "current_lat": lat + 0.01,
        "current_lng": lng - 0.008,
        "eta_minutes": 7,
        "equipment": ["defibrillator", "advanced_airway", "trauma_kit"],
        "status": "en_route",
    }


async def get_hospital_capacity(specialty: str) -> dict:
    """Return current ER bed availability for a given specialty."""
    # Mock data — real impl queries hospital ADT system via FHIR
    capacity_map = {
        "trauma": {"hospital_id": "SFGH-001", "name": "SF General Hospital", "available_beds": 3, "trauma_level": 1},
        "cardiac": {"hospital_id": "UCSF-002", "name": "UCSF Medical Center", "available_beds": 2, "trauma_level": 2},
        "pediatric": {"hospital_id": "CHSF-003", "name": "UCSF Benioff Children's", "available_beds": 5, "trauma_level": 2},
        "general": {"hospital_id": "SFGH-001", "name": "SF General Hospital", "available_beds": 3, "trauma_level": 1},
    }
    key = specialty.lower() if specialty.lower() in capacity_map else "general"
    return capacity_map[key]


async def create_incident_summary(
    victims: int,
    injuries: list[str],
    location: str,
) -> dict:
    """Build a structured IncidentSummary from triage data."""
    severity = min(10.0, float(victims) * 1.5 + len(injuries) * 0.8)
    hospital = await get_hospital_capacity("trauma")
    ambulance = await get_nearest_ambulance(37.7749, -122.4194)

    first_aid = []
    for injury in injuries:
        inj = injury.lower()
        if "bleed" in inj or "hemorrhag" in inj:
            first_aid.append("Apply firm direct pressure to wound with clean cloth — do not remove once placed.")
        if "unconscious" in inj or "unresponsive" in inj:
            first_aid.append("Tilt head back, lift chin. Check breathing every 30 seconds.")
        if "fracture" in inj or "broken" in inj:
            first_aid.append("Immobilise the limb — do not attempt to realign.")
        if "burn" in inj:
            first_aid.append("Cool burn with lukewarm running water for 10 minutes. Do not use ice.")
    if not first_aid:
        first_aid = ["Keep victim still and warm. Monitor breathing. Do not give food or water."]

    summary = IncidentSummary(
        victim_count=victims,
        injuries=injuries,
        severity_score=round(severity, 1),
        recommended_hospital=hospital["name"],
        eta_minutes=ambulance["eta_minutes"],
        first_aid_instructions=first_aid,
    )
    return summary.model_dump()


async def notify_er_team(hospital_id: str, summary: dict) -> dict:
    """Publish an ER notification to Pub/Sub (stub — real impl uses google-cloud-pubsub)."""
    import os

    topic = os.getenv("PUBSUB_TOPIC", "er-notifications")
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "demo-project")

    if os.getenv("DEMO_MODE", "false").lower() == "true":
        print(f"[DEMO] Would publish to projects/{project}/topics/{topic}")
        return {"status": "demo_stub", "hospital_id": hospital_id}

    # Real Pub/Sub publish (uncomment in production)
    # from google.cloud import pubsub_v1
    # publisher = pubsub_v1.PublisherClient()
    # topic_path = publisher.topic_path(project, topic)
    # data = json.dumps({"hospital_id": hospital_id, "summary": summary}).encode()
    # future = publisher.publish(topic_path, data)
    # future.result()

    return {"status": "notified", "hospital_id": hospital_id, "topic": topic}


# ── Firestore callback ─────────────────────────────────────────────────────────

async def before_agent_callback(callback_context) -> None:
    """Fetch caller history from Firestore and inject into session state."""
    session_id = getattr(callback_context, "session_id", None)
    if not session_id:
        return

    if os.getenv("DEMO_MODE", "false").lower() == "true":
        callback_context.state["caller_history"] = []
        return

    try:
        from google.cloud import firestore
        db = firestore.AsyncClient(project=os.getenv("GOOGLE_CLOUD_PROJECT"))
        collection = os.getenv("FIRESTORE_COLLECTION", "incidents")
        docs = db.collection(collection).where("session_id", "==", session_id).limit(5)
        history = [doc.to_dict() async for doc in await docs.get()]
        callback_context.state["caller_history"] = history
    except Exception as exc:
        print(f"[WARN] Firestore fetch failed: {exc}")
        callback_context.state["caller_history"] = []


# ── Agent definition ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Golden Hour Dispatcher — an AI-powered emergency triage coordinator.

Your job:
1. Extract victim count, injury types, and location from caller description.
2. Call get_nearest_ambulance with the incident coordinates.
3. Call get_hospital_capacity with the primary injury specialty.
4. Call create_incident_summary with all collected data.
5. Call notify_er_team with the hospital ID and summary.
6. Return the structured IncidentSummary.

Always:
- Be calm, clear, and authoritative.
- If the caller is panicking, acknowledge their fear briefly, then redirect to action.
- Prioritise life-threatening injuries first.
- Never guess — ask one clarifying question if critical info is missing.
- Do not mention that you are an AI unless directly asked.
"""

# ADK runner uses generateContent (not Live API) — needs a text model.
# GEMINI_LIVE_MODEL is used only for the Live WebSocket session in live_session.py.
_ADK_MODEL = os.getenv("GEMINI_ADK_MODEL", "gemini-2.5-flash")

dispatcher_agent = Agent(
    name="GoldenHourDispatcher",
    model=_ADK_MODEL,
    description="Real-time emergency triage and dispatch coordinator.",
    instruction=SYSTEM_PROMPT,
    tools=[
        get_nearest_ambulance,
        get_hospital_capacity,
        create_incident_summary,
        notify_er_team,
    ],
    before_agent_callback=before_agent_callback,
)


# ── Standalone runner helper ──────────────────────────────────────────────────

async def runner(prompt: str, session_id: str = "test-session-001") -> dict:
    """Run the dispatcher agent with a text prompt. Returns the final response dict."""
    r = InMemoryRunner(agent=dispatcher_agent, app_name="golden-hour")

    session = await r.session_service.create_session(
        app_name="golden-hour",
        user_id="test-user",
        session_id=session_id,
    )

    events = r.run_async(
        user_id="test-user",
        session_id=session.id,
        new_message=genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=prompt)],
        ),
    )

    final_response = {}
    async for event in events:
        if event.is_final_response():
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        try:
                            final_response = json.loads(part.text)
                        except json.JSONDecodeError:
                            final_response = {"raw_response": part.text}
    return final_response


if __name__ == "__main__":
    prompt = (
        "There's been a bad car accident at the corner of Market and 5th in San Francisco. "
        "Two people are hurt — one is unconscious and bleeding from the head, "
        "the other has a possible broken arm. Please help!"
    )
    result = asyncio.run(runner(prompt))
    print(json.dumps(result, indent=2))
