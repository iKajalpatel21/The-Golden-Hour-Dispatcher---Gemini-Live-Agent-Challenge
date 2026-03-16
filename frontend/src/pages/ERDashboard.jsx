/**
 * ERDashboard — hospital command view.
 *
 * Dark mode, high-contrast, CSS variables, projector-ready.
 *
 * Features:
 *  - react-leaflet map: red pin (incident) + blue animated pin (ambulance)
 *  - Live incident summary from /api/incident/:id
 *  - AI preparation checklist
 *  - Live audio transcript
 *  - Alert Specialist button
 *  - Voice engine badge (Gemini Live / ElevenLabs HD)
 *  - /demo/replay button
 */

import React, { useState, useEffect, useRef, useCallback } from "react";
import { MapContainer, TileLayer, Marker, Popup, useMap } from "react-leaflet";
import L from "leaflet";

// ── CSS variables injected once ───────────────────────────────────────────────
const cssVars = `
  :root {
    --bg: #0d1117;
    --surface: #161b22;
    --border: #30363d;
    --red: #f85149;
    --green: #3fb950;
    --blue: #58a6ff;
    --purple: #bc8cff;
    --text: #e6edf3;
    --muted: #8b949e;
    --warn: #d29922;
  }
`;

const BACKEND = import.meta.env.VITE_BACKEND_URL || "";
const WS_BASE  = BACKEND.replace(/^http/, "ws") || `ws://${location.host}`;

// Leaflet icon fix for bundlers
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  iconUrl:       "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  shadowUrl:     "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
});

const redIcon = new L.Icon({
  iconUrl: "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-red.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41], iconAnchor: [12, 41],
});

const blueIcon = new L.Icon({
  iconUrl: "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-blue.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41], iconAnchor: [12, 41],
});

// Animate ambulance marker on the map
function AmbulanceMarker({ position }) {
  const markerRef = useRef(null);
  useEffect(() => {
    if (markerRef.current) {
      markerRef.current.setLatLng(position);
    }
  }, [position]);
  return <Marker ref={markerRef} position={position} icon={blueIcon}>
    <Popup>Ambulance AMB-042</Popup>
  </Marker>;
}

