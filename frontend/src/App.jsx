import { useEffect, useRef, useState } from "react";
import SimulatorPanel from "./components/SimulatorPanel";
import AlertStream from "./components/AlertStream";
import AgentTrace from "./components/AgentTrace";
import HITLQueue from "./components/HITLQueue";
import RCAPanel from "./components/RCAPanel";
import MetricsPanel from "./components/MetricsPanel";
import VoiceBriefing from "./components/VoiceBriefing";
import AIMSLog from "./components/AIMSLog";
import { subscribeGlobalEvents, subscribeIncidentStream } from "./api";
import {
  useIncidentStore,
  selectP1Count,
  selectP2Count,
  selectResolvedCount,
  selectHitlCount,
} from "./store/useIncidentStore";

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
  // AlertStream, HITLQueue, and MetricsPanel now subscribe to the store
  // directly (see their own optional-prop-override fallbacks), so App no
  // longer threads incidents/pendingHITL/busyHitlId/loading through props
  // to them -- only what App itself still needs (header counts, the CSV
  // export, the per-incident SSE subscription, and the props AgentTrace/
  // RCAPanel genuinely need) is read here.
  const incidents = useIncidentStore((s) => s.incidents);
  const selectedId = useIncidentStore((s) => s.selectedIncidentId);
  const selectedDetail = useIncidentStore((s) => s.selectedIncidentDetail);
  const healthy = useIncidentStore((s) => s.healthy);
  const p1 = useIncidentStore(selectP1Count);
  const p2 = useIncidentStore(selectP2Count);
  const resolved = useIncidentStore(selectResolvedCount);
  const hitlCount = useIncidentStore(selectHitlCount);

  const selectIncident = useIncidentStore((s) => s.selectIncident);
  const fetchIncidents = useIncidentStore((s) => s.fetchIncidents);
  const fetchHitlPending = useIncidentStore((s) => s.fetchHitlPending);
  const fetchSelectedDetail = useIncidentStore((s) => s.fetchSelectedDetail);
  const fetchMetrics = useIncidentStore((s) => s.fetchMetrics);
  const checkHealth = useIncidentStore((s) => s.checkHealth);

  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem("sre-theme") || "dark";
    } catch {
      return "dark";
    }
  });

  const incidentStreamCloser = useRef(null);
  const scenarioTriggerRef = useRef(null);

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
    if (incidentId) selectIncident(incidentId);
  }, [selectIncident]);

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
        selectIncident(null);
      }
    }
    window.addEventListener("keydown", handleKeyPress);
    return () => window.removeEventListener("keydown", handleKeyPress);
  }, [selectIncident]);

  // Sound alerts on a newly-seen P1 or a newly-seen HITL request --
  // genuinely uses subscribeWithSelector (rather than a plain useEffect on
  // the `incidents`/`pendingHitl` values above) so this diffing logic runs
  // exactly once per real store update, independent of React's render
  // cycle. Diffs by ID membership (not a raw count comparison), so a P1
  // resolving in the same tick a new one arrives still alerts correctly.
  useEffect(() => {
    let knownP1Ids = new Set();
    let knownHitlIds = new Set();

    const unsubIncidents = useIncidentStore.subscribe(
      (s) => s.incidents,
      (list) => {
        const activeP1 = list.filter((i) => i.severity === "P1" && i.status !== "RESOLVED");
        const freshP1 = activeP1.some((i) => !knownP1Ids.has(i.incident_id));
        knownP1Ids = new Set(activeP1.map((i) => i.incident_id));
        if (freshP1) playTone([[880, 0], [1046, 0.1]]);
      }
    );
    const unsubHitl = useIncidentStore.subscribe(
      (s) => s.pendingHITL,
      (list) => {
        const freshHitl = list.some(({ request }) => !knownHitlIds.has(request.request_id));
        knownHitlIds = new Set(list.map(({ request }) => request.request_id));
        if (freshHitl) playTone([[440, 0], [440, 0.15], [440, 0.3]]);
      }
    );
    return () => {
      unsubIncidents();
      unsubHitl();
    };
  }, []);

  // Initial load + global SSE subscription
  useEffect(() => {
    fetchIncidents();
    fetchHitlPending();
    fetchMetrics();
    checkHealth();

    const closeGlobal = subscribeGlobalEvents(
      (payload) => {
        useIncidentStore.setState({ healthy: true });
        if (payload.type === "incident_created" || payload.type === "status_update") {
          fetchIncidents();
        }
        if (["hitl_pending", "hitl_resolved", "status_update"].includes(payload.type)) {
          fetchHitlPending();
        }
        // Reads the CURRENT selection from the store rather than closing
        // over `selectedId` (this effect intentionally has an empty dep
        // array so it doesn't resubscribe on every selection change) --
        // the dedicated per-incident stream below already covers this via
        // its own [selectedId]-scoped effect, so this is a low-cost extra
        // safety net, not the primary mechanism.
        if (payload.type === "trace" && payload.incident_id === useIncidentStore.getState().selectedIncidentId) {
          fetchSelectedDetail(payload.incident_id);
        }
      },
      () => useIncidentStore.setState({ healthy: false })
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
      useIncidentStore.setState({ selectedIncidentDetail: null });
      return;
    }
    fetchSelectedDetail(selectedId);
    incidentStreamCloser.current = subscribeIncidentStream(
      selectedId,
      () => fetchSelectedDetail(selectedId),
      () => fetchSelectedDetail(selectedId)
    );
    return () => {
      if (incidentStreamCloser.current) incidentStreamCloser.current();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  function handleSimulateTriggered(res) {
    fetchIncidents();
    selectIncident(res.incident_id);
  }

  const counts = { p1, p2, resolved, hitl: hitlCount };

  return (
    <div className="app-shell flex flex-col min-h-screen bg-base text-gray-100">
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
        <MetricsPanel />
      </div>

      <div className="flex-1 grid grid-cols-1 lg:grid-cols-10 min-h-0">
        <div className="lg:col-span-3 min-h-0">
          <AlertStream />
        </div>
        <div className="lg:col-span-4 min-h-0">
          <AgentTrace incident={selectedDetail} />
        </div>
        <div className="lg:col-span-3 min-h-0">
          <HITLQueue />
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
