import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import AnomalyPanel from "../../components/AnomalyPanel";

global.fetch = vi.fn();

const mockAnalysis = {
  incident_id: "inc_test001",
  correlation_graph: {
    nodes: [{ id: "a1", service: "payment-service", severity: "P1", score: 4, namespace: "production" }],
    edges: [],
    root_cause_alert_id: "a1",
    root_cause_service: "payment-service",
    confidence: 0.5,
  },
  burst_detection: { is_burst: true, analysis: "Burst detected: 4 alerts in 5min window" },
  escalation_prediction: {
    escalation_risk: 0.72,
    risk_level: "HIGH",
    factors: {
      severity_contribution: 0.24,
      anomaly_contribution: 0.0,
      volume_contribution: 0.08,
      hitl_contribution: 0.1,
    },
  },
};

describe("AnomalyPanel", () => {
  beforeEach(() => {
    fetch.mockClear();
    fetch.mockResolvedValue({ ok: true, json: () => Promise.resolve(mockAnalysis) });
  });

  it("renders nothing when there is no incident selected", () => {
    const { container } = render(<AnomalyPanel incidentId={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("fetches and displays the escalation risk", async () => {
    render(<AnomalyPanel incidentId="inc_test001" />);
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/incidents/inc_test001/anomaly-analysis"));
    await waitFor(() => {
      expect(screen.getByText(/ESCALATION RISK: HIGH/i)).toBeInTheDocument();
    });
  });

  it("shows the burst warning when a burst is detected", async () => {
    render(<AnomalyPanel incidentId="inc_test001" />);
    await waitFor(() => {
      expect(screen.getByText(/ALERT BURST/i)).toBeInTheDocument();
    });
  });

  it("shows the root cause service from the correlation graph", async () => {
    render(<AnomalyPanel incidentId="inc_test001" />);
    await waitFor(() => {
      expect(screen.getByText(/payment-service/i)).toBeInTheDocument();
    });
  });

  it("hides the burst warning when no burst is detected", async () => {
    fetch.mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          ...mockAnalysis,
          burst_detection: { is_burst: false, analysis: "Normal rate" },
        }),
    });
    render(<AnomalyPanel incidentId="inc_test001" />);
    await waitFor(() => {
      expect(screen.getByText(/ESCALATION RISK/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/ALERT BURST/i)).not.toBeInTheDocument();
  });
});
