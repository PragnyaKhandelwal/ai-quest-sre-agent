import { getRcaJsonUrl, getRcaPdfUrl } from "../api";

export default function RCAPanel({ incident }) {
  if (!incident?.rca) return null;
  const rca = incident.rca;

  async function downloadJson() {
    const res = await fetch(getRcaJsonUrl(incident.incident_id));
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `RCA_${incident.incident_id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function downloadPdf() {
    const res = await fetch(getRcaPdfUrl(incident.incident_id));
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `RCA_${incident.incident_id}.pdf`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="bg-panel border-t border-border px-6 py-4">
      <div className="flex items-center justify-between flex-wrap gap-3 mb-3">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-300">
            Post-Mortem / RCA -- {rca.title}
          </h2>
          <span className="text-xs text-gray-500">Blameless report · {incident.incident_id}</span>
        </div>
        <div className="flex gap-2">
          <button
            onClick={downloadJson}
            className="px-3 py-1.5 text-xs font-semibold rounded border border-cyan/40 text-cyan hover:bg-cyan/10 transition"
          >
            ⬇ Download JSON
          </button>
          <button
            onClick={downloadPdf}
            className="px-3 py-1.5 text-xs font-semibold rounded border border-success/40 text-success hover:bg-success/10 transition"
          >
            ⬇ Download PDF
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
        <div>
          <h3 className="text-xs font-semibold text-gray-400 uppercase mb-1">Root Cause</h3>
          <p className="text-gray-200 text-xs leading-relaxed">{rca.root_cause}</p>
        </div>
        <div>
          <h3 className="text-xs font-semibold text-gray-400 uppercase mb-1">Prevention Recommendations</h3>
          <ul className="list-disc list-inside text-gray-200 text-xs space-y-0.5">
            {rca.prevention_recommendations.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="text-xs font-semibold text-gray-400 uppercase mb-1">Timeline</h3>
          <ul className="text-gray-400 text-[11px] font-mono-log space-y-0.5 max-h-28 overflow-y-auto">
            {rca.timeline.map((t, i) => (
              <li key={i}>
                <span className="text-gray-600">{new Date(t.timestamp * 1000).toLocaleTimeString()}</span>{" "}
                <span className="text-cyan">[{t.actor}]</span> {t.event}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
