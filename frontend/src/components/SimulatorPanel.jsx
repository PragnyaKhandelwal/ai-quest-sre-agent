import { useEffect, useState } from "react";
import { simulateScenario } from "../api";

const SCENARIOS = [
  { id: 1, label: "Simulate P1 Memory Leak", emoji: "🔴", color: "border-danger text-danger hover:bg-danger/10" },
  { id: 2, label: "Simulate P2 Bad Deploy", emoji: "🟠", color: "border-warn text-warn hover:bg-warn/10" },
  { id: 3, label: "Simulate P1 DB Deadlock", emoji: "🔴", color: "border-danger text-danger hover:bg-danger/10" },
  { id: 4, label: "Simulate P2 Node Drain", emoji: "🟠", color: "border-warn text-warn hover:bg-warn/10" },
  { id: 5, label: "Simulate P2 CPU Throttle", emoji: "🟠", color: "border-warn text-warn hover:bg-warn/10" },
  { id: 6, label: "Simulate P3 Cert Expiry", emoji: "🟡", color: "border-yellow-500 text-yellow-400 hover:bg-yellow-500/10" },
];

export default function SimulatorPanel({ onTriggered, exposeTrigger, children }) {
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

  // Lets App.jsx's global keyboard-shortcut handler (Ctrl+1..4) call this
  // component's own trigger() without lifting scenario state up a level.
  useEffect(() => {
    exposeTrigger?.(trigger);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex flex-col gap-2 px-4 py-3 bg-panel border-b border-border">
      <div className="flex items-center gap-2 flex-nowrap overflow-x-auto pb-1">
        <span className="text-xs uppercase tracking-wider text-gray-400 font-semibold mr-1 flex-shrink-0">
          Incident Simulator
        </span>
        {SCENARIOS.map((s) => (
          <button
            key={s.id}
            onClick={() => trigger(s.id)}
            disabled={loadingId !== null}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md border text-xs font-medium transition disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap flex-shrink-0 ${s.color}`}
          >
            <span>{s.emoji}</span>
            <span>{s.label}</span>
            {loadingId === s.id && <span className="animate-spin">⏳</span>}
          </button>
        ))}
        {lastError && (
          <span className="text-danger text-xs ml-2 flex-shrink-0 whitespace-nowrap">Error: {lastError}</span>
        )}
      </div>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <span className="text-[10px] text-gray-600">
          Shortcuts: Ctrl+1/2/3/4 to trigger scenarios · Ctrl+K to clear selection
        </span>
        {children && <div className="flex items-center gap-2">{children}</div>}
      </div>
    </div>
  );
}
