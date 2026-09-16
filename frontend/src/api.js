export const BACKEND_URL =
  import.meta.env.VITE_BACKEND_URL || "http://localhost:8000";

async function handle(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      // backend/exceptions.py's error responses carry a human-readable
      // `message` plus a structured `detail` object (not a string) -- prefer
      // `message`, and only fall back to a string-typed `detail` for the
      // few endpoints that still raise a plain FastAPI HTTPException.
      detail = body.message || (typeof body.detail === "string" ? body.detail : detail);
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}

export function getHealth() {
  return fetch(`${BACKEND_URL}/health`).then(handle);
}

export function getStats() {
  return fetch(`${BACKEND_URL}/stats`).then(handle);
}

export function getMetrics() {
  return fetch(`${BACKEND_URL}/metrics`).then(handle);
}

export function getIncidentMetrics(id) {
  return fetch(`${BACKEND_URL}/incidents/${id}/metrics`).then(handle);
}

export function getAnomalyAnalysis(id) {
  return fetch(`${BACKEND_URL}/incidents/${id}/anomaly-analysis`).then(handle);
}

export function listIncidents() {
  return fetch(`${BACKEND_URL}/incidents`).then(handle);
}

export function getIncident(id) {
  return fetch(`${BACKEND_URL}/incidents/${id}`).then(handle);
}

export function simulateScenario(n) {
  return fetch(`${BACKEND_URL}/simulate/${n}`, { method: "POST" }).then(handle);
}

export function getHitlPending() {
  return fetch(`${BACKEND_URL}/hitl/pending`).then(handle);
}

export function approveHitl(incidentId, requestId, decidedBy = "sre-oncall") {
  return fetch(`${BACKEND_URL}/hitl/${incidentId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: requestId, decided_by: decidedBy }),
  }).then(handle);
}

export function rejectHitl(incidentId, requestId, decidedBy = "sre-oncall") {
  return fetch(`${BACKEND_URL}/hitl/${incidentId}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: requestId, decided_by: decidedBy }),
  }).then(handle);
}

export function getAimsEvents() {
  return fetch(`${BACKEND_URL}/aims/events`).then(handle);
}

export function getAimsEventsForIncident(incidentId) {
  return fetch(`${BACKEND_URL}/aims/events/${incidentId}`).then(handle);
}

export function getRcaJsonUrl(incidentId) {
  return `${BACKEND_URL}/incidents/${incidentId}/rca`;
}

export function getRcaPdfUrl(incidentId) {
  return `${BACKEND_URL}/incidents/${incidentId}/rca/pdf`;
}

export function subscribeGlobalEvents(onMessage, onError) {
  const es = new EventSource(`${BACKEND_URL}/events`);
  es.onmessage = (evt) => {
    try {
      onMessage(JSON.parse(evt.data));
    } catch {
      /* ignore keep-alive/comment lines */
    }
  };
  es.onerror = (err) => {
    if (onError) onError(err);
  };
  return () => es.close();
}

export function subscribeIncidentStream(incidentId, onStep, onDone) {
  const es = new EventSource(`${BACKEND_URL}/incidents/${incidentId}/stream`);
  es.onmessage = (evt) => {
    try {
      onStep(JSON.parse(evt.data));
    } catch {
      /* ignore */
    }
  };
  es.addEventListener("done", () => {
    if (onDone) onDone();
  });
  es.onerror = () => {
    // EventSource auto-retries; nothing to do here for the demo.
  };
  return () => es.close();
}
