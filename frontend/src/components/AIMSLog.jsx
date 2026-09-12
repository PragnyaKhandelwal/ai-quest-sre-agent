import { useEffect, useState } from "react";
import { getAimsEvents } from "../api";

const AGENT_COLORS = {
  TriageAndDedupAgent: "text-cyan",
  RootCauseDiagnosticianAgent: "text-purple-300",
  RemediationPlannerAgent: "text-warn",
  PostMortemRCAAgent: "text-success",
  LyzrSafeAI: "text-danger",
  ToolRegistry: "text-yellow-300",
  pipeline: "text-gray-400",
};

function agentColor(name) {
  return AGENT_COLORS[name] || "text-gray-300";
}

function AIMSRow({ event }) {
  const [expanded, setExpanded] = useState(false);
  const toolCalled = event.metadata?.tool_called;

  return (
    <>
      <tr
        className="border-b border-border/60 hover:bg-white/5 cursor-pointer"
        onClick={() => setExpanded((v) => !v)}
      >
        <td className="py-1.5 pr-3 text-gray-500 whitespace-nowrap">
          {new Date(event.timestamp * 1000).toLocaleTimeString()}
        </td>
        <td className="py-1.5 pr-3 text-gray-400 whitespace-nowrap">
          {event.incident_id || "—"}
        </td>
        <td className={`py-1.5 pr-3 font-semibold whitespace-nowrap ${agentColor(event.agent_name)}`}>
          {event.agent_name}
        </td>
        <td className="py-1.5 pr-3 text-gray-300 max-w-[240px] truncate">{event.action}</td>
        <td className="py-1.5 pr-3 text-yellow-300 whitespace-nowrap">{toolCalled || "—"}</td>
        <td className="py-1.5 pr-3 text-gray-400 whitespace-nowrap">{event.tokens_used}</td>
        <td className="py-1.5 pr-3 text-gray-400 whitespace-nowrap">{event.latency_ms.toFixed(1)}ms</td>
        <td className="py-1.5 pr-3 whitespace-nowrap">
          <span
            className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${
              event.blocked
                ? "text-danger border-danger/40 bg-danger/10"
                : "text-success border-success/40 bg-success/10"
            }`}
          >
            {event.blocked ? "BLOCKED" : "OK"}
          </span>
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-border/60 bg-black/30">
          <td colSpan={8} className="p-2 font-mono-log text-[11px] text-gray-400">
            <div className="mb-1">
              <span className="text-gray-600">input:</span> {event.input_summary || "—"}
            </div>
            <div>
              <span className="text-gray-600">output:</span> {event.output_summary || "—"}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export default function AIMSLog() {
  const [events, setEvents] = useState([]);
  const [error, setError] = useState(false);

  useEffect(() => {
    function refresh() {
      getAimsEvents()
        .then((data) => {
          setEvents(data);
          setError(false);
        })
        .catch(() => setError(true));
    }
    refresh();
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, []);

  function downloadJson() {
    const blob = new Blob([JSON.stringify(events, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `aims_audit_trail_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="bg-panel border-t-2 border-cyan/40">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
        <div>
          <h2 className="text-sm font-bold uppercase tracking-wider text-cyan">
            🔍 AIMS Audit Trail — Chronological Command/Query/Output Log
          </h2>
          <span className="text-[11px] text-gray-500">
            {events.length} event(s) · auto-refreshes every 3s · click a row to expand
          </span>
        </div>
        <button
          onClick={downloadJson}
          className="px-3 py-1.5 text-xs font-semibold rounded border border-cyan/40 text-cyan hover:bg-cyan/10 transition whitespace-nowrap"
        >
          ⬇ Download AIMS Log (JSON)
        </button>
      </div>
      <div className="max-h-64 overflow-y-auto overflow-x-auto px-4">
        {error && <div className="text-danger text-xs py-3">Failed to load AIMS events.</div>}
        {!error && events.length === 0 && (
          <div className="text-gray-500 text-xs py-3">No AIMS events yet — trigger a simulation above.</div>
        )}
        {events.length > 0 && (
          <table className="w-full text-[11px] font-mono-log border-collapse min-w-[720px]">
            <thead className="sticky top-0 bg-panel">
              <tr className="text-gray-500 border-b border-border text-left">
                <th className="py-1.5 pr-3 font-medium">Time</th>
                <th className="py-1.5 pr-3 font-medium">Incident</th>
                <th className="py-1.5 pr-3 font-medium">Agent</th>
                <th className="py-1.5 pr-3 font-medium">Action</th>
                <th className="py-1.5 pr-3 font-medium">Tool Called</th>
                <th className="py-1.5 pr-3 font-medium">Tokens</th>
                <th className="py-1.5 pr-3 font-medium">Latency</th>
                <th className="py-1.5 pr-3 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <AIMSRow key={e.event_id} event={e} />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
