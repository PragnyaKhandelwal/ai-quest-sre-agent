import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import AlertStream from "../../components/AlertStream";

const mockIncidents = [
  {
    incident_id: "inc_001",
    severity: "P1",
    service: "payment-service",
    status: "RESOLVED",
    title: "payment-service pod OOMKilled",
    updated_at: Date.now() / 1000,
    has_pending_hitl: false,
  },
  {
    incident_id: "inc_002",
    severity: "P2",
    service: "api-gateway",
    status: "AWAITING_HITL",
    title: "api-gateway error rate spike",
    updated_at: Date.now() / 1000,
    has_pending_hitl: true,
  },
];

describe("AlertStream", () => {
  it("renders the empty state message", () => {
    render(<AlertStream incidents={[]} selectedId={null} onSelect={vi.fn()} />);
    expect(screen.getByText(/No incidents|Trigger a simulation/i)).toBeInTheDocument();
  });

  it("renders the incident count", () => {
    render(<AlertStream incidents={mockIncidents} selectedId={null} onSelect={vi.fn()} />);
    expect(screen.getByText(/2 incident/i)).toBeInTheDocument();
  });

  it("renders the P1 severity badge", () => {
    render(<AlertStream incidents={mockIncidents} selectedId={null} onSelect={vi.fn()} />);
    expect(screen.getByText("P1")).toBeInTheDocument();
  });

  it("renders the service name", () => {
    // Exact match, not a regex: "payment-service" also appears (as a
    // substring) inside the incident's title text node, which would make a
    // /payment-service/i regex match two separate elements.
    render(<AlertStream incidents={mockIncidents} selectedId={null} onSelect={vi.fn()} />);
    expect(screen.getByText("payment-service")).toBeInTheDocument();
  });

  it("calls onSelect with the incident_id when a card is clicked", () => {
    const onSelect = vi.fn();
    render(<AlertStream incidents={mockIncidents} selectedId={null} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("payment-service"));
    expect(onSelect).toHaveBeenCalledWith("inc_001");
  });

  it("shows the HITL badge for an incident with a pending action", () => {
    // Exact match: the AWAITING_HITL status text also contains "HITL" as a
    // substring, so a /HITL/i regex would match two separate elements --
    // this asserts on the dedicated "● HITL" badge specifically.
    render(<AlertStream incidents={mockIncidents} selectedId={null} onSelect={vi.fn()} />);
    expect(screen.getByText("● HITL")).toBeInTheDocument();
  });

  it("shows the RESOLVED status for a resolved incident", () => {
    render(<AlertStream incidents={mockIncidents} selectedId={null} onSelect={vi.fn()} />);
    expect(screen.getByText("RESOLVED")).toBeInTheDocument();
  });

  it("filters incidents by the search box", () => {
    render(<AlertStream incidents={mockIncidents} selectedId={null} onSelect={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/Filter by service/i), {
      target: { value: "api-gateway" },
    });
    expect(screen.getByText("api-gateway")).toBeInTheDocument();
    expect(screen.queryByText("payment-service")).not.toBeInTheDocument();
  });

  it("shows skeleton placeholders while loading", () => {
    const { container } = render(
      <AlertStream incidents={[]} loading selectedId={null} onSelect={vi.fn()} />
    );
    expect(container.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
  });
});
