"""Receives Grafana alert webhooks and kicks off an automated investigation.

On a firing alert, this service:
  1. Pulls the logs and traces around the alert's time window from Loki/Tempo.
  2. Saves everything needed to understand the problem to disk.
  3. Launches the coding assistant (Claude Code, headless) against the repo
     to investigate the root cause.
"""

import json
import logging
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("incident-response")

INCIDENTS_DIR = Path(os.getenv("INCIDENTS_DIR", "incidents"))
LOKI_URL = os.getenv("LOKI_URL", "http://loki:3100")
TEMPO_URL = os.getenv("TEMPO_URL", "http://tempo:3200")
REPO_DIR = os.getenv("REPO_DIR", "/workspace")
LOOKBACK_MINUTES = int(os.getenv("INCIDENT_LOOKBACK_MINUTES", "10"))
SERVICE_SELECTOR = '{service_name="order-tracker"}'

app = FastAPI(title="Incident Responder")


def fetch_logs(start: datetime, end: datetime) -> list[dict]:
    try:
        resp = httpx.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={
                "query": SERVICE_SELECTOR,
                "start": str(int(start.timestamp() * 1_000_000_000)),
                "end": str(int(end.timestamp() * 1_000_000_000)),
                "limit": 500,
                "direction": "forward",
            },
            timeout=10,
        )
        resp.raise_for_status()
        streams = resp.json()["data"]["result"]
        entries = [
            {"labels": stream["stream"], "timestamp": value[0], "line": value[1]}
            for stream in streams
            for value in stream["values"]
        ]
        entries.sort(key=lambda e: e["timestamp"])
        return entries
    except Exception as exc:
        logger.exception("Failed to fetch logs from Loki")
        return [{"error": str(exc)}]


def _tempo_search(query: str, start: datetime, end: datetime) -> list[dict]:
    resp = httpx.get(
        f"{TEMPO_URL}/api/search",
        params={"q": query, "start": int(start.timestamp()), "end": int(end.timestamp()), "limit": 20},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("traces", [])


def fetch_traces(endpoint: str, start: datetime, end: datetime) -> list[dict]:
    queries = []
    if endpoint and endpoint != "unknown":
        queries.append(
            f'{{resource.service.name="order-tracker" && span.http.target="{endpoint}" && span.http.status_code>=500}}'
        )
    queries.append('{resource.service.name="order-tracker" && status=error}')

    summaries: list[dict] = []
    last_error = None
    for query in queries:
        try:
            summaries = _tempo_search(query, start, end)
            if summaries:
                break
        except Exception as exc:
            last_error = exc
    if not summaries and last_error is not None:
        logger.exception("Failed to search traces in Tempo")
        return [{"error": str(last_error)}]

    traces = []
    for summary in summaries[:5]:
        trace_id = summary.get("traceID")
        detail = None
        if trace_id:
            try:
                resp = httpx.get(f"{TEMPO_URL}/api/traces/{trace_id}", timeout=10)
                resp.raise_for_status()
                detail = resp.json()
            except Exception:
                logger.exception("Failed to fetch trace %s", trace_id)
        traces.append({"summary": summary, "detail": detail})
    return traces


def save_incident(alert: dict, logs: list[dict], traces: list[dict]) -> Path:
    incident_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    incident_dir = INCIDENTS_DIR / incident_id
    incident_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "incident_id": incident_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "alert": alert,
        "logs": logs,
        "traces": traces,
    }
    (incident_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    return incident_dir


def run_assistant(incident_dir: Path, alert: dict, endpoint: str) -> None:
    dashboard_url = alert.get("dashboardURL") or alert.get("panelURL") or ""
    report_path = incident_dir / "report.json"
    prompt = (
        f"A Grafana alert fired: {alert.get('labels', {}).get('alertname', '5xx errors')} "
        f"on endpoint {endpoint}. "
        f"An incident report with the matching logs and traces was saved to {report_path}. "
        f"Dashboard: {dashboard_url}. "
        "Read the report, find the root cause of the 5xx responses in this repo, and write a short "
        f"root-cause analysis plus a proposed fix to {incident_dir / 'analysis.md'}. "
        "Only write the analysis file for now -- do not modify any other files."
    )
    log_path = incident_dir / "assistant.log"
    try:
        with open(log_path, "w") as log_file:
            subprocess.Popen(
                [
                    "claude",
                    "-p", prompt,
                    "--output-format", "json",
                    "--dangerously-skip-permissions",
                ],
                cwd=REPO_DIR,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        logger.info("Started coding assistant for incident %s", incident_dir.name)
    except Exception:
        logger.exception("Failed to start coding assistant for incident %s", incident_dir.name)


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.post("/alerts")
async def receive_alert(request: Request):
    payload = await request.json()
    handled = []
    for alert in payload.get("alerts", []):
        if alert.get("status") != "firing":
            continue

        labels = alert.get("labels", {})
        endpoint = labels.get("http_target", "unknown")

        starts_at = alert.get("startsAt", "")
        try:
            fired_at = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        except ValueError:
            fired_at = datetime.now(timezone.utc)
        window_start = fired_at - timedelta(minutes=LOOKBACK_MINUTES)
        window_end = fired_at + timedelta(minutes=LOOKBACK_MINUTES)

        logs = fetch_logs(window_start, window_end)
        traces = fetch_traces(endpoint, window_start, window_end)
        incident_dir = save_incident(alert, logs, traces)
        run_assistant(incident_dir, alert, endpoint)

        handled.append({"incident_id": incident_dir.name, "endpoint": endpoint})

    return {"received": len(payload.get("alerts", [])), "handled": handled}
