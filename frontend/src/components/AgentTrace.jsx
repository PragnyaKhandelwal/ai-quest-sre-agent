import { useState } from "react";
import DecisionGraph from "./DecisionGraph";

const AGENT_COLORS = {
  TriageAndDedupAgent: "bg-cyan/15 text-cyan",
  RootCauseDiagnosticianAgent: "bg-purple-500/15 text-purple-300",
  RemediationPlannerAgent: "bg-warn/15 text-warn",
  PostMortemRCAAgent: "bg-success/15 text-success",
  LyzrSafeAI: "bg-danger/15 text-danger",
  pipeline: "bg-gray-500/15 text-gray-300",
};

function ConfidenceMeter({ confidence, threshold }) {
  const pct = Math.round(confidence * 100);
  const below = confidence < threshold;
  return (
    <div className="mt-2">
      <div className="flex items-center justify-between text-[11px] mb-1">
        <span className="text-gray-400">Diagnosis confidence</span>
        <span className={below ? "text-danger font-semibold" : "text-success font-semibold"}>{pct}%</span>
      </div>
      <div className="h-2 rounded-full bg-black/40 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${below ? "bg-danger" : "bg-success"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {below && (
        <div className="text-[11px] text-danger mt-1">
          Below {Math.round(threshold * 100)}% threshold -- escalated for human review
        </div>
      )}
    </div>
  );
}

function latencyBadgeColor(ms) {
  if (ms < 2000) return "text-success";
  if (ms < 5000) return "text-warn";
  return "text-danger";
}

function HallucinationGuardResult({ report }) {
  if (!report) return null;
  return (
    <span
      className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border ${
        report.passed
          ? "text-success border-success/40 bg-success/10"
          : "text-warn border-warn/40 bg-warn/10"
      }`}
    >
      {report.passed ? "✓ Hallucination Guard passed" : "⚠ Hallucination Guard flagged output"}
    </span>
  );
}

function RetrievalScores({ scores }) {
  if (!Array.isArray(scores) || scores.length === 0) return null;
  const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
  return (
    <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded border text-cyan border-cyan/40 bg-cyan/10">
      🔍 {scores.length} logs retrieved, avg score {avg.toFixed(3)}
    </span>
  );
}

function TraceStep({ step, hallucinationReport }) {
  const [expanded, setExpanded] = useState(false);
  const color = AGENT_COLORS[step.agent_name] || "bg-gray-500/15 text-gray-300";
  const evidence = step.detail?.evidence;
  const confidence = step.detail?.confidence;
  const retrievalScores = step.detail?.retrieval_scores;

  return (
    <div className={`rounded-lg border ${step.blocked ? "border-danger/50" : "border-border"} bg-black/20 p-3`}>
      <div className="flex items-center justify-between flex-wrap gap-2">
        <span className={`text-[11px] font-semibold px-2 py-0.5 rounded ${color}`}>{step.agent_name}</span>
        <div className="flex items-center gap-3 text-[11px] text-gray-500 font-mono-log">
          <span title="tokens consumed by this call">🔢 {step.tokens_used} tok</span>
          <span title="latency of this call" className={`font-semibold ${latencyBadgeColor(step.latency_ms)}`}>
            ⚡ {step.latency_ms.toFixed(0)}ms
          </span>
          <span>{new Date(step.timestamp * 1000).toLocaleTimeString()}</span>
        </div>
      </div>

      {step.blocked && (
        <div className="mt-2 text-xs font-semibold text-danger bg-danger/10 border border-danger/40 rounded px-2 py-1">
          ⛔ BLOCKED: Destructive action requires human approval
        </div>
      )}

      <p className="text-sm text-gray-200 mt-2 leading-snug">{step.summary}</p>

      {(hallucinationReport || retrievalScores) && (
        <div className="flex flex-wrap items-center gap-1.5 mt-2">
          <HallucinationGuardResult report={hallucinationReport} />
          <RetrievalScores scores={retrievalScores} />
        </div>
      )}

      {typeof confidence === "number" && (
        <ConfidenceMeter confidence={confidence} threshold={0.7} />
      )}

      {Array.isArray(evidence) && evidence.length > 0 && (
        <div className="mt-2">
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-[11px] text-cyan hover:underline"
          >
            {expanded ? "Hide" : "Show"} evidence ({evidence.length})
          </button>
          {expanded && (
            <ul className="mt-1.5 space-y-1 font-mono-log text-[11px] text-gray-400 bg-black/30 rounded p-2 max-h-40 overflow-y-auto">
              {evidence.map((e, i) => (
                <li key={i}>
                  <span className="text-gray-600">L{e.line_number}:</span> {e.log_line}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function shareIncident(incidentId) {
  const url = `${window.location.origin}${window.location.pathname}?incident=${incidentId}`;
  navigator.clipboard?.writeText(url);
  alert("Incident URL copied to clipboard!");
}

export default function AgentTrace({ incident }) {
  const [view, setView] = useState("trace"); // "trace" | "graph"

  if (!incident) {
    return (
      <div className="flex flex-col h-full bg-base">
        <div className="px-4 py-3 border-b border-border">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-300">
            Agent Reasoning Trace
          </h2>
        </div>
        <div className="flex-1 flex flex-col items-center justify-center text-gray-500 text-sm px-6 text-center gap-2">
          <span className="text-2xl opacity-40">🔎</span>
          Select an incident from the alert stream to view the agent reasoning trace.
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-base">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-300">
            Agent Reasoning Trace
          </h2>
          <div className="text-xs text-gray-500 mt-0.5 font-mono-log">{incident.incident_id}</div>
        </div>
        <button
          onClick={() => shareIncident(incident.incident_id)}
          title="Copy a shareable link to this incident"
          className="text-[11px] text-gray-400 hover:text-cyan border border-border rounded px-2 py-1 mr-2"
        >
          🔗 Share
        </button>
        <div className="flex rounded-md border border-border overflow-hidden text-[11px] font-semibold">
          <button
            onClick={() => setView("trace")}
            className={`px-2.5 py-1 transition ${view === "trace" ? "bg-cyan/20 text-cyan" : "text-gray-500 hover:text-gray-300"}`}
          >
            Trace
          </button>
          <button
            onClick={() => setView("graph")}
            className={`px-2.5 py-1 transition ${view === "graph" ? "bg-cyan/20 text-cyan" : "text-gray-500 hover:text-gray-300"}`}
          >
            Decision Graph
          </button>
        </div>
      </div>

      {view === "graph" ? (
        <div className="flex-1 overflow-y-auto">
          <DecisionGraph incident={incident} />
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {incident.trace.length === 0 && (
            <div className="text-gray-500 text-sm">Waiting for agents to start reasoning...</div>
          )}
          {incident.trace.map((step) => {
            // Each hallucination_guard.py report is keyed by agent name (only
            // the diagnostician runs through the guard today); match it to
            // its trace step so the badge renders inline with that step.
            const hallucinationReport = (incident.hallucination_reports || []).find(
              (r) => r.agent === step.agent_name
            );
            return <TraceStep key={step.step_id} step={step} hallucinationReport={hallucinationReport} />;
          })}
        </div>
      )}
    </div>
  );
}
