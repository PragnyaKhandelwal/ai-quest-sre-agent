import { create } from "zustand";

// Declared for centralized per-incident metrics caching, same
// "available but not yet wired everywhere" pattern already used in this
// codebase (e.g. backend/settings.py's enable_* feature flags): kept
// separate from useIncidentStore because it's keyed by incident_id rather
// than being a single piece of app state. MetricsPanel.jsx currently
// fetches and caches its own per-incident metrics locally rather than
// through this store, specifically to avoid touching its already-tested
// behavior in this pass -- this is here for the next component (or a
// future MetricsPanel refactor) that wants a shared cache instead of a
// component-local one.
export const useMetricsStore = create((set) => ({
  sessionMetrics: null,
  incidentMetrics: {},

  setSessionMetrics: (metrics) => set({ sessionMetrics: metrics }),
  setIncidentMetrics: (incidentId, metrics) =>
    set((state) => ({
      incidentMetrics: { ...state.incidentMetrics, [incidentId]: metrics },
    })),
}));
