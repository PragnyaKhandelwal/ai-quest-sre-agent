import { useCallback, useEffect, useRef, useState } from "react";
import SimulatorPanel from "./components/SimulatorPanel";
import AlertStream from "./components/AlertStream";
import AgentTrace from "./components/AgentTrace";
import HITLQueue from "./components/HITLQueue";
import RCAPanel from "./components/RCAPanel";
import MetricsPanel from "./components/MetricsPanel";
import VoiceBriefing from "./components/VoiceBriefing";
import AIMSLog from "./components/AIMSLog";
import {
  approveHitl,
  getHealth,
  getHitlPending,
  getIncident,
  listIncidents,
  rejectHitl,
  subscribeGlobalEvents,
  subscribeIncidentStream,
} from "./api";

const SCENARIO_KEY_MAP = { 1: 1, 2: 2, 3: 3, 4: 4 };

function playTone(frequencies) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.connect(gain);
    gain.connect(ctx.destination);
    frequencies.forEach(([freq, at]) => oscillator.frequency.setValueAtTime(freq, ctx.currentTime + at));
    gain.gain.setValueAtTime(0.3, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
    oscillator.start(ctx.currentTime);
    oscillator.stop(ctx.currentTime + 0.3);
  } catch {
    /* AudioContext unavailable (e.g. no user gesture yet, or unsupported browser) -- silent alerts are not fatal */
  }
}

