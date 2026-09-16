import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import HITLQueue from "../../components/HITLQueue";

// Real shape returned by GET /hitl/pending (backend/store.py's
// list_pending_hitl()): a list of {incident_id, request} where `request`
// is a serialized HITLRequest (agents/schemas.py), whose `action` field is
// a serialized RemediationAction.
const mockPending = [
  {
    incident_id: "inc_test001",
    request: {
      request_id: "hitl_abc123",
      status: "PENDING",
      expires_at: Date.now() / 1000 + 300,
      action: {
        step: 1,
        description: "Terminate blocking session",
        command: "SELECT pg_terminate_backend(pid)",
        is_destructive: true,
        risk_level: "critical",
        reason: "Session is holding a long-running lock",
      },
    },
  },
];

describe("HITLQueue", () => {
  it("renders empty state when no pending actions", () => {
    render(<HITLQueue pending={[]} onApprove={vi.fn()} onReject={vi.fn()} busyId={null} />);
    expect(screen.getByText(/No destructive actions awaiting approval/i)).toBeInTheDocument();
  });

  it("renders a pending HITL action", () => {
    render(<HITLQueue pending={mockPending} onApprove={vi.fn()} onReject={vi.fn()} busyId={null} />);
    expect(screen.getByText(/Terminate blocking session/i)).toBeInTheDocument();
  });

  it("shows the risk level for the action", () => {
    render(<HITLQueue pending={mockPending} onApprove={vi.fn()} onReject={vi.fn()} busyId={null} />);
    expect(screen.getByText(/risk: critical/i)).toBeInTheDocument();
  });

  it("shows the destructive command", () => {
    render(<HITLQueue pending={mockPending} onApprove={vi.fn()} onReject={vi.fn()} busyId={null} />);
    expect(screen.getByText(/pg_terminate_backend/i)).toBeInTheDocument();
  });

  it("calls onApprove with the incident and request IDs when Approve is clicked", () => {
    const onApprove = vi.fn();
    render(<HITLQueue pending={mockPending} onApprove={onApprove} onReject={vi.fn()} busyId={null} />);
    fireEvent.click(screen.getByText(/Approve/i));
    expect(onApprove).toHaveBeenCalledWith("inc_test001", "hitl_abc123");
  });

  it("calls onReject with the incident and request IDs when Reject is clicked", () => {
    const onReject = vi.fn();
    render(<HITLQueue pending={mockPending} onApprove={vi.fn()} onReject={onReject} busyId={null} />);
    fireEvent.click(screen.getByText(/Reject/i));
    expect(onReject).toHaveBeenCalledWith("inc_test001", "hitl_abc123");
  });

  it("shows the pending count badge", () => {
    render(<HITLQueue pending={mockPending} onApprove={vi.fn()} onReject={vi.fn()} busyId={null} />);
    expect(screen.getByText(/1 PENDING/i)).toBeInTheDocument();
  });

  it("disables the Approve/Reject buttons for the request currently being decided", () => {
    render(
      <HITLQueue pending={mockPending} onApprove={vi.fn()} onReject={vi.fn()} busyId="hitl_abc123" />
    );
    expect(screen.getByText(/Approve/i).closest("button")).toBeDisabled();
    expect(screen.getByText(/Reject/i).closest("button")).toBeDisabled();
  });
});
