import { useEffect, useState } from "react";
import { getIncidentMetrics, getMetrics } from "../api";
import { useIncidentStore } from "../store/useIncidentStore";

function latencyColor(ms) {
  // Real Groq API calls normally take 2-4s -- thresholds widened so that
  // normal real-inference latency doesn't read as an alarm (text-danger).
  if (ms < 5000) return "text-success";
  if (ms < 10000) return "text-warn";
  return "text-danger";
}

function HallucinationBadge({ incident }) {
  const reports = incident?.hallucination_reports || [];
  if (reports.length === 0) {
    return <span className="text-[11px] text-gray-500">Hallucination Guard: no diagnosis yet</span>;
  }
  const signalCount = reports.reduce(
    (acc, r) =>
      acc +
      (r.hallucination_signals?.length || 0) +
      (r.invalid_citations?.length || 0) +
      (!r.schema_valid ? 1 : 0),
    0
  );
  const allPassed = reports.every((r) => r.passed);
  return (
    <span
      className={`text-[11px] font-semibold px-2 py-0.5 rounded border ${
        allPassed
          ? "text-success border-success/40 bg-success/10"
          : "text-danger border-danger/40 bg-danger/10"
      }`}
      title="agents/hallucination_guard.py: schema + hedge-language + grounding validation"
    >
      {allPassed ? "✓ All outputs validated" : `⚠ ${signalCount} signal(s) detected`}
    </span>
  );
}

function RetrievalBadge({ incident }) {
  const scores = incident?.diagnosis?.retrieval_scores || [];
  if (scores.length === 0) {
    return <span className="text-[11px] text-gray-500">Retrieval Quality: n/a</span>;
  }
  const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
  return (
    <span
      className="text-[11px] font-semibold px-2 py-0.5 rounded border text-cyan border-cyan/40 bg-cyan/10"
      title="TF-IDF cosine similarity, avg across top-K retrieved log lines"
    >
      🔍 Retrieval avg score: {avg.toFixed(3)}
    </span>
  );
}

export default function MetricsPanel({ selectedIncident: propSelectedIncident }) {
  // Optional-prop-override: falls back to the store's full incident detail
  // when no prop is passed. Session-level metrics (session/incidentMetrics
  // below) deliberately stay component-local rather than moving into the
  // store -- this component polls independently on a 5s interval, and a
  // shared store value would go stale between mounts in tests that don't
  // reset it (each test here starts local state at null, matching this
  // component's original per-mount fetch behavior).
  const storeSelectedIncident = useIncidentStore((s) => s.selectedIncidentDetail);
  const selectedIncident = propSelectedIncident ?? storeSelectedIncident;
  const [session, setSession] = useState(null);
  const [incidentMetrics, setIncidentMetrics] = useState([]);

  useEffect(() => {
    function refresh() {
      getMetrics().then(setSession).catch(() => {});
    }
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!selectedIncident) {
      setIncidentMetrics([]);
      return;
    }
    getIncidentMetrics(selectedIncident.incident_id)
      .then(setIncidentMetrics)
      .catch(() => setIncidentMetrics([]));
  }, [selectedIncident?.incident_id, selectedIncident?.trace?.length]);

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs font-mono-log text-gray-300">
        <span>🔢 Total Tokens: <span className="text-cyan font-semibold">{(session?.total_tokens ?? 0).toLocaleString()}</span></span>
        <span>💰 Est. Cost: <span className="text-success font-semibold">{session?.cost_formatted ?? "$0.0000"}</span></span>
        <span>
          ⚡ Avg Latency:{" "}
          <span className={`font-semibold ${latencyColor(session?.avg_latency_ms ?? 0)}`}>
            {(session?.avg_latency_ms ?? 0).toFixed(0)}ms
          </span>
        </span>
        <span>📞 Agent Calls: <span className="text-gray-100 font-semibold">{session?.total_calls ?? 0}</span></span>
      </div>

      {selectedIncident && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <HallucinationBadge incident={selectedIncident} />
            <RetrievalBadge incident={selectedIncident} />
          </div>

          {incidentMetrics.length > 0 && (
            <div className="overflow-x-auto">
              <table className="text-[11px] font-mono-log border-collapse">
                <thead>
                  <tr className="text-gray-500">
                    <th className="text-left pr-3 font-medium">Agent</th>
                    <th className="text-left pr-3 font-medium">Latency</th>
                    <th className="text-left pr-3 font-medium">Tokens</th>
                    <th className="text-left font-medium">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {incidentMetrics.map((m, i) => (
                    <tr key={i} className="text-gray-300">
                      <td className="pr-3">{m.agent}</td>
                      <td className={`pr-3 ${latencyColor(m.latency_ms)}`}>{m.latency_ms}ms</td>
                      <td className="pr-3">{m.total_tokens}</td>
                      <td>${m.cost_usd}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
