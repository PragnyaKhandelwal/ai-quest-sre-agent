import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import MetricsPanel from "../../components/MetricsPanel";

global.fetch = vi.fn();

const mockMetrics = {
  total_tokens: 1656,
  total_cost_usd: 0.0005,
  avg_latency_ms: 60,
  total_calls: 4,
  cost_formatted: "$0.0005",
};

describe("MetricsPanel", () => {
  beforeEach(() => {
    fetch.mockClear();
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockMetrics),
    });
  });

  it("renders the metrics header labels", () => {
    render(<MetricsPanel />);
    expect(screen.getByText(/Total Tokens/i)).toBeInTheDocument();
    expect(screen.getByText(/Est\. Cost/i)).toBeInTheDocument();
    expect(screen.getByText(/Avg Latency/i)).toBeInTheDocument();
    expect(screen.getByText(/Agent Calls/i)).toBeInTheDocument();
  });

  it("displays the token count after fetch resolves", async () => {
    render(<MetricsPanel />);
    await waitFor(() => {
      expect(screen.getByText(/1,656|1656/)).toBeInTheDocument();
    });
  });

  it("displays the cost estimate after fetch resolves", async () => {
    render(<MetricsPanel />);
    await waitFor(() => {
      expect(screen.getByText("$0.0005")).toBeInTheDocument();
    });
  });

  it("displays the agent call count after fetch resolves", async () => {
    render(<MetricsPanel />);
    // The count renders in its own nested <span>, separate from the
    // "Agent Calls:" label text node -- Testing Library's default text
    // matcher doesn't concatenate text across element boundaries, so this
    // asserts on the value's own span rather than the combined phrase.
    await waitFor(() => {
      expect(screen.getByText("4")).toBeInTheDocument();
    });
  });

  it("shows zero-value defaults before the fetch resolves", () => {
    fetch.mockImplementation(() => new Promise(() => {})); // never resolves
    render(<MetricsPanel />);
    expect(screen.getByText(/\$0\.0000/)).toBeInTheDocument();
  });
});
