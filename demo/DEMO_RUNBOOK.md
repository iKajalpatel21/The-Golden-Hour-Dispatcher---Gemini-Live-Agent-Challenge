# Golden Hour Dispatcher — 4-Minute Demo Script

> **Projector setup:** Two windows side by side — Caller View (left), ER Dashboard (right).
> Font size bumped to 16pt in browser. Dark mode on both.

---

## MINUTE 1 — The Setup (0:00–1:00)

**Words to say:**
> "Emergency response is where seconds matter most. Today we're showing a system where AI doesn't just answer questions — it acts. This is the Golden Hour Dispatcher. It listens to a 911 call in real time, sees the scene through the phone camera, and coordinates ambulances, hospitals, and first-aid guidance simultaneously."

**Click:** Nothing — let the split screen speak.

**Judges see:** Both panels loaded, red SOS button prominent on the left, empty ER map on the right.

**Pause moment:** After "simultaneously" — let it land for 2 seconds. Then:

> "Let's see it work."

**Recovery if UI doesn't load:** `DEMO_MODE=true make demo` — the simulator runs headlessly and prints all events to the terminal. Walk through the terminal output verbally.

---

## MINUTE 2 — The Call (1:00–2:00)

**Words to say:**
> "I'm going to play a pre-recorded 911 call. The caller is panicking — they've just witnessed a car accident."

**Click:** Press the red **SOS** button on CallerView.

**Judges see:**
- Camera preview activates (or demo video)
- Status bar changes to "Connected — speak now"
- Transcript panel begins populating on the right

**At T+15s (automatic):**
> "Notice the status badge — Gemini just detected elevated distress in the caller's voice via **affective dialog**. The system is now in high-urgency response mode."

**Judges see:** Voice badge appears in the top-right of ER Dashboard showing "Gemini Live".

**Pause moment:** Point to the badge. "This is Gemini Live's built-in emotional intelligence — not a rule, not a keyword match. It's listening to how they're speaking."

**Recovery if WebSocket fails:** Say "Let me switch to demo mode" and run:
```
DEMO_MODE=true python demo/incident_simulator.py --speed 1.0
```
Walk through the printed timeline.

---

## MINUTE 3 — The Triage + Parallel Agents (2:00–3:00)

**Words to say:**
> "At T+30 seconds, the system captures a video frame from the caller's camera. Gemini's vision model identifies a head laceration and an arm fracture."

**Judges see:** ER Dashboard — Incident Summary panel populates:
- Victims: 2
- Injuries: head trauma, unconscious, broken arm
- Severity: 8.1/10 (shown in red)

**At T+45s (automatic):**
> "Here's where it gets interesting. Three agents fire **simultaneously** — a Dispatch Agent routing the nearest ambulance via Google Maps, a Medical Agent generating first-aid instructions, and an ER Agent preparing SF General Hospital right now."

**Judges see:**
- Map: blue ambulance pin appears and begins moving toward the red incident pin
- Checklist panel: "Activate Trauma Bay 1", "Neurosurgery on standby", "CT scanner cleared"
- ETA appears: "Ambulance ETA: 7 min"

**Pause moment:** Point to the map. Slow down. "The ambulance is moving in real time. The hospital is already preparing before the patient arrives."

**Recovery if map doesn't render:** The ER Dashboard right-side panels still show all data. Say "The map animation is loading — but look at this preparation checklist populating automatically."

---

## MINUTE 4 — The Voice Switch + Close (3:00–4:00)

**Words to say:**
> "At T+50 seconds, something important happens."

**Judges see:** Voice badge changes from "Gemini Live" → **"ElevenLabs HD"**

> "Notice the voice just changed. Gemini detected sustained caller distress via affective dialog and handed off voice delivery to **ElevenLabs** for higher-fidelity, emotionally calibrated guidance."

> "The instructions you're hearing now — 'Apply firm pressure to the wound. Do not move the patient. You are doing great.' — that's ElevenLabs synthesising in real time with stability tuned for urgency."

**Pause moment (longest one):** Let the ElevenLabs audio finish playing. Silence is powerful here. Then:

> "One architecture. Two voice engines. Gemini reasons. ElevenLabs speaks when it matters most."

**Click:** Press **Alert Specialist** button on ER Dashboard.

**Judges see:** Status bar updates: "Specialist alerted via Pub/Sub"

**Final words:**
> "The entire pipeline — caller audio, computer vision, parallel agent coordination, hospital notification, and HD voice synthesis — runs in under 60 seconds on Cloud Run. This is what the golden hour of emergency response looks like with AI."

**Recovery if ElevenLabs is slow:**
Set `VOICE_MODE=gemini` in the running environment — Gemini native audio continues seamlessly. Say:
> "I've switched to Gemini's native audio — the architecture is identical, the voice engine is swappable by a single environment variable."

---

## Quick Reference

| Time | Event | Demo Action |
|------|-------|-------------|
| T+0  | Call starts | Press SOS |
| T+15 | Distress detected | Point to voice badge |
| T+30 | Vision triage | Point to Incident Summary |
| T+45 | Parallel agents | Point to map + checklist |
| T+50 | ElevenLabs fires | Explain voice switch |
| T+60 | ER dashboard full | Let it breathe |
| T+75 | Ambulance moving | Point to map |
| T+90 | Close | "Alert Specialist" button |
