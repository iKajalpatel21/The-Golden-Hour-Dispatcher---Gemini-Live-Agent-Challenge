/**
 * CallerView — Gemini Live audio + scenario text buttons.
 *
 * Audio path:  Mic → ScriptProcessor → 16kHz PCM → Gemini Live → raw PCM back → speaker
 * Text path:   Scenario button / text input → Gemini Live text turn → audio back
 * ElevenLabs:  tool_result triggers MP3 audio from parallel agent
 */

import React, { useState, useRef, useCallback } from "react";

const BACKEND = import.meta.env.VITE_BACKEND_URL || "";
const WS_BASE  = BACKEND.replace(/^http/, "ws") || `ws://${location.host}`;

const SCENARIOS = [
  {
    label: "🚗 Car Accident",
    text:  "There's been a bad car accident at Market and 5th Street in San Francisco. Two people are hurt — one woman is unconscious with bleeding from her head, the other has a broken arm. Please help!",
  },
  {
    label: "❤️ Cardiac Arrest",
    text:  "My husband just collapsed in our living room at 2847 Oak Street, San Francisco. He was clutching his chest and now he's not responding. He's 62 years old. Please hurry!",
  },
  {
    label: "🔥 House Fire",
    text:  "There's a fire at 455 Castro Street! Three people got out but two have burns on their arms and face. One child is coughing badly from smoke and can barely breathe. We need help now!",
  },
];

