import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import SimulatorPanel from "../../components/SimulatorPanel";

// SimulatorPanel calls simulateScenario() from ../api, which itself calls
// global fetch -- mocking fetch exercises the real code path end to end
// instead of mocking the api module away.
global.fetch = vi.fn();

describe("SimulatorPanel", () => {
  beforeEach(() => {
    fetch.mockClear();
    fetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ incident_id: "inc_test001" }),
    });
  });

  it("renders all 6 simulate buttons", () => {
    render(<SimulatorPanel onTriggered={vi.fn()} />);
    expect(screen.getByText(/Memory Leak/i)).toBeInTheDocument();
    expect(screen.getByText(/Bad Deploy/i)).toBeInTheDocument();
    expect(screen.getByText(/DB Deadlock/i)).toBeInTheDocument();
    expect(screen.getByText(/Node Drain/i)).toBeInTheDocument();
    expect(screen.getByText(/CPU Throttle/i)).toBeInTheDocument();
    expect(screen.getByText(/Cert Expiry/i)).toBeInTheDocument();
  });

  it("calls the simulate endpoint when a button is clicked", () => {
    render(<SimulatorPanel onTriggered={vi.fn()} />);
    const btn = screen.getByText(/Memory Leak/i);
    fireEvent.click(btn);
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/simulate/1"),
      expect.any(Object)
    );
  });

  it("calls onTriggered with the API response once the request resolves", async () => {
    const onTriggered = vi.fn();
    render(<SimulatorPanel onTriggered={onTriggered} />);
    await act(async () => {
      fireEvent.click(screen.getByText(/Memory Leak/i));
      await vi.waitFor(() => {
        expect(onTriggered).toHaveBeenCalledWith({ incident_id: "inc_test001" });
      });
    });
  });

  it("shows the P1 label on the memory leak button", () => {
    render(<SimulatorPanel onTriggered={vi.fn()} />);
    expect(screen.getByText(/P1 Memory Leak/i)).toBeInTheDocument();
  });

  it("shows the P2 label on the bad deploy button", () => {
    render(<SimulatorPanel onTriggered={vi.fn()} />);
    expect(screen.getByText(/P2 Bad Deploy/i)).toBeInTheDocument();
  });

  it("shows the keyboard shortcuts hint", () => {
    render(<SimulatorPanel onTriggered={vi.fn()} />);
    expect(screen.getByText(/Ctrl\+1\/2\/3\/4/i)).toBeInTheDocument();
  });
});
