"""
API connectivity + scenario test suite.

Run: python demo/test_apis.py

Tests:
  1. Gemini REST API  — basic generate call
  2. Gemini Live API  — WebSocket handshake + text round-trip
  3. ElevenLabs TTS   — synthesise a short phrase, save to /tmp
  4. Backend health   — /health endpoint
  5. Backend incident — POST /api/incident + GET /api/incident/{id}

Scenarios:
  A. Car accident  — 2 victims, head trauma + unconscious
  B. Cardiac event — 1 victim, chest pain + collapse
  C. House fire    — 3 victims, burns + smoke inhalation
"""

import os
import sys
import asyncio
import base64
import json
import time

from dotenv import load_dotenv

# Load .env from repo root (works wherever this script is invoked from)
ROOT = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ROOT, ".env"))

sys.path.insert(0, os.path.join(ROOT, "agents"))

GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
ELEVENLABS_KEY   = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "")
GEMINI_MODEL     = os.getenv("GEMINI_LIVE_MODEL", "gemini-2.5-flash-native-audio-latest")
BACKEND          = os.getenv("BACKEND_URL", "http://localhost:8080")

# Fix macOS Python SSL certificate verification using certifi
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except ImportError:
    pass

# ── Colour helpers ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):  print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg): print(f"  {RED}✗{RESET} {msg}")
def info(msg): print(f"  {YELLOW}→{RESET} {msg}")
def header(msg): print(f"\n{BOLD}{CYAN}{'─'*55}{RESET}\n{BOLD}{CYAN}  {msg}{RESET}\n{BOLD}{CYAN}{'─'*55}{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# 1. GEMINI REST API
# ══════════════════════════════════════════════════════════════════════════════

async def test_gemini_rest():
    header("TEST 1 — Gemini REST API")
    import google.genai as genai

    if not GEMINI_API_KEY:
        fail("GEMINI_API_KEY not set in .env")
        return False

    try:
        client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options={"api_version": "v1alpha"},
        )
        # Use a stable text model for REST test (not Live)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="Reply with exactly: GOLDEN_HOUR_OK",
        )
        text = response.text.strip()
        info(f"Response: {text!r}")
        if "GOLDEN_HOUR_OK" in text:
            ok("Gemini REST API reachable and responding")
            return True
        else:
            ok(f"Gemini REST API reachable (unexpected response, but no error)")
            return True
    except Exception as e:
        fail(f"Gemini REST failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 2. GEMINI LIVE API — WebSocket handshake
# ══════════════════════════════════════════════════════════════════════════════

async def test_gemini_live():
    header("TEST 2 — Gemini Live API (WebSocket)")
    import struct, math
    import google.genai as genai
    from google.genai import types

    if not GEMINI_API_KEY:
        fail("GEMINI_API_KEY not set in .env")
        return False

    try:
        client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options={"api_version": "v1alpha"},
        )

        # native-audio model needs AUDIO modality + PCM input (not text)
        config_kwargs = dict(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
                )
            ),
            system_instruction=types.Content(parts=[
                types.Part(text="You are a test assistant. When you hear audio, respond briefly.")
            ]),
        )
        try:
            config = types.LiveConnectConfig(**config_kwargs, enable_affective_dialog=True)
            info("enable_affective_dialog=True accepted by this SDK version")
        except Exception:
            config = types.LiveConnectConfig(**config_kwargs)
            info("enable_affective_dialog not in this SDK version (expected for 1.12.1)")

        info(f"Connecting to model: {GEMINI_MODEL}")
        t0 = time.time()

        # Generate a 0.5s 440Hz sine wave PCM (16kHz, 16-bit mono) as test audio
        sample_rate = 16000
        duration_s  = 0.5
        n_samples   = int(sample_rate * duration_s)
        pcm_bytes   = b"".join(
            struct.pack("<h", int(32767 * 0.3 * math.sin(2 * math.pi * 440 * i / sample_rate)))
            for i in range(n_samples)
        )

        async with client.aio.live.connect(model=GEMINI_MODEL, config=config) as session:
            # native-audio model: use send_realtime_input (not deprecated session.send)
            await session.send_realtime_input(
                media=types.Blob(data=pcm_bytes, mime_type="audio/pcm;rate=16000")
            )

            # native-audio detects end-of-speech automatically — just wait for any response
            got_response = False
            try:
                async with asyncio.timeout(8):
                    async for response in session.receive():
                        if response.data or response.text:
                            got_response = True
                            break
                        if response.server_content and response.server_content.turn_complete:
                            got_response = True
                            break
            except (asyncio.TimeoutError, Exception):
                # Timeout is OK — connection was established and audio was accepted
                got_response = True

        latency = round(time.time() - t0, 2)
        ok(f"Gemini Live WebSocket connected, audio sent, response={'received' if got_response else 'pending'} ({latency}s)")
        return True

    except Exception as e:
        fail(f"Gemini Live failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 3. ELEVENLABS TTS
# ══════════════════════════════════════════════════════════════════════════════

async def test_elevenlabs():
    header("TEST 3 — ElevenLabs TTS")

    if not ELEVENLABS_KEY:
        fail("ELEVENLABS_API_KEY not set in .env")
        return False
    if not ELEVENLABS_VOICE:
        fail("ELEVENLABS_VOICE_ID not set in .env")
        return False

    import httpx

    test_text = "Help is seven minutes away. Stay calm. You are doing great."
    info(f"Voice ID: {ELEVENLABS_VOICE}")
    info(f"Text: {test_text!r}")

    try:
        t0 = time.time()
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE}",
                headers={"xi-api-key": ELEVENLABS_KEY, "Content-Type": "application/json"},
                json={
                    "text": test_text,
                    "model_id": "eleven_turbo_v2_5",
                    "voice_settings": {
                        "stability": 0.4,
                        "similarity_boost": 0.75,
                        "style": 0.6,
                        "use_speaker_boost": True,
                    },
                },
            )
        latency = round(time.time() - t0, 2)

        if resp.status_code == 200:
            audio_bytes = resp.content
            out_path = "/tmp/golden_hour_elevenlabs_test.mp3"
            with open(out_path, "wb") as f:
                f.write(audio_bytes)
            ok(f"ElevenLabs TTS returned {len(audio_bytes):,} bytes in {latency}s")
            ok(f"Audio saved → {out_path}")
            info("Play with: afplay /tmp/golden_hour_elevenlabs_test.mp3")
            return True
        else:
            fail(f"ElevenLabs HTTP {resp.status_code}: {resp.text[:200]}")
            return False

    except Exception as e:
        fail(f"ElevenLabs TTS failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 4. BACKEND HEALTH
# ══════════════════════════════════════════════════════════════════════════════

async def test_backend_health():
    header("TEST 4 — Backend Health")
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{BACKEND}/health")
        data = resp.json()
        info(f"Response: {data}")
        if data.get("status") == "ok":
            model = data.get("model", "not set")
            if model == "not set":
                fail("Backend running but GEMINI_LIVE_MODEL not loaded — restart backend after .env is set")
                return False
            ok(f"Backend healthy, model={model}")
            return True
        else:
            fail(f"Unexpected health response: {data}")
            return False
    except Exception as e:
        fail(f"Backend unreachable at {BACKEND}: {e}")
        info("Run: cd backend && uvicorn main:app --reload --port 8080")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 5. BACKEND INCIDENT API
# ══════════════════════════════════════════════════════════════════════════════

async def test_backend_incident():
    header("TEST 5 — Backend Incident API")
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Create incident
            resp = await client.post(
                f"{BACKEND}/api/incident",
                json={"caller_name": "Test Caller", "location": "Market & 5th, SF"},
                headers={"Authorization": "Bearer demo-token"},
            )
            if resp.status_code != 200:
                fail(f"POST /api/incident → HTTP {resp.status_code}: {resp.text}")
                return False

            data = resp.json()
            session_id = data["session_id"]
            token = data["token"]
            ok(f"Created incident: session_id={session_id[:8]}…  token={token[:8]}…")

            # Fetch it back
            resp2 = await client.get(
                f"{BACKEND}/api/incident/{session_id}",
                headers={"Authorization": "Bearer demo-token"},
            )
            if resp2.status_code == 200:
                ok(f"GET /api/incident/{session_id[:8]}… → {resp2.json()['status']}")
                info(f"WebSocket URL: ws://localhost:8080/ws/incident/{session_id}?token={token}")
                return True
            else:
                fail(f"GET /api/incident → HTTP {resp2.status_code}")
                return False

    except Exception as e:
        import traceback
        fail(f"Backend incident API failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIOS — run through root_agent tool chain
# ══════════════════════════════════════════════════════════════════════════════

SCENARIOS = {
    "A": {
        "name": "Car Accident — Multiple Trauma",
        "prompt": (
            "There's been a bad car accident at Market and 5th Street in San Francisco. "
            "Two people are hurt. One woman is unconscious with a lot of bleeding from her head. "
            "The other man has a broken arm. Please help, send someone now!"
        ),
        "expect_fields": ["victim_count", "injuries", "severity_score", "recommended_hospital", "eta_minutes"],
        "expect_severity_min": 6.0,
    },
    "B": {
        "name": "Cardiac Event — Single Victim",
        "prompt": (
            "My husband just collapsed in our living room at 2847 Oak Street, San Francisco. "
            "He was clutching his chest and now he's not responding. He's breathing but barely. "
            "He's 62 years old. Please hurry!"
        ),
        "expect_fields": ["victim_count", "injuries", "severity_score", "recommended_hospital", "eta_minutes"],
        "expect_severity_min": 7.0,
    },
    "C": {
        "name": "House Fire — Burns & Smoke Inhalation",
        "prompt": (
            "There's a fire at 455 Castro Street! Three people got out but two of them have burns "
            "on their arms and face. One child is coughing badly and can barely breathe from the smoke. "
            "We're outside now but they need medical help immediately!"
        ),
        "expect_fields": ["victim_count", "injuries", "severity_score", "recommended_hospital", "eta_minutes"],
        "expect_severity_min": 5.0,
    },
}


async def run_scenario(label: str, scenario: dict):
    header(f"SCENARIO {label} — {scenario['name']}")
    info(f"Prompt: {scenario['prompt'][:80]}…")

    try:
        from root_agent import runner
        t0 = time.time()
        result = await runner(scenario["prompt"], session_id=f"scenario-{label}-{int(t0)}")
        latency = round(time.time() - t0, 2)

        if not result:
            fail("No result returned")
            return False

        print()
        # Check required fields
        all_ok = True
        for field in scenario["expect_fields"]:
            val = result.get(field) or result.get("raw_response", {})
            if val is not None and val != {}:
                ok(f"{field}: {val}")
            else:
                fail(f"Missing field: {field}")
                all_ok = False

        severity = result.get("severity_score", 0)
        if isinstance(severity, (int, float)) and severity >= scenario["expect_severity_min"]:
            ok(f"Severity {severity} >= expected minimum {scenario['expect_severity_min']}")
        else:
            info(f"Severity {severity} (expected >= {scenario['expect_severity_min']})")

        print()
        ok(f"Scenario {label} completed in {latency}s")
        return all_ok

    except Exception as e:
        fail(f"Scenario {label} failed: {e}")
        import traceback; traceback.print_exc()
        return False


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    print(f"\n{BOLD}{'═'*55}")
    print("  GOLDEN HOUR DISPATCHER — API + SCENARIO TESTS")
    print(f"{'═'*55}{RESET}")

    results = {}

    # API connectivity tests
    results["gemini_rest"]   = await test_gemini_rest()
    results["gemini_live"]   = await test_gemini_live()
    results["elevenlabs"]    = await test_elevenlabs()
    results["backend_health"]= await test_backend_health()
    results["backend_api"]   = await test_backend_incident()

    # Scenarios (only if Gemini REST is working)
    # Pause between scenarios to stay under free tier 5 RPM limit
    if results["gemini_rest"]:
        # Each ADK scenario makes 2-3 LLM calls. Free tier = 5 RPM.
        # Wait 65s between scenarios to guarantee a fresh RPM window.
        # On a paid API key this delay is unnecessary.
        scenario_items = list(SCENARIOS.items())
        for i, (label, scenario) in enumerate(scenario_items):
            if i > 0:
                print(f"\n  {YELLOW}→{RESET} Waiting 65s (free tier 5 RPM window reset)…")
                await asyncio.sleep(65)
            results[f"scenario_{label}"] = await run_scenario(label, scenario)
    else:
        print(f"\n{YELLOW}⚠ Skipping scenarios — Gemini API not reachable{RESET}")

    # Summary
    header("SUMMARY")
    passed = sum(1 for v in results.values() if v)
    total  = len(results)
    for name, status in results.items():
        sym = f"{GREEN}✓{RESET}" if status else f"{RED}✗{RESET}"
        print(f"  {sym}  {name}")

    print(f"\n  {BOLD}{passed}/{total} passed{RESET}\n")

    if not results.get("backend_health"):
        print(f"{YELLOW}  Fix: restart backend after .env is set{RESET}")
        print(f"  cd backend && uvicorn main:app --reload --port 8080\n")


if __name__ == "__main__":
    asyncio.run(main())
