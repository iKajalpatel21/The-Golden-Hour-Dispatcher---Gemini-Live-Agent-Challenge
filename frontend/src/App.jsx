import React, { useState } from "react";
import CallerView from "./pages/CallerView";
import ERDashboard from "./pages/ERDashboard";

export default function App() {
  const [view, setView] = useState("caller"); // "caller" | "er"

  return (
    <div>
      {/* Simple nav for demo switching */}
      <div style={{
        display: "flex",
        gap: 8,
        padding: "8px 16px",
        background: "#111",
        borderBottom: "1px solid #333",
      }}>
        <button
          onClick={() => setView("caller")}
          style={{
            padding: "6px 14px",
            background: view === "caller" ? "#c0392b" : "#222",
            color: "#fff",
            border: "none",
            borderRadius: 6,
            cursor: "pointer",
            fontWeight: 600,
          }}
        >
          Caller View
        </button>
        <button
          onClick={() => setView("er")}
          style={{
            padding: "6px 14px",
            background: view === "er" ? "#2471a3" : "#222",
            color: "#fff",
            border: "none",
            borderRadius: 6,
            cursor: "pointer",
            fontWeight: 600,
          }}
        >
          ER Dashboard
        </button>
      </div>

      {view === "caller" ? <CallerView /> : <ERDashboard />}
    </div>
  );
}
