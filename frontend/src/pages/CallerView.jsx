/**
 * CallerView — mobile-first emergency caller UI.
 *
 * Flow:
 *  1. Press SOS → getUserMedia (mic + camera)
 *  2. WebSocket /ws/incident/{sessionId}?token=...
 *  3. Stream 16kHz PCM audio (100ms chunks) + 1fps JPEG frames over WS
 *  4. Play back Gemini native audio OR ElevenLabs HD audio through AudioContext
 *  5. Show live transcript + status bar
 */

import React, { useState, useRef, useEffect, useCallback } from "react";

const BACKEND = import.meta.env.VITE_BACKEND_URL || "";
const WS_BASE = BACKEND.replace(/^http/, "ws") || `ws://${location.host}`;
const CHUNK_MS = 100;    // audio chunk interval
const FRAME_FPS = 1;     // video frame rate

export default function CallerView() {
  const [phase, setPhase] = useState("idle");   // idle | connecting | active | ended
  const [status, setStatus] = useState("Press SOS to connect");
  const [transcript, setTranscript] = useState([]);
  const [voiceBadge, setVoiceBadge] = useState(null); // "Gemini Live" | "ElevenLabs HD"

  const videoRef      = useRef(null);
  const wsRef         = useRef(null);
  const streamRef     = useRef(null);
  const audioCtxRef   = useRef(null);
  const processorRef  = useRef(null);
  const frameTimerRef = useRef(null);
  const canvasRef     = useRef(document.createElement("canvas"));

  // ── Audio playback queue ──────────────────────────────────────────────────
  const audioQueue = useRef([]);
  const playingRef = useRef(false);

  async function playNextChunk() {
    if (playingRef.current || audioQueue.current.length === 0) return;
    playingRef.current = true;
    const { data, type } = audioQueue.current.shift();
    try {
      const ctx = audioCtxRef.current || new AudioContext({ sampleRate: 24000 });
      audioCtxRef.current = ctx;
      const buf = await ctx.decodeAudioData(data.slice(0));
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(ctx.destination);
      src.onended = () => { playingRef.current = false; playNextChunk(); };
      src.start();
      setVoiceBadge(type === "audio_elevenlabs" ? "ElevenLabs HD" : "Gemini Live");
    } catch {
      playingRef.current = false;
      playNextChunk();
    }
  }

  function enqueueAudio(arrayBuffer, type) {
    audioQueue.current.push({ data: arrayBuffer, type });
    playNextChunk();
  }

  // ── Start call ─────────────────────────────────────────────────────────────
  const startCall = useCallback(async () => {
    setPhase("connecting");
    setStatus("Connecting…");

    try {
      // 1. Create incident session
      const res = await fetch(`${BACKEND}/api/incident`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer demo-token",
        },
        body: JSON.stringify({ caller_name: "Caller" }),
      });
      const { session_id, token } = await res.json();

      // 2. Get media
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }

      // 3. Open WebSocket
      const ws = new WebSocket(`${WS_BASE}/ws/incident/${session_id}?token=${token}`);
      wsRef.current = ws;

      ws.onopen = () => {
        setPhase("active");
        setStatus("Connected — speak now");
        startAudioStreaming(stream);
        startFrameCapture();
      };

      ws.onmessage = (evt) => {
        const msg = JSON.parse(evt.data);

        if (msg.type === "audio_gemini" || msg.type === "audio_elevenlabs") {
          const bytes = Uint8Array.from(atob(msg.data), c => c.charCodeAt(0));
          enqueueAudio(bytes.buffer, msg.type);
        }

        if (msg.type === "transcript") {
          setTranscript(prev => [...prev.slice(-20), msg.text]);
        }

        if (msg.type === "tool_result") {
          const eta = msg.data?.dispatch?.eta_minutes;
          if (eta) setStatus(`Help is ${eta} min away`);
        }

        if (msg.type === "status") {
          setStatus(msg.text);
        }

        if (msg.type === "error") {
          setStatus(`Error: ${msg.text}`);
        }
      };

      ws.onclose = () => endCall();

    } catch (err) {
      setStatus(`Failed: ${err.message}`);
      setPhase("idle");
    }
  }, []);

  // ── Audio streaming: 16kHz PCM, 100ms chunks ──────────────────────────────
  function startAudioStreaming(stream) {
    const ctx = new AudioContext({ sampleRate: 16000 });
    audioCtxRef.current = ctx;
    const src = ctx.createMediaStreamSource(stream);
    const processor = ctx.createScriptProcessor(1600, 1, 1); // 100ms at 16kHz
    processorRef.current = processor;

    processor.onaudioprocess = (e) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;
      const float32 = e.inputBuffer.getChannelData(0);
      const int16 = new Int16Array(float32.length);
      for (let i = 0; i < float32.length; i++) {
        int16[i] = Math.max(-32768, Math.min(32767, float32[i] * 32768));
      }
      const b64 = btoa(String.fromCharCode(...new Uint8Array(int16.buffer)));
      wsRef.current.send(JSON.stringify({ type: "audio", data: b64 }));
    };

    src.connect(processor);
    processor.connect(ctx.destination);
  }

  // ── Frame capture: 1fps JPEG ~50KB ────────────────────────────────────────
  function startFrameCapture() {
    const canvas = canvasRef.current;
    canvas.width = 320;
    canvas.height = 240;

    frameTimerRef.current = setInterval(() => {
      if (!videoRef.current || wsRef.current?.readyState !== WebSocket.OPEN) return;
      const ctx2d = canvas.getContext("2d");
      ctx2d.drawImage(videoRef.current, 0, 0, 320, 240);
      canvas.toBlob(blob => {
        if (!blob) return;
        const reader = new FileReader();
        reader.onloadend = () => {
          const b64 = reader.result.split(",")[1];
          wsRef.current?.send(JSON.stringify({ type: "image", data: b64, mime: "image/jpeg" }));
        };
        reader.readAsDataURL(blob);
      }, "image/jpeg", 0.6);
    }, 1000 / FRAME_FPS);
  }

  // ── End call ──────────────────────────────────────────────────────────────
  const endCall = useCallback(() => {
    clearInterval(frameTimerRef.current);
    processorRef.current?.disconnect();
    audioCtxRef.current?.close();
    streamRef.current?.getTracks().forEach(t => t.stop());
    wsRef.current?.close();
    setPhase("ended");
    setStatus("Call ended");
    setVoiceBadge(null);
  }, []);

  useEffect(() => () => endCall(), []);

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div style={{
      minHeight: "100vh",
      background: "#0a0a0a",
      color: "#fff",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      padding: "24px 16px",
      boxSizing: "border-box",
    }}>

      {/* Header */}
      <div style={{ fontSize: 13, color: "#888", letterSpacing: 2, marginBottom: 8 }}>
        GOLDEN HOUR DISPATCHER
      </div>

      {/* Status bar */}
      <div style={{
        background: phase === "active" ? "#1a3a1a" : phase === "connecting" ? "#2a2a0a" : "#1a1a1a",
        border: `1px solid ${phase === "active" ? "#2ecc71" : "#444"}`,
        borderRadius: 8,
        padding: "8px 16px",
        fontSize: 14,
        color: phase === "active" ? "#2ecc71" : "#aaa",
        marginBottom: 24,
        textAlign: "center",
        minWidth: 260,
      }}>
        {status}
        {voiceBadge && (
          <span style={{
            marginLeft: 10,
            fontSize: 11,
            background: voiceBadge === "ElevenLabs HD" ? "#6c3483" : "#1a5276",
            padding: "2px 8px",
            borderRadius: 10,
            color: "#ddd",
          }}>
            {voiceBadge}
          </span>
        )}
      </div>

      {/* Video preview */}
      <video
        ref={videoRef}
        autoPlay
        muted
        playsInline
        style={{
          width: "100%",
          maxWidth: 340,
          borderRadius: 12,
          background: "#111",
          border: "1px solid #333",
          marginBottom: 24,
          display: phase === "active" ? "block" : "none",
        }}
      />

      {/* SOS Button */}
      {phase === "idle" && (
        <button
          onClick={startCall}
          style={{
            width: 160,
            height: 160,
            borderRadius: "50%",
            background: "linear-gradient(145deg, #e74c3c, #c0392b)",
            color: "#fff",
            fontSize: 32,
            fontWeight: 900,
            border: "4px solid #e74c3c",
            cursor: "pointer",
            boxShadow: "0 0 40px rgba(231,76,60,0.5)",
            letterSpacing: 2,
          }}
        >
          SOS
        </button>
      )}

      {/* Connecting spinner */}
      {phase === "connecting" && (
        <div style={{ fontSize: 48 }}>⏳</div>
      )}

      {/* End call button */}
      {phase === "active" && (
        <button
          onClick={endCall}
          style={{
            padding: "12px 36px",
            background: "#333",
            color: "#e74c3c",
            border: "2px solid #e74c3c",
            borderRadius: 30,
            fontSize: 16,
            fontWeight: 700,
            cursor: "pointer",
            marginBottom: 24,
          }}
        >
          End Call
        </button>
      )}

      {/* Live transcript */}
      {transcript.length > 0 && (
        <div style={{
          width: "100%",
          maxWidth: 400,
          marginTop: 16,
        }}>
          <div style={{ fontSize: 11, color: "#666", letterSpacing: 1, marginBottom: 6 }}>
            AI DISPATCHER
          </div>
          <div style={{
            background: "#111",
            border: "1px solid #333",
            borderRadius: 10,
            padding: 14,
            maxHeight: 180,
            overflowY: "auto",
          }}>
            {transcript.map((line, i) => (
              <p key={i} style={{
                margin: "4px 0",
                fontSize: 14,
                color: i === transcript.length - 1 ? "#fff" : "#888",
                lineHeight: 1.5,
              }}>
                {line}
              </p>
            ))}
          </div>
        </div>
      )}

      {/* Ended state */}
      {phase === "ended" && (
        <button
          onClick={() => { setPhase("idle"); setTranscript([]); setStatus("Press SOS to connect"); }}
          style={{
            marginTop: 24,
            padding: "10px 28px",
            background: "#222",
            color: "#aaa",
            border: "1px solid #444",
            borderRadius: 8,
            cursor: "pointer",
          }}
        >
          New Call
        </button>
      )}
    </div>
  );
}