export default function ERDashboard() {
  const [incident, setIncident]       = useState(null);
  const [transcript, setTranscript]   = useState([]);
  const [voiceBadge, setVoiceBadge]   = useState(null);
  const [ambulancePos, setAmbulancePos] = useState([37.785, -122.411]);
  const [status, setStatus]           = useState("Waiting for incident…");
  const [sessionId, setSessionId]     = useState(null);

  const wsRef    = useRef(null);
  const pollRef  = useRef(null);

  // Demo incident position (SF Market & 5th)
  const INCIDENT_POS = [37.7749, -122.4194];

  // ── Poll Firestore for incident updates ───────────────────────────────────
  const pollIncident = useCallback(async (sid) => {
    try {
      const res = await fetch(`${BACKEND}/api/incident/${sid}`, {
        headers: { Authorization: "Bearer demo-token" },
      });
      if (res.ok) {
        const data = await res.json();
        setIncident(data);
        if (data.dispatch?.eta_minutes) {
          setStatus(`Ambulance ETA: ${data.dispatch.eta_minutes} min`);
        }
      }
    } catch { /* silent */ }
  }, []);

  // ── WebSocket listener for live data ──────────────────────────────────────
  const connectWS = useCallback((sid, token) => {
    const ws = new WebSocket(`${WS_BASE}/ws/incident/${sid}?token=${token}`);
    wsRef.current = ws;

    ws.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);

      if (msg.type === "transcript") {
        setTranscript(prev => [...prev.slice(-30), msg.text]);
      }

      if (msg.type === "audio_elevenlabs") {
        setVoiceBadge("ElevenLabs HD");
        setTimeout(() => setVoiceBadge(null), 8000);
      }
      if (msg.type === "audio_gemini") {
        setVoiceBadge("Gemini Live");
        setTimeout(() => setVoiceBadge(null), 4000);
      }

      if (msg.type === "tool_result") {
        const d = msg.data;
        setIncident(prev => ({ ...prev, ...d }));
        if (d?.dispatch?.eta_minutes) {
          setStatus(`Ambulance ETA: ${d.dispatch.eta_minutes} min`);
          // Animate ambulance toward incident
          animateAmbulance(INCIDENT_POS, d.dispatch.eta_minutes);
        }
      }
    };
  }, []);

  // ── Animate ambulance pin ─────────────────────────────────────────────────
  function animateAmbulance(target, etaMinutes) {
    const steps = etaMinutes * 10; // ~10 updates per minute
    let step = 0;
    const start = ambulancePos.slice();
    const timer = setInterval(() => {
      step++;
      const t = step / steps;
      setAmbulancePos([
        start[0] + (target[0] - start[0]) * t,
        start[1] + (target[1] - start[1]) * t,
      ]);
      if (step >= steps) clearInterval(timer);
    }, 6000); // every 6s = 1 minute wall-clock simulated
  }

  // ── Demo replay ───────────────────────────────────────────────────────────
  const startDemoReplay = async () => {
    try {
      const res = await fetch(`${BACKEND}/demo/simulate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ speed: 1.0 }),
      });
      const { session_id } = await res.json();
      setSessionId(session_id);
      setStatus("Demo replay started…");
      pollRef.current = setInterval(() => pollIncident(session_id), 3000);
    } catch (err) {
      setStatus(`Demo error: ${err.message}`);
    }
  };

  // ── Alert specialist ──────────────────────────────────────────────────────
  const alertSpecialist = async () => {
    if (!incident?.session_id) return;
    await fetch(`${BACKEND}/api/hospital/SFGH-001/notify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer demo-token",
      },
      body: JSON.stringify({
        hospital_id: "SFGH-001",
        summary: incident,
      }),
    });
    setStatus("Specialist alerted via Pub/Sub");
  };

  useEffect(() => () => {
    clearInterval(pollRef.current);
    wsRef.current?.close();
  }, []);

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <>
      <style>{cssVars}</style>
      <div style={{
        minHeight: "100vh",
        background: "var(--bg)",
        color: "var(--text)",
        padding: 20,
        boxSizing: "border-box",
        fontFamily: "system-ui, sans-serif",
      }}>

        {/* Header row */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
          <div>
            <div style={{ fontSize: 11, color: "var(--muted)", letterSpacing: 2 }}>SF GENERAL HOSPITAL</div>
            <div style={{ fontSize: 22, fontWeight: 800, color: "var(--red)" }}>INCIDENT COMMAND</div>
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            {voiceBadge && (
              <span style={{
                fontSize: 12,
                padding: "4px 10px",
                borderRadius: 20,
                background: voiceBadge === "ElevenLabs HD" ? "#6c3483" : "#1a5276",
                color: "#ddd",
                fontWeight: 600,
              }}>
                {voiceBadge}
              </span>
            )}
            <button
              onClick={startDemoReplay}
              style={btnStyle("var(--blue)")}
            >
              Demo Replay
            </button>
            <button
              onClick={alertSpecialist}
              style={btnStyle("var(--warn)")}
            >
              Alert Specialist
            </button>
          </div>
        </div>

        {/* Status bar */}
        <div style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderLeft: "4px solid var(--green)",
          borderRadius: 8,
          padding: "10px 16px",
          fontSize: 14,
          color: "var(--green)",
          marginBottom: 20,
        }}>
          {status}
        </div>

        {/* Main grid */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>

          {/* Map */}
          <div style={cardStyle()}>
            <SectionTitle>Live Scene Map</SectionTitle>
            <div style={{ height: 300, borderRadius: 8, overflow: "hidden" }}>
              <MapContainer
                center={INCIDENT_POS}
                zoom={14}
                style={{ height: "100%", width: "100%" }}
              >
                <TileLayer
                  url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                  attribution="© OpenStreetMap"
                />
                <Marker position={INCIDENT_POS} icon={redIcon}>
                  <Popup>Incident Location</Popup>
                </Marker>
                <AmbulanceMarker position={ambulancePos} />
              </MapContainer>
            </div>
          </div>

          {/* Incident summary */}
          <div style={cardStyle()}>
            <SectionTitle>Incident Summary</SectionTitle>
            {incident ? (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
                <tbody>
                  <Row label="Caller"      value={incident.caller_name || "Unknown"} />
                  <Row label="Victims"     value={incident.victim_count ?? "—"} />
                  <Row label="Injuries"    value={(incident.injuries || []).join(", ") || "—"} />
                  <Row label="Severity"    value={incident.severity_score != null ? `${incident.severity_score}/10` : "—"}
                       color={incident.severity_score > 7 ? "var(--red)" : "var(--warn)"} />
                  <Row label="Hospital"    value={incident.recommended_hospital || "—"} />
                  <Row label="ETA"         value={incident.eta_minutes ? `${incident.eta_minutes} min` : "—"} color="var(--green)" />
                </tbody>
              </table>
            ) : (
              <div style={{ color: "var(--muted)", fontSize: 13 }}>No active incident</div>
            )}
          </div>

          {/* Preparation checklist */}
          <div style={cardStyle()}>
            <SectionTitle>Preparation Checklist</SectionTitle>
            {incident?.er?.dashboard_payload?.preparation?.length > 0 ? (
              <ul style={{ margin: 0, padding: "0 0 0 16px", fontSize: 14, lineHeight: 2 }}>
                {incident.er.dashboard_payload.preparation.map((item, i) => (
                  <li key={i} style={{ color: "var(--text)" }}>{item}</li>
                ))}
              </ul>
            ) : incident?.first_aid_instructions?.length > 0 ? (
              <ul style={{ margin: 0, padding: "0 0 0 16px", fontSize: 14, lineHeight: 2 }}>
                {incident.first_aid_instructions.map((s, i) => (
                  <li key={i} style={{ color: "var(--text)" }}>{s}</li>
                ))}
              </ul>
            ) : (
              <div style={{ color: "var(--muted)", fontSize: 13 }}>Awaiting triage…</div>
            )}
          </div>

          {/* Live transcript */}
          <div style={cardStyle()}>
            <SectionTitle>Scene Audio Transcript</SectionTitle>
            <div style={{
              maxHeight: 220,
              overflowY: "auto",
              fontSize: 13,
              lineHeight: 1.7,
            }}>
              {transcript.length === 0 ? (
                <div style={{ color: "var(--muted)" }}>Awaiting audio…</div>
              ) : (
                transcript.map((line, i) => (
                  <p key={i} style={{
                    margin: "2px 0",
                    color: i === transcript.length - 1 ? "var(--text)" : "var(--muted)",
                  }}>
                    {line}
                  </p>
                ))
              )}
            </div>
          </div>

        </div>
      </div>
    </>
  );
}

// ── Small shared components ────────────────────────────────────────────────────

function SectionTitle({ children }) {
  return (
    <div style={{
      fontSize: 11,
      fontWeight: 700,
      letterSpacing: 2,
      color: "var(--muted)",
      marginBottom: 12,
      textTransform: "uppercase",
    }}>
      {children}
    </div>
  );
}

function Row({ label, value, color }) {
  return (
    <tr>
      <td style={{ color: "var(--muted)", padding: "5px 0", width: "40%", fontSize: 13 }}>{label}</td>
      <td style={{ color: color || "var(--text)", fontWeight: 600, fontSize: 14 }}>{value}</td>
    </tr>
  );
}

function cardStyle() {
  return {
    background: "var(--surface)",
    border: "1px solid var(--border)",
    borderRadius: 10,
    padding: 18,
  };
}

function btnStyle(color) {
  return {
    padding: "8px 18px",
    background: "transparent",
    color: color,
    border: `1px solid ${color}`,
    borderRadius: 6,
    cursor: "pointer",
    fontSize: 13,
    fontWeight: 600,
  };
}
