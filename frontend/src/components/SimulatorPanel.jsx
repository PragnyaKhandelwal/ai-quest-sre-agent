import { useState } from "react";
import { simulateScenario } from "../api";

const SCENARIOS = [
  { id: 1, label: "Simulate P1 Memory Leak", emoji: "🔴", color: "border-danger text-danger hover:bg-danger/10" },
  { id: 2, label: "Simulate P2 Bad Deploy", emoji: "🟠", color: "border-warn text-warn hover:bg-warn/10" },
  { id: 3, label: "Simulate P1 DB Deadlock", emoji: "🔴", color: "border-danger text-danger hover:bg-danger/10" },
  { id: 4, label: "Simulate P2 Node Drain", emoji: "🟠", color: "border-warn text-warn hover:bg-warn/10" },
];

export default function SimulatorPanel({ onTriggered }) {
  const [loadingId, setLoadingId] = useState(null);
  const [lastError, setLastError] = useState(null);

  async function trigger(id) {
    setLoadingId(id);
    setLastError(null);
    try {
      const res = await simulateScenario(id);
      onTriggered?.(res);
    } catch (err) {
      setLastError(err.message);
    } finally {
      setLoadingId(null);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3 px-4 py-3 bg-panel border-b border-border">
      <span className="text-xs uppercase tracking-wider text-gray-400 font-semibold mr-1">
        Incident Simulator
      </span>
      {SCENARIOS.map((s) => (
        <button
          key={s.id}
          onClick={() => trigger(s.id)}
          disabled={loadingId !== null}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-md border text-sm font-medium transition disabled:opacity-40 disabled:cursor-not-allowed ${s.color}`}
        >
          <span>{s.emoji}</span>
          <span>{s.label}</span>
          {loadingId === s.id && <span className="animate-spin">⏳</span>}
        </button>
      ))}
      {lastError && (
        <span className="text-danger text-xs ml-2">Error: {lastError}</span>
      )}
    </div>
  );
}
