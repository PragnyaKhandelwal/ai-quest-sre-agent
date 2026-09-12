"""
backend/mock_generator.py

Generates realistic Prometheus/PagerDuty-style alert payloads and a matching
mock log corpus (50+ lines) for each of the 4 canonical incident scenarios
used throughout the demo and judged via POST /simulate/{1..4}.

The log corpora deliberately contain the exact keyword evidence that
agents/diagnostician_agent.py's local-simulation heuristic looks for, so
the diagnosis returned in local-simulation mode is always grounded in
lines that are actually present here -- nothing is hallucinated.
"""
from __future__ import annotations

import time
from typing import List, Tuple

from agents.schemas import Alert

_BASE_TIME = time.time()


def _ts(offset_seconds: float) -> str:
    t = time.localtime(_BASE_TIME - 300 + offset_seconds)
    return time.strftime("%Y-%m-%dT%H:%M:%S", t)


# ---------------------------------------------------------------------------
# Scenario 1: Memory Leak (P1) -- payment-service
# ---------------------------------------------------------------------------
def _scenario_1() -> Tuple[List[Alert], List[str]]:
    alerts = [
        Alert(
            source="prometheus",
            service="payment-service",
            namespace="production",
            alertname="PodOOMKilled",
            severity_hint="critical",
            title="payment-service pod OOMKilled",
            description="Container payment-service-7d9f8c-x2k4p was OOMKilled after exceeding memory limit (512Mi).",
            labels={"pod": "payment-service-7d9f8c-x2k4p", "reason": "OOMKilled"},
        ),
        Alert(
            source="prometheus",
            service="payment-service",
            namespace="production",
            alertname="HighMemoryUsage",
            severity_hint="critical",
            title="payment-service memory usage above 95%",
            description="Container memory usage for payment-service has exceeded 95% of the configured limit for 10 minutes.",
            labels={"threshold": "95%"},
        ),
        Alert(
            source="pagerduty",
            service="payment-service",
            namespace="production",
            alertname="CrashLoopBackOff",
            severity_hint="critical",
            title="payment-service repeated pod restarts",
            description="payment-service has restarted 6 times in the last 15 minutes (CrashLoopBackOff).",
            labels={"restart_count": "6"},
        ),
    ]

    logs = []
    logs.append(f"{_ts(0)} INFO  payment-service startup complete, connection pool size=20")
    for i in range(1, 30):
        rss = 180 + i * 12
        logs.append(
            f"{_ts(i*10)} INFO  payment-service [pool-worker-{i%5}] processed txn id=txn_{1000+i} pool.active={min(20, i)} pool.idle={max(0, 20-i)} rss_mb={rss}"
        )
        if i % 6 == 0:
            logs.append(f"{_ts(i*10+2)} WARN  payment-service connection pool: leaked connection detected, not returned after txn_{1000+i}, heap growing")
    logs.append(f"{_ts(310)} WARN  payment-service memory usage at 89% of limit (512Mi), heap fragmentation increasing")
    logs.append(f"{_ts(320)} WARN  payment-service GC pause 1200ms, heap still climbing, connection pool exhausted of idle connections")
    logs.append(f"{_ts(330)} ERROR payment-service OutOfMemoryError: Java heap space in ConnectionPoolManager.acquire()")
    logs.append(f"{_ts(331)} ERROR kubelet  Memory cgroup out of memory: Killed process payment-service (pid 1) total-vm rss_mb=511")
    logs.append(f"{_ts(332)} ERROR kubelet  Container payment-service-7d9f8c-x2k4p in pod payment-service-7d9f8c-x2k4p was OOMKilled")
    logs.append(f"{_ts(340)} INFO  kubelet  Restarting container payment-service-7d9f8c-x2k4p (restart 4/6)")
    logs.append(f"{_ts(345)} WARN  payment-service connection pool leak persists across restart: pool.idle=0 pool.active=20 rss_mb=498")
    logs.append(f"{_ts(360)} ERROR kubelet  Container payment-service-7d9f8c-x2k4p was OOMKilled (restart 5/6)")
    logs.append(f"{_ts(380)} ERROR kubelet  Container payment-service-7d9f8c-x2k4p was OOMKilled (restart 6/6)")
    logs.append(f"{_ts(390)} ERROR alertmanager  CrashLoopBackOff detected for payment-service, escalating to on-call")
    return alerts, logs


