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

export default function App() {
  const [incidents, setIncidents] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [selectedDetail, setSelectedDetail] = useState(null);
  const [pendingHitl, setPendingHitl] = useState([]);
  const [busyHitlId, setBusyHitlId] = useState(null);
  const [healthy, setHealthy] = useState(true);

  const incidentStreamCloser = useRef(null);

  const refreshIncidents = useCallback(() => {
    listIncidents().then(setIncidents).catch(() => setHealthy(false));
  }, []);

  const refreshHitl = useCallback(() => {
    getHitlPending().then(setPendingHitl).catch(() => {});
  }, []);

  const refreshSelectedDetail = useCallback((id) => {
    if (!id) return;
    getIncident(id).then(setSelectedDetail).catch(() => {});
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

  return (
    <div className="flex flex-col h-screen bg-base text-gray-100">
      <header className="flex items-center justify-between px-5 py-3 border-b border-border bg-panel">
        <div className="flex items-center gap-3">
          <span className="text-lg font-bold tracking-tight">
            🛰️ SRE War Room <span className="text-cyan">::</span> Governed Multi-Agent Incident Response
          </span>
        </div>
        <div className="flex items-center gap-5 text-xs">
          <span className={`flex items-center gap-1.5 font-semibold ${healthy ? "text-success" : "text-danger"}`}>
            <span className={`w-2 h-2 rounded-full ${healthy ? "bg-success" : "bg-danger"} ${healthy ? "pulse-dot" : ""}`} />
            {healthy ? "All Agents Online" : "Agent Error"}
          </span>
        </div>
      </header>

      <SimulatorPanel onTriggered={handleSimulateTriggered}>
        <VoiceBriefing incident={selectedDetail} />
      </SimulatorPanel>

      <div className="px-4 py-2 bg-panel border-b border-border">
        <MetricsPanel selectedIncident={selectedDetail} />
      </div>

      <div className="flex-1 grid grid-cols-1 lg:grid-cols-10 min-h-0">
        <div className="lg:col-span-3 min-h-0">
          <AlertStream incidents={incidents} selectedId={selectedId} onSelect={setSelectedId} />
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
