"""
Demo replay engine — replays a pre-recorded 911 incident through the live pipeline.

Usage:
    python demo/incident_simulator.py --speed 1.0

Requirements:
    demo/assets/incident_audio.pcm   (90s, 16kHz mono PCM, 16-bit LE)
    demo/assets/scene_frames/        (5 JPEG images: 01.jpg … 05.jpg)

If assets are missing, the simulator generates synthetic stand-ins automatically.

Timeline:
  T+0s  — WebSocket connects, audio stream starts
  T+15s — Panic audio cue → Gemini affective dialog detects distress
  T+30s — Scene frame with wound → vision triage fires
  T+45s — ParallelAgent triggers (dispatch + medical + ER simultaneously)
  T+50s — ElevenLabs voice delivers first aid instructions
  T+60s — ER dashboard populates
  T+75s — Ambulance pin starts moving
"""

import os
import sys
import asyncio
import argparse
import base64
import json
import struct
import time
from pathlib import Path

import websockets
from dotenv import load_dotenv

load_dotenv()

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8080")
WS_BASE     = BACKEND_URL.replace("http", "ws")

ASSETS_DIR = Path(__file__).parent / "assets"
FRAMES_DIR = ASSETS_DIR / "scene_frames"
AUDIO_FILE = ASSETS_DIR / "incident_audio.pcm"


# ── Synthetic asset generators (used when real assets are missing) ─────────────

def generate_synthetic_pcm(duration_s: int = 90, sample_rate: int = 16000) -> bytes:
    """Generate a sine wave PCM file as a stand-in for the real audio."""
    import math
    samples = []
    for i in range(duration_s * sample_rate):
        t = i / sample_rate
        # Mix 440Hz + 880Hz to simulate speech-like complexity
        val = int(32767 * 0.3 * (math.sin(2 * math.pi * 440 * t) +
                                  math.sin(2 * math.pi * 880 * t)))
        samples.append(struct.pack("<h", val))
    return b"".join(samples)


def generate_synthetic_jpeg(frame_num: int) -> bytes:
    """Generate a minimal valid JPEG as a stand-in for real scene photos."""
    # 1×1 white JPEG — smallest valid file
    return bytes([
        0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00,
        0x01, 0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB,
        0x00, 0x43, 0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08, 0x07,
        0x07, 0x07, 0x09, 0x09, 0x08, 0x0A, 0x0C, 0x14, 0x0D, 0x0C, 0x0B,
        0x0B, 0x0C, 0x19, 0x12, 0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E,
        0x1D, 0x1A, 0x1C, 0x1C, 0x20, 0x24, 0x2E, 0x27, 0x20, 0x22, 0x2C,
        0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29, 0x2C, 0x30, 0x31, 0x34, 0x34,
        0x34, 0x1F, 0x27, 0x39, 0x3D, 0x38, 0x32, 0x3C, 0x2E, 0x33, 0x34,
        0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01, 0x00, 0x01, 0x01,
        0x01, 0x11, 0x00, 0xFF, 0xC4, 0x00, 0x1F, 0x00, 0x00, 0x01, 0x05,
        0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
        0x09, 0x0A, 0x0B, 0xFF, 0xC4, 0x00, 0xB5, 0x10, 0x00, 0x02, 0x01,
        0x03, 0x03, 0x02, 0x04, 0x03, 0x05, 0x05, 0x04, 0x04, 0x00, 0x00,
        0x01, 0x7D, 0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21,
        0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32,
        0x81, 0x91, 0xA1, 0x08, 0x23, 0x42, 0xB1, 0xC1, 0x15, 0x52, 0xD1,
        0xF0, 0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0A, 0x16, 0x17, 0x18,
        0x19, 0x1A, 0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x34, 0x35, 0x36,
        0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49,
        0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5A, 0x63, 0x64,
        0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75, 0x76, 0x77,
        0x78, 0x79, 0x7A, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8A,
        0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3, 0xA4,
        0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6,
        0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8,
        0xC9, 0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA,
        0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF1,
        0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA, 0xFF, 0xDA,
        0x00, 0x08, 0x01, 0x01, 0x00, 0x00, 0x3F, 0x00, 0xFB, 0xD2, 0x8A,
        0x28, 0x03, 0xFF, 0xD9,
    ])


def ensure_assets():
    """Create synthetic assets if real ones are missing."""
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    if not AUDIO_FILE.exists():
        print("[SIM] Generating synthetic PCM audio (real file: demo/assets/incident_audio.pcm)")
        AUDIO_FILE.write_bytes(generate_synthetic_pcm(90))

    for i in range(1, 6):
        frame_path = FRAMES_DIR / f"{i:02d}.jpg"
        if not frame_path.exists():
            print(f"[SIM] Generating synthetic frame {i} (real files: demo/assets/scene_frames/)")
            frame_path.write_bytes(generate_synthetic_jpeg(i))