# ---------------------------------------------------------------------------
# Scenario 2: Bad Deploy (P2) -- api-gateway
# ---------------------------------------------------------------------------
def _scenario_2() -> Tuple[List[Alert], List[str]]:
    alerts = [
        Alert(
            source="prometheus",
            service="api-gateway",
            namespace="production",
            alertname="HighErrorRate",
            severity_hint="high",
            title="api-gateway error rate spike to 45%",
            description="5xx error rate for api-gateway jumped from 0.5% to 45% within 3 minutes of deployment.",
            labels={"error_rate": "45%"},
        ),
        Alert(
            source="pagerduty",
            service="api-gateway",
            namespace="production",
            alertname="DeploymentRegression",
            severity_hint="high",
            title="Regression detected after image push v2.3.1",
            description="New revision api-gateway:v2.3.1 shows sustained 5xx responses immediately after rollout.",
            labels={"image": "api-gateway:v2.3.1"},
        ),
    ]

    logs = []
    logs.append(f"{_ts(0)} INFO  ci-cd  deploy started: api-gateway image=api-gateway:v2.3.1 rollout strategy=RollingUpdate")
    logs.append(f"{_ts(5)} INFO  kubelet  Pulling image api-gateway:v2.3.1")
    logs.append(f"{_ts(15)} INFO  kubelet  Successfully pulled image api-gateway:v2.3.1")
    logs.append(f"{_ts(20)} INFO  kubelet  Started container api-gateway (revision v2.3.1)")
    logs.append(f"{_ts(22)} ERROR api-gateway  Missing required environment variable: DOWNSTREAM_AUTH_URL")
    logs.append(f"{_ts(23)} ERROR api-gateway  Failed to initialize auth client: environment variable DOWNSTREAM_AUTH_URL not set")
    for i in range(1, 25):
        logs.append(f"{_ts(23+i*3)} ERROR api-gateway  request_id={i} GET /v1/orders -> 502 Bad Gateway (auth client not initialized)")
        if i % 5 == 0:
            logs.append(f"{_ts(23+i*3+1)} WARN  ingress  api-gateway 5xx error rate now at {5*i}% over last minute")
    logs.append(f"{_ts(130)} WARN  ingress  api-gateway error rate sustained at 45%, alert threshold breached")
    logs.append(f"{_ts(135)} ERROR api-gateway  environment variable DOWNSTREAM_AUTH_URL missing since rollout of v2.3.1")
    logs.append(f"{_ts(140)} INFO  ci-cd  previous stable revision recorded: api-gateway:v2.3.0")
    logs.append(f"{_ts(145)} WARN  alertmanager  correlating error spike with recent rollout event (image v2.3.1)")
    return alerts, logs


