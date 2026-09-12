/**
 * AIMS Decision Graph -- Stretch Goal
 * Pure SVG visualization of the agent decision pipeline for the selected
 * incident. Hardcoded layout (not a dynamic force graph) -- deliberately
 * simple so it renders instantly and matches the dark war-room theme.
 */

const STATUS_STYLE = {
  pending: { stroke: "#3a4358", text: "#6b7280", badge: "PENDING", pulse: false },
  running: { stroke: "#00d4ff", text: "#00d4ff", badge: "RUNNING", pulse: true },
  complete: { stroke: "#00ff88", text: "#00ff88", badge: "COMPLETE", pulse: false },
  blocked: { stroke: "#ff3366", text: "#ff3366", badge: "BLOCKED", pulse: true },
  skipped: { stroke: "#2a3550", text: "#4b5563", badge: "N/A", pulse: false },
};

function findStep(incident, agentName) {
  return (incident.trace || []).find((s) => s.agent_name === agentName);
}

function deriveStatuses(incident) {
  if (!incident) {
    return {
      ingest: "pending", triage: "pending", diagnostic: "pending",
      remediation: "pending", hitl: "skipped", approval: "skipped", postmortem: "pending",
    };
  }
  const status = incident.status;
  const triageStep = findStep(incident, "TriageAndDedupAgent");
  const diagStep = findStep(incident, "RootCauseDiagnosticianAgent");
  const remediationStep = findStep(incident, "RemediationPlannerAgent");
  const postmortemStep = findStep(incident, "PostMortemRCAAgent");

  const hasDestructive = (incident.runbook?.blocked_steps || []).length > 0;
  const pendingCount = (incident.pending_hitl || []).length;

  let hitl = "skipped";
  let approval = "skipped";
  if (hasDestructive) {
    if (status === "AWAITING_HITL" || pendingCount > 0) {
      hitl = "blocked";
      approval = "running";
    } else if (postmortemStep) {
      const destructiveActions = (incident.runbook?.actions || []).filter((a) => a.is_destructive);
      const anyApproved = destructiveActions.some((a) => a.executed);
      hitl = "complete";
      approval = anyApproved ? "complete" : "blocked";
    } else {
      hitl = "running";
      approval = "pending";
    }
  }

  return {
    ingest: "complete",
    triage: triageStep ? (triageStep.blocked ? "blocked" : "complete") : status === "TRIAGING" ? "running" : "pending",
    diagnostic: diagStep ? (diagStep.blocked ? "blocked" : "complete") : status === "DIAGNOSING" ? "running" : "pending",
    remediation: remediationStep
      ? (remediationStep.blocked ? "blocked" : "complete")
      : status === "REMEDIATING"
      ? "running"
      : "pending",
    hitl,
    approval,
    postmortem: postmortemStep ? "complete" : status === "POST_MORTEM" ? "running" : "pending",
  };
}

function Node({ x, y, w, h, emoji, name, statusKey, latencyMs }) {
  const style = STATUS_STYLE[statusKey] || STATUS_STYLE.pending;
  return (
    <g>
      <rect
        x={x} y={y} width={w} height={h} rx={10}
        fill="#0f1424" stroke={style.stroke} strokeWidth={statusKey === "running" || statusKey === "blocked" ? 2 : 1.5}
      >
        {style.pulse && (
          <animate attributeName="stroke-opacity" values="1;0.4;1" dur="1.4s" repeatCount="indefinite" />
        )}
      </rect>
      <text x={x + w / 2} y={y + 20} textAnchor="middle" fontSize="16">{emoji}</text>
      <text x={x + w / 2} y={y + 38} textAnchor="middle" fontSize="10.5" fontWeight="600" fill="#e6edf3">
        {name}
      </text>
      <text x={x + w / 2} y={y + 54} textAnchor="middle" fontSize="9" fontWeight="700" fill={style.text}>
        {style.badge}
      </text>
      {typeof latencyMs === "number" && (
        <text x={x + w / 2} y={y + h - 6} textAnchor="middle" fontSize="8.5" fill="#6b7280" fontFamily="monospace">
          {latencyMs.toFixed(0)}ms
        </text>
      )}
    </g>
  );
}

