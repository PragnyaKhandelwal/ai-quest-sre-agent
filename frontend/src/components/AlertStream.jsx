import { useState } from "react";

const SkeletonCard = () => (
  <div className="animate-pulse border-b border-border px-4 py-3">
    <div className="h-3 bg-gray-700 rounded w-1/4 mb-2"></div>
    <div className="h-4 bg-gray-700 rounded w-3/4 mb-1"></div>
    <div className="h-3 bg-gray-700 rounded w-1/2"></div>
  </div>
);

const SEVERITY_STYLES = {
  P1: "bg-danger/15 text-danger border-danger/40",
  P2: "bg-warn/15 text-warn border-warn/40",
  P3: "bg-yellow-500/15 text-yellow-400 border-yellow-500/40",
  P4: "bg-cyan/15 text-cyan border-cyan/40",
};

const STATUS_STYLES = {
  TRIAGING: "text-gray-400",
  DIAGNOSING: "text-cyan",
  REMEDIATING: "text-warn",
  AWAITING_HITL: "text-danger",
  POST_MORTEM: "text-cyan",
  RESOLVED: "text-success",
  ERROR: "text-danger",
};

function timeAgo(ts) {
  const diff = Math.max(0, Date.now() / 1000 - ts);
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

export default function AlertStream({ incidents, loading, selectedId, onSelect }) {
  const [filter, setFilter] = useState("");
  const filteredIncidents = incidents.filter((i) => {
    const needle = filter.toLowerCase();
    return (
      !needle ||
      i.service?.toLowerCase().includes(needle) ||
      i.severity?.toLowerCase().includes(needle) ||
      i.status?.toLowerCase().includes(needle) ||
      i.title?.toLowerCase().includes(needle)
    );
  });

  return (
    <div className="flex flex-col h-full bg-panel border-r border-border">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-300">
          Live Alert Stream
        </h2>
        <span className="text-xs text-gray-500">{incidents.length} incident(s)</span>
      </div>
      <div className="px-3 py-2 border-b border-border">
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by service, severity, or status..."
          className="w-full bg-black/30 border border-border rounded px-2.5 py-1.5 text-xs text-gray-200 placeholder-gray-600 focus:outline-none focus:border-cyan"
        />
      </div>
      <div className="flex-1 overflow-y-auto divide-y divide-border">
        {loading && (
          <>
            <SkeletonCard />
            <SkeletonCard />
            <SkeletonCard />
          </>
        )}
        {!loading && filteredIncidents.length === 0 && (
          <div className="p-6 text-center flex flex-col items-center gap-2">
            <span className="text-2xl opacity-40">{incidents.length === 0 ? "🛡️" : "📡"}</span>
            {incidents.length === 0 ? (
              <>
                <div className="text-sm font-mono-log text-gray-400">All systems nominal</div>
                <div className="text-[11px] font-mono-log text-gray-600">
                  Trigger a simulation above to begin incident response ↑
                </div>
              </>
            ) : (
              <div className="text-sm text-gray-500">No incidents match your filter.</div>
            )}
          </div>
        )}
        {!loading && filteredIncidents.map((inc) => (
          <button
            key={inc.incident_id}
            onClick={() => onSelect(inc.incident_id)}
            className={`w-full text-left px-4 py-3 transition hover:bg-white/5 ${
              selectedId === inc.incident_id ? "bg-cyan/10 border-l-2 border-cyan" : "border-l-2 border-transparent"
            }`}
          >
            <div className="flex items-center justify-between mb-1">
              <span
                className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${SEVERITY_STYLES[inc.severity] || SEVERITY_STYLES.P4}`}
              >
                {inc.severity}
              </span>
              <span className="text-[10px] text-gray-500">{timeAgo(inc.updated_at)}</span>
            </div>
            <div className="text-sm font-medium text-gray-100 truncate">{inc.service}</div>
            <div className="text-xs text-gray-500 truncate mt-0.5">{inc.title}</div>
            <div className="flex items-center justify-between mt-1.5">
              <span className={`text-[11px] font-mono-log ${STATUS_STYLES[inc.status] || "text-gray-400"}`}>
                {inc.status}
              </span>
              {inc.has_pending_hitl && (
                <span className="text-[10px] font-bold text-danger animate-pulse">● HITL</span>
              )}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
