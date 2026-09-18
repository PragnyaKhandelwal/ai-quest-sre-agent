import { useEffect, useState } from "react";

function useCountdown(expiresAt) {
  const [remaining, setRemaining] = useState(Math.max(0, expiresAt - Date.now() / 1000));

  useEffect(() => {
    const id = setInterval(() => {
      setRemaining(Math.max(0, expiresAt - Date.now() / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [expiresAt]);

  const mins = Math.floor(remaining / 60);
  const secs = Math.floor(remaining % 60);
  return { mins, secs, urgent: remaining < 60 };
}

function Countdown({ expiresAt }) {
  const { mins, secs, urgent } = useCountdown(expiresAt);
  return (
    <span className={`font-mono-log text-xs ${urgent ? "text-danger" : "text-gray-400"}`}>
      {mins}:{secs.toString().padStart(2, "0")}
    </span>
  );
}

const RISK_BADGE_STYLES = {
  low: "bg-success/15 border-success/40 text-success",
  medium: "bg-warn/15 border-warn/40 text-warn",
  high: "bg-warn/15 border-warn/40 text-warn",
  critical: "bg-danger/15 border-danger/40 text-danger",
};

function HITLCard({ incident_id, request, onApprove, onReject, busy }) {
  const { urgent } = useCountdown(request.expires_at);
  const riskBadge = RISK_BADGE_STYLES[request.action.risk_level] || "bg-gray-500/15 border-gray-500/40 text-gray-400";

  return (
    <div
      className={`px-4 py-3 border-l-2 ${
        urgent ? "border-l-danger bg-danger/5" : "border-l-warn bg-warn/5"
      }`}
    >
      <div className="flex items-center justify-between mb-1">
        <span
          className={`text-[10px] font-bold font-mono-log tracking-wider ${urgent ? "text-danger" : "text-warn"}`}
        >
          ⚠ HUMAN APPROVAL REQUIRED
        </span>
        <Countdown expiresAt={request.expires_at} />
      </div>
      <span className="text-[11px] font-mono-log text-gray-500">{incident_id}</span>
      <div className="text-sm font-medium text-gray-100 mt-1">{request.action.description}</div>
      <span
        className={`inline-block text-[10px] font-semibold uppercase mt-1.5 px-1.5 py-0.5 rounded border ${riskBadge}`}
      >
        risk: {request.action.risk_level}
      </span>
      <div className="mt-2 flex items-start gap-1">
        <pre className="flex-1 bg-black/40 border border-border rounded px-2 py-1.5 text-[11px] font-mono-log text-cyan overflow-x-auto whitespace-pre-wrap break-all">
          {request.action.command}
        </pre>
        <button
          onClick={() => navigator.clipboard?.writeText(request.action.command)}
          title="Copy command to clipboard"
          className="text-xs text-gray-400 hover:text-cyan px-1.5 py-1"
        >
          📋
        </button>
      </div>
      <p className="text-[11px] text-gray-500 mt-1">{request.action.reason}</p>
      <div className="flex gap-2 mt-2.5">
        <button
          disabled={busy}
          onClick={() => onApprove(incident_id, request.request_id)}
          className="flex-1 bg-success/15 border border-success/40 text-success text-xs font-semibold py-1.5 rounded hover:bg-success/25 transition disabled:opacity-40"
        >
          ✓ Approve
        </button>
        <button
          disabled={busy}
          onClick={() => onReject(incident_id, request.request_id)}
          className="flex-1 bg-danger/15 border border-danger/40 text-danger text-xs font-semibold py-1.5 rounded hover:bg-danger/25 transition disabled:opacity-40"
        >
          ✗ Reject
        </button>
      </div>
    </div>
  );
}

export default function HITLQueue({ pending, onApprove, onReject, busyId }) {
  return (
    <div className="flex flex-col h-full bg-panel border-l border-border">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between flex-wrap gap-x-2 gap-y-1">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-300">
          HITL Approval Queue
        </h2>
        {pending.length > 0 && (
          <span className="text-[10px] font-bold text-danger animate-pulse whitespace-nowrap">{pending.length} PENDING</span>
        )}
      </div>
      <div className="flex-1 overflow-y-auto divide-y divide-border">
        {pending.length === 0 && (
          <div className="p-6 text-center text-gray-500 text-sm flex flex-col items-center gap-2">
            <span className="text-2xl opacity-40">✅</span>
            No destructive actions awaiting approval.
          </div>
        )}
        {pending.map(({ incident_id, request }) => (
          <HITLCard
            key={request.request_id}
            incident_id={incident_id}
            request={request}
            onApprove={onApprove}
            onReject={onReject}
            busy={busyId === request.request_id}
          />
        ))}
      </div>
    </div>
  );
}
