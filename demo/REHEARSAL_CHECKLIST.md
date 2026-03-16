# Rehearsal & Pre-Demo Checklist

---

## 30 Minutes Before

- [ ] `cd golden-hour && cp .env.example .env` — confirm real keys are in `.env`, not example values
- [ ] `DEMO_MODE=false` confirmed in `.env`
- [ ] `VOICE_MODE=elevenlabs` confirmed in `.env`
- [ ] Run `make dev` — both backend (:8080) and frontend (:5173) start cleanly
- [ ] Open `http://localhost:5173` — both CallerView and ERDashboard render
- [ ] `curl http://localhost:8080/health` → `{"status":"ok"}`
- [ ] Test ElevenLabs manually: `python agents/voice_layer.py` — hear audio, confirm no error
- [ ] Test root agent: `python agents/root_agent.py` — get JSON output with all fields
- [ ] Confirm `demo/assets/incident_audio.pcm` exists (record from script below if not)
- [ ] Confirm `demo/assets/scene_frames/01.jpg` … `05.jpg` exist
- [ ] Plug in laptop power — do not run on battery during demo
- [ ] Browser zoom at 100%, dark mode on, bookmarks bar hidden
- [ ] Turn off Slack/Discord/email notifications

---

## 5 Minutes Before

- [ ] Pre-load tabs:
  - Tab 1: `http://localhost:5173` → **Caller View** (left half of screen)
  - Tab 2: `http://localhost:5173` → **ER Dashboard** (right half of screen, click "ER Dashboard" nav button)
  - Tab 3: `http://localhost:8080/health` (hidden — quick sanity check)
- [ ] Confirm `DEMO_MODE=false` one last time: `grep DEMO_MODE .env`
- [ ] Have terminal open, cd'd to project root, with `make demo` ready to paste
- [ ] Audio output: confirm laptop speakers work OR headset is connected
- [ ] Volume: set to ~60% — the ElevenLabs voice should be audible to judges

---

## Primary Backup: DEMO_MODE=true

If live APIs are unavailable or slow:

```bash
# In .env — change this one line:
DEMO_MODE=true

# Restart backend
make dev

# All Firestore, Pub/Sub, and ElevenLabs calls now use fixture files
# from demo/fixtures/ — everything still runs, no API calls made
```

Fixtures live in `demo/fixtures/` — pre-populate them with one good run:
```bash
# Run once when APIs are healthy:
python demo/incident_simulator.py --speed 1.0 2>&1 | tee demo/fixtures/capture.json
```

---

## Backup 2: VOICE_MODE=gemini (ElevenLabs slow)

If ElevenLabs API is experiencing latency:

```bash
# In .env:
VOICE_MODE=gemini

# Restart backend — MedicalAgent now uses Gemini native audio
```

Say in demo:
> "I've switched to Gemini's native audio output. The architecture is identical — the voice engine is a single environment variable. In production both run, with ElevenLabs as primary and Gemini as instant fallback."

This is a **feature**, not a failure. Judges see resilience by design.

---

## Backup 3: Full simulator (WebSocket + backend down)

```bash
python demo/incident_simulator.py --speed 1.0
```

Walk through terminal output verbally. All events print with timestamps.
The architecture still holds — you're narrating the pipeline instead of showing it.

---

## 911 Call Recording Script

Record this in a quiet room. Aim for ~90 seconds. Use a slightly stressed but coherent tone — not hysterical, but clearly scared.

---

> **[0s]** *(calling)* Hello? Hello, I need help. There's been a car accident — two cars just collided at Market and 5th Street in San Francisco.
>
> **[8s]** There are two people hurt. One of them — she's not responding. She's unconscious. There's blood on her head, a lot of blood. I don't know what to do.
>
> **[18s]** *(voice breaks slightly)* The other guy is awake but his arm looks — I think his arm is broken. He's in a lot of pain.
>
> **[26s]** Please, please send someone fast. She's not waking up. I tried talking to her—
>
> **[32s]** *(camera sound — turning to face the scene)*
>
> **[35s]** I'm sending you a video right now. Can you see it? There's glass everywhere.
>
> **[42s]** Should I — should I move her? I don't know if I should move her.
>
> **[50s]** *(AI voice begins — ElevenLabs fires here)*
>
> **[55s]** Okay. Okay. I'm pressing on the wound. With my jacket. Is that right?
>
> **[65s]** She's breathing. She's breathing — I can see her chest moving.
>
> **[72s]** Seven minutes. Okay. I can do seven minutes. I'll stay right here.
>
> **[80s]** Thank you. Thank you. I'm staying on the line.

---

**Recording tips:**
- Use your phone voice memo app, save as `.m4a`, convert to 16kHz mono PCM:
  ```bash
  ffmpeg -i recording.m4a -ar 16000 -ac 1 -f s16le demo/assets/incident_audio.pcm
  ```
- The pause at T+32s is intentional — silence triggers Gemini's affective attention
- The break in voice at T+18s is the panic cue that shifts dialog tone