function exportIncidentsCsv(incidents) {
  const rows = [
    ["Incident ID", "Severity", "Service", "Status", "Title", "Updated At"],
    ...incidents.map((i) => [
      i.incident_id, i.severity, i.service, i.status, i.title || "", i.updated_at || "",
    ]),
  ];
  const csv = rows.map((r) => r.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `sre-incidents-${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function App() {
  const [incidents, setIncidents] = useState([]);
  const [incidentsLoading, setIncidentsLoading] = useState(true);
  const [selectedId, setSelectedId] = useState(null);
  const [selectedDetail, setSelectedDetail] = useState(null);
  const [pendingHitl, setPendingHitl] = useState([]);
  const [busyHitlId, setBusyHitlId] = useState(null);
  const [healthy, setHealthy] = useState(true);
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem("sre-theme") || "dark";
    } catch {
      return "dark";
    }
  });

  const incidentStreamCloser = useRef(null);
  const knownIncidentIds = useRef(new Set());
  const knownHitlIds = useRef(new Set());
  const scenarioTriggerRef = useRef(null);

  const refreshIncidents = useCallback(() => {
    listIncidents()
      .then((list) => {
        // Sound alert on a newly-seen P1 incident -- diffed against the
        // previously-seen id set rather than array length, so a resolved
        // incident dropping off the list never triggers a false alarm.
        const freshP1 = list.some(
          (i) => i.severity === "P1" && !knownIncidentIds.current.has(i.incident_id)
        );
        knownIncidentIds.current = new Set(list.map((i) => i.incident_id));
        if (freshP1) playTone([[880, 0], [1046, 0.1]]);
        setIncidents(list);
        setIncidentsLoading(false);
      })
      .catch(() => {
        setHealthy(false);
        setIncidentsLoading(false);
      });
  }, []);

  const refreshHitl = useCallback(() => {
    getHitlPending()
      .then((list) => {
        const freshHitl = list.some(
          ({ request }) => !knownHitlIds.current.has(request.request_id)
        );
        knownHitlIds.current = new Set(list.map(({ request }) => request.request_id));
        if (freshHitl) playTone([[440, 0], [440, 0.15], [440, 0.3]]);
        setPendingHitl(list);
      })
      .catch(() => {});
  }, []);

  const refreshSelectedDetail = useCallback((id) => {
    if (!id) return;
    getIncident(id).then(setSelectedDetail).catch(() => {});
  }, []);

  // Theme toggle -- persisted per-browser; see index.css's
  // [data-theme="light"] rules for the overrides applied to the app chrome
  // (header + page background). The dense data panels (alert stream, agent
  // trace, HITL queue) intentionally keep their dark "mission control"
  // styling in both modes, matching how ops dashboards like Grafana/Datadog
  // keep console-style panels dark even inside an otherwise light theme.
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("sre-theme", theme);
    } catch {
      /* private browsing / storage disabled -- theme just won't persist */
    }
  }, [theme]);

  // Deep-link: ?incident=<id> auto-selects that incident on load (paired
  // with the "Share Incident" button in AgentTrace.jsx).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const incidentId = params.get("incident");
    if (incidentId) setSelectedId(incidentId);
  }, []);

  // Keyboard shortcuts: Ctrl/Cmd+1..4 trigger a scenario, Ctrl/Cmd+K clears
  // the current selection (there is no bulk-delete endpoint, so this can't
  // "clear all incidents" server-side -- it deselects instead).
  useEffect(() => {
    function handleKeyPress(e) {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key in SCENARIO_KEY_MAP) {
        e.preventDefault();
        scenarioTriggerRef.current?.(SCENARIO_KEY_MAP[e.key]);
      } else if (e.key === "k") {
        e.preventDefault();
        setSelectedId(null);
      }
    }
    window.addEventListener("keydown", handleKeyPress);
    return () => window.removeEventListener("keydown", handleKeyPress);
  }, []);

  // Initial load + global SSE subscription
  useEffect(() => {
    refreshIncidents();
    refreshHitl();
    getHealth()
      .then(() => setHealthy(true))
      .catch(() => setHealthy(false));

    const closeGlobal = subscribeGlobalEvents(
      (payload) => {
        setHealthy(true);
        if (payload.type === "incident_created" || payload.type === "status_update") {
          refreshIncidents();
        }
        if (["hitl_pending", "hitl_resolved", "status_update"].includes(payload.type)) {
          refreshHitl();
        }
        if (payload.type === "trace" && payload.incident_id === selectedId) {
          refreshSelectedDetail(selectedId);
        }
      },
      () => setHealthy(false)
    );

    return () => {
      closeGlobal();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Per-incident live trace stream
  useEffect(() => {
    if (incidentStreamCloser.current) {
      incidentStreamCloser.current();
      incidentStreamCloser.current = null;
    }
    if (!selectedId) {
      setSelectedDetail(null);
      return;
    }
    refreshSelectedDetail(selectedId);
    incidentStreamCloser.current = subscribeIncidentStream(
      selectedId,
      () => refreshSelectedDetail(selectedId),
      () => refreshSelectedDetail(selectedId)
    );
    return () => {
      if (incidentStreamCloser.current) incidentStreamCloser.current();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  function handleSimulateTriggered(res) {
    refreshIncidents();
    setSelectedId(res.incident_id);
  }

  async function handleApprove(incidentId, requestId) {
    setBusyHitlId(requestId);
    try {
      await approveHitl(incidentId, requestId);
      refreshHitl();
    } finally {
      setBusyHitlId(null);
    }
  }

  async function handleReject(incidentId, requestId) {
    setBusyHitlId(requestId);
    try {
      await rejectHitl(incidentId, requestId);
      refreshHitl();
    } finally {
      setBusyHitlId(null);
    }
  }

  const counts = {
    p1: incidents.filter((i) => i.severity === "P1").length,
    p2: incidents.filter((i) => i.severity === "P2").length,
    resolved: incidents.filter((i) => i.status === "RESOLVED").length,
    hitl: pendingHitl.length,
  };

  return (
    <div className="app-shell flex flex-col h-screen bg-base text-gray-100">
      <header className="app-header flex items-center justify-between px-5 py-3 border-b border-border bg-panel flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <span className="text-lg font-bold tracking-tight">
            🛰️ SRE War Room <span className="text-cyan">::</span> Governed Multi-Agent Incident Response
          </span>
        </div>
        <div className="flex items-center gap-4 text-xs flex-wrap">
          <span className="flex items-center gap-2.5 font-mono-log text-[11px]" title="Live incident counts">
            <span className="text-danger font-semibold">🔴 P1: {counts.p1}</span>
            <span className="text-warn font-semibold">🟠 P2: {counts.p2}</span>
            <span className="text-success font-semibold">✅ Resolved: {counts.resolved}</span>
            <span className="text-cyan font-semibold">⏳ HITL: {counts.hitl}</span>
          </span>
          <button
            onClick={() => exportIncidentsCsv(incidents)}
            title="Export all incidents as CSV"
            className="text-gray-400 hover:text-cyan border border-border rounded px-2 py-1 transition"
          >
            📊 Export CSV
          </button>
          <button
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
            title="Toggle light/dark chrome"
            className="text-gray-400 hover:text-cyan border border-border rounded px-2 py-1 transition"
          >
            {theme === "dark" ? "🌙" : "☀️"}
          </button>
          <span className={`flex items-center gap-1.5 font-semibold ${healthy ? "text-success" : "text-danger"}`}>
            <span className={`w-2 h-2 rounded-full ${healthy ? "bg-success" : "bg-danger"} ${healthy ? "pulse-dot" : ""}`} />
            {healthy ? "All Agents Online" : "Agent Error"}
          </span>
        </div>
      </header>

      <SimulatorPanel onTriggered={handleSimulateTriggered} exposeTrigger={(fn) => (scenarioTriggerRef.current = fn)}>
        <VoiceBriefing incident={selectedDetail} />
      </SimulatorPanel>

      <div className="mx-4 my-2.5 px-4 py-3 bg-panel border border-border rounded-lg">
        <div className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold mb-2">
          Session Metrics
        </div>
        <MetricsPanel selectedIncident={selectedDetail} />
      </div>

      <div className="flex-1 grid grid-cols-1 lg:grid-cols-10 min-h-0">
        <div className="lg:col-span-3 min-h-0">
          <AlertStream incidents={incidents} loading={incidentsLoading} selectedId={selectedId} onSelect={setSelectedId} />
        </div>
        <div className="lg:col-span-4 min-h-0">
          <AgentTrace incident={selectedDetail} />
        </div>
        <div className="lg:col-span-3 min-h-0">
          <HITLQueue pending={pendingHitl} onApprove={handleApprove} onReject={handleReject} busyId={busyHitlId} />
        </div>
      </div>

      {selectedDetail?.rca && (
        <div className="max-h-72 overflow-y-auto">
          <RCAPanel incident={selectedDetail} />
        </div>
      )}

      <AIMSLog />
    </div>
  );
}