# ---------------------------------------------------------------------------
# Scenario 3: DB Deadlock (P1) -- order-service -> postgres-primary
# ---------------------------------------------------------------------------
def _scenario_3() -> Tuple[List[Alert], List[str]]:
    alerts = [
        Alert(
            source="prometheus",
            service="order-service",
            namespace="production",
            alertname="QueryTimeoutCascade",
            severity_hint="critical",
            title="order-service query timeout cascade",
            description="order-service is experiencing cascading query timeouts against postgres-primary.",
            labels={},
        ),
        Alert(
            source="prometheus",
            service="postgres-primary",
            namespace="production",
            alertname="LockTimeoutExceeded",
            severity_hint="critical",
            title="postgres-primary lock_timeout exceeded",
            description="Multiple sessions on postgres-primary have exceeded lock_timeout waiting on the orders table.",
            labels={"table": "orders"},
        ),
        Alert(
            source="pagerduty",
            service="order-service",
            namespace="production",
            alertname="ConnectionPoolExhausted",
            severity_hint="critical",
            title="order-service connection pool exhausted",
            description="order-service database connection pool is fully exhausted waiting on blocked queries.",
            labels={},
        ),
    ]

    logs = []
    logs.append(f"{_ts(0)} INFO  order-service  processing checkout request order_id=88213")
    logs.append(f"{_ts(2)} INFO  postgres-primary  session pid=4471 BEGIN transaction (order_id=88213)")
    logs.append(f"{_ts(3)} INFO  postgres-primary  session pid=4471 UPDATE orders SET status='processing' WHERE id=88213")
    logs.append(f"{_ts(120)} WARN  postgres-primary  session pid=4471 long-running transaction detected, duration=120s, holding row lock on orders id=88213")
    for i in range(1, 25):
        logs.append(f"{_ts(120+i*4)} ERROR order-service  query timeout: UPDATE orders SET status=... waiting on lock held by pid=4471 (attempt {i})")
        if i % 5 == 0:
            logs.append(f"{_ts(120+i*4+1)} WARN  postgres-primary  lock_timeout exceeded for session pid={4471+i} waiting on orders table")
    logs.append(f"{_ts(230)} ERROR order-service  connection pool exhausted: 0/50 connections available, all blocked on orders table lock")
    logs.append(f"{_ts(232)} ERROR postgres-primary  pg_stat_activity: 47 sessions in 'active' state waiting on relation orders")
    logs.append(f"{_ts(235)} WARN  postgres-primary  no statement_timeout configured on orders_db, long-running transaction pid=4471 still active at 235s")
    logs.append(f"{_ts(240)} ERROR alertmanager  deadlock-like cascade confirmed: query timeout cascade + lock_timeout exceeded + connection pool exhausted")
    return alerts, logs


# ---------------------------------------------------------------------------
# Scenario 4: Node Disk Full (P2) -- logging-agent on worker-3
# ---------------------------------------------------------------------------
def _scenario_4() -> Tuple[List[Alert], List[str]]:
    alerts = [
        Alert(
            source="prometheus",
            service="logging-agent",
            namespace="kube-system",
            alertname="NodeDiskPressure",
            severity_hint="high",
            title="worker-3 disk usage at 98%",
            description="Node worker-3 disk usage has reached 98%, DiskPressure condition is true.",
            labels={"node": "worker-3", "disk_usage": "98%"},
        ),
        Alert(
            source="prometheus",
            service="logging-agent",
            namespace="kube-system",
            alertname="PodEvictionStarted",
            severity_hint="high",
            title="Pod evictions starting on worker-3",
            description="Kubelet on worker-3 has begun evicting pods due to disk pressure.",
            labels={"node": "worker-3"},
        ),
    ]

    logs = []
    logs.append(f"{_ts(0)} INFO  logging-agent  audit log rotation policy: none configured")
    for i in range(1, 30):
        usage = 60 + i
        logs.append(f"{_ts(i*15)} INFO  node-exporter  worker-3 disk usage={min(98, usage)}% /var/log partition")
        if usage % 10 == 0:
            logs.append(f"{_ts(i*15+2)} WARN  logging-agent  audit log directory growing unbounded, no logrotate configured, current size increasing")
    logs.append(f"{_ts(440)} WARN  kubelet  worker-3 disk usage 92%, approaching eviction threshold")
    logs.append(f"{_ts(450)} WARN  kubelet  worker-3 disk usage 95%, DiskPressure condition set to True")
    logs.append(f"{_ts(460)} ERROR kubelet  worker-3 disk usage 98%, no space left on device for /var/log")
    logs.append(f"{_ts(462)} WARN  kubelet  worker-3 beginning pod eviction due to DiskPressure")
    logs.append(f"{_ts(465)} WARN  kubelet  evicted pod fluentd-worker-3-abcde (reason: DiskPressure)")
    logs.append(f"{_ts(468)} WARN  kubelet  evicted pod metrics-agent-worker-3-fghij (reason: DiskPressure)")
    logs.append(f"{_ts(470)} ERROR logging-agent  audit log write failed: no space left on device")
    logs.append(f"{_ts(475)} ERROR alertmanager  confirmed root cause candidate: missing logrotate config on worker-3, audit logs consumed remaining disk")
    return alerts, logs


