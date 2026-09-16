import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  approveHitl,
  getIncident,
  getRcaJsonUrl,
  getRcaPdfUrl,
  listIncidents,
  rejectHitl,
  simulateScenario,
} from "../../api";

global.fetch = vi.fn();

describe("api.js", () => {
  beforeEach(() => {
    fetch.mockClear();
  });

  it("simulateScenario POSTs to /simulate/{n}", async () => {
    fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ incident_id: "inc_001" }),
    });
    const data = await simulateScenario(1);
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/simulate/1"), {
      method: "POST",
    });
    expect(data.incident_id).toBe("inc_001");
  });

  it("listIncidents GETs /incidents", async () => {
    fetch.mockResolvedValueOnce({ ok: true, json: () => Promise.resolve([]) });
    await listIncidents();
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/incidents"));
  });

  it("getIncident GETs /incidents/{id}", async () => {
    fetch.mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({}) });
    await getIncident("inc_test001");
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/incidents/inc_test001"));
  });

  it("approveHitl POSTs a JSON body with the request_id", async () => {
    fetch.mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ status: "APPROVED" }) });
    await approveHitl("inc_test001", "hitl_abc");
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/hitl/inc_test001/approve"),
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining("hitl_abc"),
      })
    );
  });

  it("rejectHitl POSTs to the reject endpoint", async () => {
    fetch.mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ status: "REJECTED" }) });
    await rejectHitl("inc_test001", "hitl_abc");
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/hitl/inc_test001/reject"),
      expect.any(Object)
    );
  });

  it("getRcaJsonUrl and getRcaPdfUrl build the correct URLs", () => {
    expect(getRcaJsonUrl("inc_x")).toContain("/incidents/inc_x/rca");
    expect(getRcaPdfUrl("inc_x")).toContain("/incidents/inc_x/rca/pdf");
  });

  it("rejects with the structured error message on a non-ok response", async () => {
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: () =>
        Promise.resolve({
          error: "INCIDENT_NOT_FOUND",
          message: "Incident 'nope' not found",
          detail: { incident_id: "nope" },
        }),
    });
    await expect(getIncident("nope")).rejects.toThrow("Incident 'nope' not found");
  });

  it("propagates a network error", async () => {
    fetch.mockRejectedValueOnce(new Error("Network error"));
    await expect(listIncidents()).rejects.toThrow("Network error");
  });
});
