import { create } from "zustand";
import { devtools, subscribeWithSelector } from "zustand/middleware";
import {
  approveHitl,
  getHealth,
  getHitlPending,
  getIncident,
  getMetrics,
  listIncidents,
  rejectHitl,
  simulateScenario,
} from "../api";

export const useIncidentStore = create(
  devtools(
    subscribeWithSelector((set, get) => ({
      // -- State ---------------------------------------------------------
      incidents: [],
      // Starts false: AlertStream.jsx falls back to this field when its
      // `loading` prop is omitted, and its existing tests rely on an
      // omitted prop meaning "not loading" (matching the component's
      // original implicit-undefined-is-falsy behavior).
      incidentsLoading: false,
      selectedIncidentId: null,
      // Full incident detail (trace/diagnosis/rca/...) for the selected
      // incident -- deliberately a SEPARATE field from `incidents`, not
      // derived by searching that list: GET /incidents returns lightweight
      // summary_dict()-shaped rows (severity/service/status/title only),
      // while AgentTrace/RCAPanel/VoiceBriefing need the full detail from
      // GET /incidents/{id}. Conflating the two would silently break every
      // consumer that reads `.trace`/`.rca`/`.diagnosis`.
      selectedIncidentDetail: null,
      metrics: null,
      pendingHITL: [],
      busyHitlId: null,
      healthy: true,
      error: null,

      // -- Simple setters --------------------------------------------------
      selectIncident: (id) => set({ selectedIncidentId: id }, false, "selectIncident"),
      clearSelectedDetail: () => set({ selectedIncidentDetail: null }, false, "clearSelectedDetail"),
      setError: (error) => set({ error }, false, "setError"),

      // -- Async actions -----------------------------------------------------
      fetchIncidents: async () => {
        try {
          const list = await listIncidents();
          set({ incidents: Array.isArray(list) ? list : [], incidentsLoading: false, healthy: true }, false, "fetchIncidents");
        } catch (e) {
          set({ incidentsLoading: false, healthy: false, error: e.message }, false, "fetchIncidents/error");
        }
      },

      fetchHitlPending: async () => {
        try {
          const list = await getHitlPending();
          set({ pendingHITL: list }, false, "fetchHitlPending");
        } catch {
          /* transient network hiccup -- keep the last known queue rather than clearing it */
        }
      },

      fetchSelectedDetail: async (id) => {
        const targetId = id ?? get().selectedIncidentId;
        if (!targetId) return;
        try {
          const detail = await getIncident(targetId);
          // Guard against a slow response landing after the user already
          // switched to a different incident.
          if (get().selectedIncidentId === targetId) {
            set({ selectedIncidentDetail: detail }, false, "fetchSelectedDetail");
          }
        } catch {
          /* ignore -- the SSE stream/next poll will retry */
        }
      },

      fetchMetrics: async () => {
        try {
          const data = await getMetrics();
          set({ metrics: data }, false, "fetchMetrics");
        } catch {
          /* non-critical -- session metrics can be momentarily stale */
        }
      },

      checkHealth: async () => {
        try {
          await getHealth();
          set({ healthy: true }, false, "checkHealth");
        } catch {
          set({ healthy: false }, false, "checkHealth/error");
        }
      },

      // Not called by SimulatorPanel today (it manages its own
      // loading/error UI state around simulateScenario() directly) --
      // available here for any future caller that just wants "trigger a
      // scenario and select the resulting incident" as one step.
      triggerScenario: async (scenario) => {
        const res = await simulateScenario(scenario);
        await get().fetchIncidents();
        set({ selectedIncidentId: res.incident_id }, false, "triggerScenario");
        return res;
      },

      approveHITL: async (incidentId, requestId) => {
        set({ busyHitlId: requestId }, false, "approveHITL/start");
        try {
          await approveHitl(incidentId, requestId);
          await get().fetchHitlPending();
        } finally {
          set({ busyHitlId: null }, false, "approveHITL/done");
        }
      },

      rejectHITL: async (incidentId, requestId) => {
        set({ busyHitlId: requestId }, false, "rejectHITL/start");
        try {
          await rejectHitl(incidentId, requestId);
          await get().fetchHitlPending();
        } finally {
          set({ busyHitlId: null }, false, "rejectHITL/done");
        }
      },
    })),
    { name: "SREIncidentStore" }
  )
);

// Plain selector functions rather than object-literal getters inside the
// state object above: Zustand's `set()` merges state via object spread
// (`{ ...state, ...partial }`), which evaluates a getter ONCE at spread
// time and bakes the result in as a plain value -- after the very first
// `set()` call, a `get selectedIncident()`-style accessor would freeze at
// whatever it returned then and never update again. These stay reactive
// because each selector call recomputes from the latest state.
export const selectP1Count = (s) => s.incidents.filter((i) => i.severity === "P1" && i.status !== "RESOLVED").length;
export const selectP2Count = (s) => s.incidents.filter((i) => i.severity === "P2" && i.status !== "RESOLVED").length;
export const selectResolvedCount = (s) => s.incidents.filter((i) => i.status === "RESOLVED").length;
export const selectHitlCount = (s) => s.pendingHITL.length;
