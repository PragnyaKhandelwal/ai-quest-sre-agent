/**
 * Anomaly Analysis Panel
 * Shows ML-based anomaly detection results (agents/anomaly_detector.py,
 * surfaced via GET /incidents/{id}/anomaly-analysis):
 * - Correlation graph (root cause vs. symptom alerts)
 * - Burst detection
 * - Escalation risk prediction
 */
import { useEffect, useState } from "react";
import { getAnomalyAnalysis } from "../api";

const RISK_STYLES = {
  CRITICAL: "text-danger border-danger/50",
  HIGH: "text-warn border-warn/50",
  MEDIUM: "text-yellow-400 border-yellow-500/50",
  LOW: "text-success border-success/50",
};

export default function AnomalyPanel({ incidentId }) {
  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!incidentId) {
      setAnalysis(null);
      return;
    }
    setLoading(true);
    getAnomalyAnalysis(incidentId)
      .then((data) => {
        setAnalysis(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [incidentId]);

  if (!incidentId) return null;

  if (loading) {
    return (
      <div className="text-cyan text-[11px] font-mono-log p-2 animate-pulse">
        🔬 Running ML anomaly detection...
      </div>
    );
  }

  if (!analysis) return null;

  const risk = analysis.escalation_prediction;
  const burst = analysis.burst_detection;
  const graph = analysis.correlation_graph;
  const riskStyle = RISK_STYLES[risk?.risk_level] || "text-gray-400 border-border";

  return (
    <div className="rounded-lg border border-border bg-black/20 p-3 mt-2">
      <div className="text-cyan text-[11px] font-mono-log font-bold mb-2">🔬 ML ANOMALY ANALYSIS</div>

      <div className={`border rounded p-2 mb-2 ${riskStyle}`}>
        <div className="text-[11px] font-mono-log">
          ESCALATION RISK: {risk?.risk_level} ({((risk?.escalation_risk ?? 0) * 100).toFixed(0)}%)
        </div>
        <div className="h-1 bg-black/40 rounded mt-1 overflow-hidden">
          <div
            className="h-1 bg-current rounded transition-all"
            style={{ width: `${(risk?.escalation_risk ?? 0) * 100}%` }}
          />
        </div>
      </div>

      {burst?.is_burst && (
        <div className="text-warn text-[11px] font-mono-log mb-2">⚡ ALERT BURST: {burst.analysis}</div>
      )}

      <div className="text-[11px] font-mono-log text-gray-400">
        ROOT CAUSE: {graph?.root_cause_service || "unknown"} ({graph?.nodes?.length || 0} correlated alert(s))
      </div>
    </div>
  );
}