# ---------------------------------------------------------------------------
# Scenario 5: CPU Throttling (P2) -- ml-inference-service
# ---------------------------------------------------------------------------
def _scenario_5() -> Tuple[List[Alert], List[str]]:
    alerts = [
        Alert(
            source="prometheus",
            service="ml-inference-service",
            namespace="production",
            alertname="CPUThrottleHigh",
            severity_hint="high",
            title="ml-inference-service CPU throttle ratio at 89%",
            description="Container CPU throttling ratio for ml-inference-service has reached 89% over the last 10 minutes.",
            labels={"throttle_ratio": "89%"},
        ),
        Alert(
            source="prometheus",
            service="ml-inference-service",
            namespace="production",
            alertname="LatencySpike",
            severity_hint="high",
            title="ml-inference-service p99 latency spike to 8s",
            description="p99 inference latency for ml-inference-service has spiked from 400ms to 8000ms.",
            labels={},
        ),
        Alert(
            source="pagerduty",
            service="ml-inference-service",
            namespace="production",
            alertname="InferenceTimeout",
            severity_hint="high",
            title="ml-inference-service model inference timeout",
            description="Multiple model inference requests are timing out after 10s.",
            labels={},
        ),
    ]

    logs = []
    logs.append(f"{_ts(0)} INFO  ci-cd  deploy started: ml-inference-service image=ml-inference:v4.2.0 (model upgrade to v4)")
    logs.append(f"{_ts(5)} INFO  kubelet  Started container ml-inference-service (revision v4.2.0) cpu.limit=500m cpu.request=250m")
    logs.append(f"{_ts(8)} INFO  ml-inference-service  model v4 loaded, larger architecture than v3 (2.1x more FLOPs per inference)")
    for i in range(1, 28):
        throttle = min(89, 20 + i * 3)
        logs.append(f"{_ts(i*8)} WARN  node-exporter  ml-inference-service cpu throttle ratio={throttle}% cpu.limit=500m")
        if i % 5 == 0:
            logs.append(f"{_ts(i*8+2)} WARN  ml-inference-service  p99 inference latency degrading, currently {400 + i*250}ms")
    logs.append(f"{_ts(230)} ERROR ml-inference-service  p99 inference latency spike to 8000ms, cpu throttle ratio=89%")
    logs.append(f"{_ts(235)} ERROR ml-inference-service  model inference timeout after 10s, request queue backing up")
    logs.append(f"{_ts(238)} ERROR ml-inference-service  model inference timeout after 10s (request_id=8842)")
    logs.append(f"{_ts(240)} WARN  kubelet  container ml-inference-service throttled by cgroup cpu.cfs_quota (limit=500m, no increase since v3)")
    logs.append(f"{_ts(245)} ERROR alertmanager  confirmed root cause candidate: cpu limits not updated after model upgrade to v4, container being throttled")
    return alerts, logs


# ---------------------------------------------------------------------------
# Scenario 6: Certificate Expiry (P3) -- api-gateway TLS cert
# ---------------------------------------------------------------------------
def _scenario_6() -> Tuple[List[Alert], List[str]]:
    alerts = [
        Alert(
            source="prometheus",
            service="api-gateway",
            namespace="production",
            alertname="CertificateExpiringSoon",
            severity_hint="medium",
            title="api-gateway TLS certificate expires in 4 hours",
            description="The TLS certificate for api-gateway (api.example.com) expires in approximately 4 hours.",
            labels={"cert": "api-gateway-tls"},
        ),
        Alert(
            source="prometheus",
            service="api-gateway",
            namespace="production",
            alertname="TLSHandshakeFailures",
            severity_hint="medium",
            title="api-gateway HTTPS handshake failures starting",
            description="Clients are beginning to see HTTPS handshake failures against api-gateway.",
            labels={},
        ),
    ]

    logs = []
    logs.append(f"{_ts(0)} INFO  cert-manager  certificate api-gateway-tls renewal check: expires in 30 days, no action needed")
    for i in range(1, 20):
        logs.append(f"{_ts(i*20)} INFO  cert-manager  certificate api-gateway-tls renewal attempt {i} scheduled")
        if i % 4 == 0:
            logs.append(f"{_ts(i*20+3)} WARN  cert-manager  ACME challenge for api-gateway-tls: DNS-01 propagation check timed out")
    logs.append(f"{_ts(390)} ERROR cert-manager  certificate api-gateway-tls renewal failed: ACME challenge DNS timeout after 5 retries")
    logs.append(f"{_ts(395)} WARN  cert-manager  certificate api-gateway-tls expires in 4 hours, renewal still failing")
    logs.append(f"{_ts(400)} ERROR ingress  TLS handshake failure for api.example.com: certificate nearing expiry")
    logs.append(f"{_ts(405)} ERROR ingress  TLS handshake failure for api.example.com (client_ip=203.0.113.42)")
    logs.append(f"{_ts(410)} WARN  alertmanager  no cert-expiry alert existed at 30-day threshold; first alert fired at 4-hour mark")
    logs.append(f"{_ts(415)} ERROR alertmanager  confirmed root cause candidate: cert-manager renewal failed due to ACME challenge DNS timeout")
    return alerts, logs