# ── Simulator ──────────────────────────────────────────────────────────────────

TIMELINE = [
    (0,  "audio_start",  "Audio stream begins"),
    (15, "panic_cue",    "Panic audio cue — affective dialog activates"),
    (30, "scene_frame",  "Wound visible — vision triage fires"),
    (45, "parallel",     "ParallelAgent executes (dispatch + medical + ER)"),
    (50, "elevenlabs",   "ElevenLabs HD voice delivers first aid"),
    (60, "er_dashboard", "ER dashboard populates"),
    (75, "ambulance",    "Ambulance pin starts moving"),
]


async def run_simulation(speed: float = 1.0):
    ensure_assets()

    print("\n╔══════════════════════════════════════════╗")
    print("║     GOLDEN HOUR DEMO SIMULATOR           ║")
    print(f"║     Speed: {speed}x                         ║")
    print("╚══════════════════════════════════════════╝\n")

    # 1. Create incident session
    import httpx
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{BACKEND_URL}/api/incident",
            json={"caller_name": "Demo Caller"},
            headers={"Authorization": "Bearer demo-token"},
        )
        if resp.status_code != 200:
            print(f"[ERROR] Could not create session: {resp.text}")
            sys.exit(1)
        data = resp.json()
        session_id = data["session_id"]
        token = data["token"]

    print(f"[SIM] Session: {session_id}")
    print(f"[SIM] Connecting WebSocket…\n")

    # 2. Load assets
    pcm_data = AUDIO_FILE.read_bytes()
    frames = sorted(FRAMES_DIR.glob("*.jpg"))

    # PCM chunks: 100ms at 16kHz = 3200 bytes (int16 × 1600)
    CHUNK_SIZE = 3200
    chunks = [pcm_data[i:i + CHUNK_SIZE] for i in range(0, len(pcm_data), CHUNK_SIZE)]
    chunk_interval = 0.1 / speed   # 100ms real-time / speed

    frame_iter = iter(frames)

    ws_url = f"{WS_BASE}/ws/incident/{session_id}?token={token}"

    async with websockets.connect(ws_url) as ws:
        start_time = time.monotonic()

        async def send_audio():
            for chunk in chunks:
                b64 = base64.b64encode(chunk).decode()
                await ws.send(json.dumps({"type": "audio", "data": b64}))
                await asyncio.sleep(chunk_interval)

        async def inject_events():
            for (t_secs, event_type, label) in TIMELINE:
                wait = t_secs / speed
                await asyncio.sleep(wait)
                elapsed = time.monotonic() - start_time
                print(f"  T+{t_secs:02d}s [{elapsed:.1f}s real] → {label}")

                if event_type == "scene_frame":
                    frame_path = next(frame_iter, frames[0])
                    img_bytes = frame_path.read_bytes()
                    b64 = base64.b64encode(img_bytes).decode()
                    await ws.send(json.dumps({"type": "image", "data": b64, "mime": "image/jpeg"}))

                elif event_type == "parallel":
                    # Inject text trigger for ParallelAgent
                    await ws.send(json.dumps({
                        "type": "text",
                        "data": (
                            "Two victims: one unconscious with head bleeding, one with broken arm. "
                            "Location: Market and 5th Street, San Francisco. Please dispatch now."
                        ),
                    }))

        async def receive_events():
            async for raw in ws:
                msg = json.loads(raw)
                mtype = msg.get("type", "")
                if mtype == "transcript":
                    print(f"  [AI] {msg['text']}")
                elif mtype == "tool_result":
                    eta = msg.get("data", {}).get("dispatch", {}).get("eta_minutes")
                    if eta:
                        print(f"  [DISPATCH] Ambulance ETA: {eta} min")
                elif mtype == "audio_elevenlabs":
                    print("  [ELEVENLABS] HD audio received — streaming to caller")
                elif mtype == "audio_gemini":
                    pass  # suppress noise
                elif mtype == "error":
                    print(f"  [ERROR] {msg.get('text')}")

        await asyncio.gather(
            send_audio(),
            inject_events(),
            receive_events(),
        )

    print("\n[SIM] Demo replay complete.\n")


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Golden Hour Demo Simulator")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Playback speed multiplier (e.g. 2.0 = 2x faster)")
    args = parser.parse_args()
    asyncio.run(run_simulation(speed=args.speed))