function Arrow({ x1, y1, x2, y2, dashed }) {
  return (
    <line
      x1={x1} y1={y1} x2={x2} y2={y2}
      stroke="#3a4358" strokeWidth={1.5}
      strokeDasharray={dashed ? "4 3" : undefined}
      markerEnd="url(#arrowhead)"
    />
  );
}

export default function DecisionGraph({ incident }) {
  const s = deriveStatuses(incident);
  const latency = (agentName) => {
    const step = incident && findStep(incident, agentName);
    return step ? step.latency_ms : undefined;
  };

  const W = 900;
  const nodeW = 130;
  const nodeH = 66;
  const rowY = 20;
  const gap = 40;
  const xs = [20, 20 + (nodeW + gap), 20 + 2 * (nodeW + gap), 20 + 3 * (nodeW + gap), 20 + 4 * (nodeW + gap)];
  const remediationX = xs[3];
  const hitlY = rowY + nodeH + 50;
  const approvalY = hitlY + nodeH + 50;

  return (
    <div className="p-4 overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${approvalY + nodeH + 20}`} className="w-full min-w-[720px]" style={{ maxHeight: 340 }}>
        <defs>
          <marker id="arrowhead" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 Z" fill="#3a4358" />
          </marker>
        </defs>

        <Arrow x1={xs[0] + nodeW} y1={rowY + nodeH / 2} x2={xs[1]} y2={rowY + nodeH / 2} />
        <Arrow x1={xs[1] + nodeW} y1={rowY + nodeH / 2} x2={xs[2]} y2={rowY + nodeH / 2} />
        <Arrow x1={xs[2] + nodeW} y1={rowY + nodeH / 2} x2={xs[3]} y2={rowY + nodeH / 2} />
        <Arrow x1={xs[3] + nodeW} y1={rowY + nodeH / 2} x2={xs[4]} y2={rowY + nodeH / 2} />

        {s.hitl !== "skipped" && (
          <>
            <Arrow x1={remediationX + nodeW / 2} y1={rowY + nodeH} x2={remediationX + nodeW / 2} y2={hitlY} />
            <Arrow x1={remediationX + nodeW / 2} y1={hitlY + nodeH} x2={remediationX + nodeW / 2} y2={approvalY} />
            <Arrow
              x1={remediationX + nodeW}
              y1={approvalY + nodeH / 2}
              x2={xs[4] + nodeW / 2}
              y2={rowY + nodeH}
              dashed
            />
          </>
        )}

        <Node x={xs[0]} y={rowY} w={nodeW} h={nodeH} emoji="🚨" name="Alert Ingest" statusKey={s.ingest} />
        <Node x={xs[1]} y={rowY} w={nodeW} h={nodeH} emoji="📊" name="Triage" statusKey={s.triage} latencyMs={latency("TriageAndDedupAgent")} />
        <Node x={xs[2]} y={rowY} w={nodeW} h={nodeH} emoji="🔍" name="Diagnostician" statusKey={s.diagnostic} latencyMs={latency("RootCauseDiagnosticianAgent")} />
        <Node x={xs[3]} y={rowY} w={nodeW} h={nodeH} emoji="🔧" name="Remediation Planner" statusKey={s.remediation} latencyMs={latency("RemediationPlannerAgent")} />
        <Node x={xs[4]} y={rowY} w={nodeW} h={nodeH} emoji="📋" name="Post-Mortem" statusKey={s.postmortem} latencyMs={latency("PostMortemRCAAgent")} />

        {s.hitl !== "skipped" && (
          <>
            <Node x={remediationX} y={hitlY} w={nodeW} h={nodeH} emoji="🛑" name="HITL Gate" statusKey={s.hitl} />
            <Node x={remediationX} y={approvalY} w={nodeW} h={nodeH} emoji="🧑‍💻" name="Human Approval" statusKey={s.approval} />
          </>
        )}
      </svg>
      {!incident && (
        <div className="text-center text-gray-500 text-xs mt-2">
          Select an incident to see its decision graph.
        </div>
      )}
    </div>
  );
}