_SCENARIOS = {
    1: ("Memory Leak (P1) - payment-service", _scenario_1),
    2: ("Bad Deploy (P2) - api-gateway", _scenario_2),
    3: ("DB Deadlock (P1) - order-service/postgres-primary", _scenario_3),
    4: ("Node Disk Full (P2) - logging-agent/worker-3", _scenario_4),
    5: ("CPU Throttling (P2) - ml-inference-service", _scenario_5),
    6: ("Certificate Expiry (P3) - api-gateway TLS cert", _scenario_6),
}


def get_scenario(scenario_number: int) -> Tuple[str, List[Alert], List[str]]:
    if scenario_number not in _SCENARIOS:
        raise ValueError(f"Unknown scenario {scenario_number}. Valid: 1-6.")
    name, builder = _SCENARIOS[scenario_number]
    alerts, logs = builder()
    # Fresh timestamps per trigger so repeated demo runs look live.
    for alert in alerts:
        alert.started_at = time.time()
    return name, alerts, logs


def list_scenarios() -> List[dict]:
    return [{"id": sid, "name": name} for sid, (name, _) in _SCENARIOS.items()]


# ---------------------------------------------------------------------------
# Tool-calling helpers (agents/tools.py): arbitrary log/metric lookups for a
# service, independent of the /simulate/{n} incident flow. Backed by the
# same scenario log corpora so results stay grounded in real, inspectable
# data rather than being fabricated per call.
# ---------------------------------------------------------------------------
def get_logs_for_service(service: str, namespace: str = "production") -> List[str]:
    """Return the log corpus for whichever canonical scenario matches this
    service+namespace, or an empty list if none match."""
    for _sid, (_name, builder) in _SCENARIOS.items():
        alerts, logs = builder()
        if any(a.service == service and a.namespace == namespace for a in alerts):
            return logs
    return []


_METRIC_BASELINES = {
    ("payment-service", "memory_usage_percent"): (91.0, 85.0, "rising"),
    ("api-gateway", "error_rate"): (45.0, 5.0, "rising"),
    ("order-service", "connection_pool_usage_percent"): (100.0, 90.0, "stable"),
    ("logging-agent", "disk_usage_percent"): (98.0, 80.0, "rising"),
    ("ml-inference-service", "cpu_throttle_ratio"): (89.0, 70.0, "rising"),
    ("api-gateway", "cert_expiry_hours"): (4.0, 720.0, "falling"),
}


def get_metrics_for_service(service: str, metric_name: str) -> dict:
    """Return a deterministic mock metric reading for a service+metric pair.
    Falls back to a generic healthy-looking reading for unknown combos so
    the tool never crashes on an unrecognized query."""
    current, threshold, trend = _METRIC_BASELINES.get(
        (service, metric_name), (12.0, 80.0, "stable")
    )
    return {
        "metric_name": metric_name,
        "current_value": current,
        "threshold": threshold,
        "is_breaching": current >= threshold,
        "trend": trend,
    }