export default function CallerView() {
  const [phase,      setPhase]      = useState("idle");
  const [status,     setStatus]     = useState("Press SOS to connect");
  const [transcript, setTranscript] = useState([]);
  const [inputText,  setInputText]  = useState("");
  const [voiceBadge, setVoiceBadge] = useState(null);

  const wsRef          = useRef(null);
  const streamRef      = useRef(null);
  const videoRef       = useRef(null);
  const canvasRef      = useRef(document.createElement("canvas"));
  const frameTimerRef  = useRef(null);
  const inputCtxRef    = useRef(null);   // 16kHz capture context
  const outputCtxRef   = useRef(null);   // playback context
  const processorRef   = useRef(null);
  const audioQueue     = useRef([]);
  const playingRef     = useRef(false);

  // ── PCM playback (Gemini Live sends raw 16-bit signed PCM @ 24kHz) ──────────
  function decodePCM(arrayBuffer) {
    const int16  = new Int16Array(arrayBuffer);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;
    if (!outputCtxRef.current || outputCtxRef.current.state === "closed") {
      outputCtxRef.current = new AudioContext({ sampleRate: 24000 });
    }
    const ctx = outputCtxRef.current;
    const buf = ctx.createBuffer(1, float32.length, 24000);
    buf.getChannelData(0).set(float32);
    return { ctx, buf };
  }

  async function playNextChunk() {
    if (playingRef.current || audioQueue.current.length === 0) return;
    playingRef.current = true;
    const { type, data } = audioQueue.current.shift();
    try {
      if (type === "audio_gemini") {
        // Raw PCM from Gemini Live
        const { ctx, buf } = decodePCM(data);
        const src = ctx.createBufferSource();
        src.buffer = buf;
        src.connect(ctx.destination);
        src.onended = () => { playingRef.current = false; playNextChunk(); };
        src.start();
        setVoiceBadge("Gemini Live");
      } else {
        // MP3 from ElevenLabs
        if (!outputCtxRef.current || outputCtxRef.current.state === "closed") {
          outputCtxRef.current = new AudioContext();
        }
        const ctx = outputCtxRef.current;
        const buf = await ctx.decodeAudioData(data.slice(0));
        const src = ctx.createBufferSource();
        src.buffer = buf;
        src.connect(ctx.destination);
        src.onended = () => { playingRef.current = false; playNextChunk(); };
        src.start();
        setVoiceBadge("ElevenLabs HD");
      }
    } catch (e) {
      console.warn("Audio play error:", e);
      playingRef.current = false;
      playNextChunk();
    }
  }

  function enqueueAudio(arrayBuffer, type) {
    audioQueue.current.push({ type, data: arrayBuffer });
    playNextChunk();
  }

  // ── Start call ──────────────────────────────────────────────────────────────
  const startCall = useCallback(async () => {
    setPhase("connecting");
    setStatus("Connecting…");
    setTranscript([]);

    try {
      const res = await fetch(`${BACKEND}/api/incident`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer demo-token" },
        body: JSON.stringify({ caller_name: "Caller" }),
      });
      const { session_id, token } = await res.json();

      // Camera + mic
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
      streamRef.current = stream;
      if (videoRef.current) videoRef.current.srcObject = stream;

      const ws = new WebSocket(`${WS_BASE}/ws/incident/${session_id}?token=${token}`);
      wsRef.current = ws;

      ws.onopen = () => {
        setPhase("active");
        setStatus("Connected — speak or pick a scenario");
        startMicCapture(stream);
        startFrameCapture();
      };

      ws.onmessage = async (evt) => {
        const msg = JSON.parse(evt.data);

        if (msg.type === "audio_gemini") {
          const bytes = Uint8Array.from(atob(msg.data), c => c.charCodeAt(0));
          enqueueAudio(bytes.buffer, "audio_gemini");
        }
        if (msg.type === "audio_elevenlabs") {
          const bytes = Uint8Array.from(atob(msg.data), c => c.charCodeAt(0));
          enqueueAudio(bytes.buffer, "audio_elevenlabs");
        }
        if (msg.type === "transcript") {
          setTranscript(prev => [...prev.slice(-30), msg.text]);
        }
        if (msg.type === "tool_result") {
          const eta = msg.data?.dispatch?.eta_minutes || msg.data?.eta_minutes;
          if (eta) setStatus(`Help is ${eta} min away — stay on the line`);
        }
        if (msg.type === "status")  setStatus(msg.text);
        if (msg.type === "error")   setStatus(`Error: ${msg.text}`);
      };

      ws.onclose = () => endCall();

    } catch (err) {
      setStatus(`Failed: ${err.message}`);
      setPhase("idle");
    }
  }, []);

  // ── Mic → 16kHz PCM → Gemini Live ──────────────────────────────────────────
  function startMicCapture(stream) {
    const ctx = new AudioContext({ sampleRate: 16000 });
    inputCtxRef.current = ctx;
    const src       = ctx.createMediaStreamSource(stream);
    const processor = ctx.createScriptProcessor(1600, 1, 1);
    processorRef.current = processor;

    processor.onaudioprocess = (e) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;
      const f32  = e.inputBuffer.getChannelData(0);
      const i16  = new Int16Array(f32.length);
      for (let i = 0; i < f32.length; i++)
        i16[i] = Math.max(-32768, Math.min(32767, f32[i] * 32768));
      // Convert to base64
      const bytes = new Uint8Array(i16.buffer);
      let b64 = "";
      const chunk = 0x8000;
      for (let i = 0; i < bytes.length; i += chunk)
        b64 += String.fromCharCode(...bytes.subarray(i, i + chunk));
      wsRef.current.send(JSON.stringify({ type: "audio", data: btoa(b64) }));
    };

    src.connect(processor);
    processor.connect(ctx.destination);
  }

  // ── Camera → 1fps JPEG ─────────────────────────────────────────────────────
  function startFrameCapture() {
    const canvas = canvasRef.current;
    canvas.width = 320; canvas.height = 240;
    frameTimerRef.current = setInterval(() => {
      if (!videoRef.current || wsRef.current?.readyState !== WebSocket.OPEN) return;
      canvas.getContext("2d").drawImage(videoRef.current, 0, 0, 320, 240);
      canvas.toBlob(blob => {
        if (!blob) return;
        const reader = new FileReader();
        reader.onloadend = () => {
          wsRef.current?.send(JSON.stringify({ type: "image", data: reader.result.split(",")[1], mime: "image/jpeg" }));
        };
        reader.readAsDataURL(blob);
      }, "image/jpeg", 0.6);
    }, 2000);
  }

  // ── Send text to Gemini Live ────────────────────────────────────────────────
  const sendText = useCallback((text) => {
    if (!text.trim() || wsRef.current?.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "text_query", text }));
    setInputText("");
  }, []);

  // ── End call ────────────────────────────────────────────────────────────────
  const endCall = useCallback(() => {
    clearInterval(frameTimerRef.current);
    processorRef.current?.disconnect();
    inputCtxRef.current?.close();
    outputCtxRef.current?.close();
    streamRef.current?.getTracks().forEach(t => t.stop());
    wsRef.current?.close();
    setPhase("ended");
    setStatus("Call ended");
    setVoiceBadge(null);
  }, []);

  // ── Styles ──────────────────────────────────────────────────────────────────
  const S = {
    wrap:        { minHeight: "100vh", background: "#0a0a0a", color: "#fff", display: "flex", flexDirection: "column", alignItems: "center", padding: "24px 16px", boxSizing: "border-box" },
    header:      { fontSize: 13, color: "#888", letterSpacing: 2, marginBottom: 8 },
    statusBar:   { background: phase === "active" ? "#1a3a1a" : phase === "connecting" ? "#2a2a0a" : "#1a1a1a", border: `1px solid ${phase === "active" ? "#2ecc71" : "#444"}`, borderRadius: 8, padding: "8px 20px", fontSize: 14, color: phase === "active" ? "#2ecc71" : "#aaa", marginBottom: 20, textAlign: "center", minWidth: 300, display: "flex", alignItems: "center", justifyContent: "center", gap: 10 },
    badge:       { fontSize: 11, background: voiceBadge === "ElevenLabs HD" ? "#6c3483" : "#1a5276", padding: "2px 8px", borderRadius: 10, color: "#ddd" },
    sosBtn:      { width: 160, height: 160, borderRadius: "50%", background: "linear-gradient(145deg,#e74c3c,#c0392b)", color: "#fff", fontSize: 32, fontWeight: 900, border: "4px solid #e74c3c", cursor: "pointer", boxShadow: "0 0 40px rgba(231,76,60,.5)", letterSpacing: 2 },
    endBtn:      { padding: "10px 32px", background: "#333", color: "#e74c3c", border: "2px solid #e74c3c", borderRadius: 30, fontSize: 15, fontWeight: 700, cursor: "pointer", marginBottom: 16 },
    scenarioRow: { display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "center", marginBottom: 12, maxWidth: 480 },
    scenarioBtn: { padding: "8px 14px", background: "#1a2a3a", border: "1px solid #2a4a6a", borderRadius: 20, color: "#7bc8f6", fontSize: 13, cursor: "pointer", fontWeight: 600 },
    inputRow:    { display: "flex", gap: 8, width: "100%", maxWidth: 480, marginBottom: 20 },
    input:       { flex: 1, background: "#1a1a1a", border: "1px solid #444", borderRadius: 8, color: "#fff", padding: "10px 14px", fontSize: 14, outline: "none" },
    sendBtn:     { padding: "10px 20px", background: inputText.trim() ? "#e74c3c" : "#333", border: "none", borderRadius: 8, color: "#fff", fontSize: 14, fontWeight: 700, cursor: inputText.trim() ? "pointer" : "not-allowed" },
    txBox:       { width: "100%", maxWidth: 480, background: "#111", border: "1px solid #333", borderRadius: 10, padding: 14, maxHeight: 260, overflowY: "auto" },
    newCallBtn:  { marginTop: 24, padding: "10px 28px", background: "#222", color: "#aaa", border: "1px solid #444", borderRadius: 8, cursor: "pointer" },
  };

  return (
    <div style={S.wrap}>
      <div style={S.header}>GOLDEN HOUR DISPATCHER</div>

      <div style={S.statusBar}>
        {status}
        {voiceBadge && <span style={S.badge}>{voiceBadge}</span>}
      </div>

      <video ref={videoRef} autoPlay muted playsInline
        style={{ width: "100%", maxWidth: 340, borderRadius: 12, background: "#111", border: "1px solid #333", marginBottom: 16, display: phase === "active" ? "block" : "none" }}
      />

      {phase === "idle"       && <button style={S.sosBtn} onClick={startCall}>SOS</button>}
      {phase === "connecting" && <div style={{ fontSize: 48 }}>⏳</div>}

      {phase === "active" && (
        <>
          <button style={S.endBtn} onClick={endCall}>End Call</button>

          <div style={{ fontSize: 11, color: "#666", letterSpacing: 1, marginBottom: 8 }}>QUICK SCENARIOS</div>
          <div style={S.scenarioRow}>
            {SCENARIOS.map(sc => (
              <button key={sc.label} style={S.scenarioBtn} onClick={() => sendText(sc.text)}>
                {sc.label}
              </button>
            ))}
          </div>

          <div style={S.inputRow}>
            <input
              style={S.input}
              placeholder="Or describe the emergency…"
              value={inputText}
              onChange={e => setInputText(e.target.value)}
              onKeyDown={e => e.key === "Enter" && sendText(inputText)}
            />
            <button style={S.sendBtn} onClick={() => sendText(inputText)} disabled={!inputText.trim()}>
              Send
            </button>
          </div>
        </>
      )}

      {transcript.length > 0 && (
        <div style={{ width: "100%", maxWidth: 480 }}>
          <div style={{ fontSize: 11, color: "#666", letterSpacing: 1, marginBottom: 6 }}>LIVE TRANSCRIPT</div>
          <div style={S.txBox}>
            {transcript.map((line, i) => (
              <p key={i} style={{ margin: "4px 0", fontSize: 14, lineHeight: 1.5,
                color: line.startsWith("You:") || line.startsWith("Caller:") ? "#7bc8f6"
                     : line.startsWith("Dispatcher:") ? "#2ecc71" : "#aaa" }}>
                {line}
              </p>
            ))}
          </div>
        </div>
      )}

      {phase === "ended" && (
        <button style={S.newCallBtn}
          onClick={() => { setPhase("idle"); setTranscript([]); setStatus("Press SOS to connect"); }}>
          New Call
        </button>
      )}
    </div>
  );
}
