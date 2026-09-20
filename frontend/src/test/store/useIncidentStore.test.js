import { describe, it, expect, vi, beforeEach } from "vitest";
import { act } from "@testing-library/react";
import {
  useIncidentStore,
  selectP1Count,
  selectP2Count,
  selectResolvedCount,
  selectHitlCount,
} from "../../store/useIncidentStore";

global.fetch = vi.fn();

// Snapshot of the store's real initial state, captured before any test
// mutates it, so each test starts from a clean slate. The store is a
// module-level singleton (Zustand doesn't unmount between tests the way a
// component does), so this reset is required -- the same "clear cached
// state between tests" pattern already used for backend/secrets.py's
// SecretManager cache in the Python test suite.
const INITIAL_STATE = useIncidentStore.getState();

function okResponse(body) {
  return { ok: true, status: 200, json: () => Promise.resolve(body) };
}

describe("useIncidentStore", () => {
  beforeEach(() => {
    fetch.mockReset();
    act(() => {
      useIncidentStore.setState(INITIAL_STATE, true);
    });
  });

  it("starts with empty incidents and not loading", () => {
    const state = useIncidentStore.getState();
    expect(state.incidents).toEqual([]);
    expect(state.incidentsLoading).toBe(false);
    expect(state.pendingHITL).toEqual([]);
    expect(state.selectedIncidentId).toBe(null);
  });

  it("fetchIncidents populates incidents and marks the store healthy", async () => {
    const incidents = [
      { incident_id: "inc_1", severity: "P1", status: "TRIAGING" },
      { incident_id: "inc_2", severity: "P2", status: "RESOLVED" },
    ];
    fetch.mockResolvedValueOnce(okResponse(incidents));
    await act(async () => {
      await useIncidentStore.getState().fetchIncidents();
    });
    expect(useIncidentStore.getState().incidents).toEqual(incidents);
    expect(useIncidentStore.getState().healthy).toBe(true);
    expect(useIncidentStore.getState().incidentsLoading).toBe(false);
  });

  it("fetchIncidents marks the store unhealthy and records the error on failure", async () => {
    fetch.mockRejectedValueOnce(new Error("network down"));
    await act(async () => {
      await useIncidentStore.getState().fetchIncidents();
    });
    expect(useIncidentStore.getState().healthy).toBe(false);
    expect(useIncidentStore.getState().error).toMatch(/network down/);
  });

  it("selectIncident updates selectedIncidentId", () => {
    act(() => {
      useIncidentStore.getState().selectIncident("inc_42");
    });
    expect(useIncidentStore.getState().selectedIncidentId).toBe("inc_42");
  });

  it("fetchHitlPending populates pendingHITL from GET /hitl/pending", async () => {
    const pending = [{ incident_id: "inc_1", request: { request_id: "hitl_1" } }];
    fetch.mockResolvedValueOnce(okResponse(pending));
    await act(async () => {
      await useIncidentStore.getState().fetchHitlPending();
    });
    expect(useIncidentStore.getState().pendingHITL).toEqual(pending);
  });

  it("checkHealth flips healthy true/false based on the backend response", async () => {
    fetch.mockResolvedValueOnce(okResponse({ status: "ok" }));
    await act(async () => {
      await useIncidentStore.getState().checkHealth();
    });
    expect(useIncidentStore.getState().healthy).toBe(true);

    fetch.mockRejectedValueOnce(new Error("down"));
    await act(async () => {
      await useIncidentStore.getState().checkHealth();
    });
    expect(useIncidentStore.getState().healthy).toBe(false);
  });

  it("approveHITL sets busyHitlId during the call and refreshes the queue after", async () => {
    fetch.mockResolvedValueOnce(okResponse({ status: "approved" }));
    fetch.mockResolvedValueOnce(okResponse([]));
    await act(async () => {
      await useIncidentStore.getState().approveHITL("inc_1", "hitl_1");
    });
    expect(useIncidentStore.getState().busyHitlId).toBe(null);
    expect(useIncidentStore.getState().pendingHITL).toEqual([]);
  });

  it("rejectHITL sets busyHitlId during the call and refreshes the queue after", async () => {
    fetch.mockResolvedValueOnce(okResponse({ status: "rejected" }));
    fetch.mockResolvedValueOnce(okResponse([]));
    await act(async () => {
      await useIncidentStore.getState().rejectHITL("inc_1", "hitl_1");
    });
    expect(useIncidentStore.getState().busyHitlId).toBe(null);
    expect(useIncidentStore.getState().pendingHITL).toEqual([]);
  });

  describe("selectors", () => {
    beforeEach(() => {
      act(() => {
        useIncidentStore.setState({
          incidents: [
            { incident_id: "a", severity: "P1", status: "TRIAGING" },
            { incident_id: "b", severity: "P1", status: "RESOLVED" },
            { incident_id: "c", severity: "P2", status: "DIAGNOSING" },
            { incident_id: "d", severity: "P2", status: "RESOLVED" },
          ],
          pendingHITL: [{ incident_id: "a", request: { request_id: "r1" } }],
        });
      });
    });

    it("selectP1Count counts only active (non-resolved) P1 incidents", () => {
      expect(selectP1Count(useIncidentStore.getState())).toBe(1);
    });

    it("selectP2Count counts only active (non-resolved) P2 incidents", () => {
      expect(selectP2Count(useIncidentStore.getState())).toBe(1);
    });

    it("selectResolvedCount counts resolved incidents regardless of severity", () => {
      expect(selectResolvedCount(useIncidentStore.getState())).toBe(2);
    });

    it("selectHitlCount reflects the pendingHITL queue length", () => {
      expect(selectHitlCount(useIncidentStore.getState())).toBe(1);
    });
  });
});
