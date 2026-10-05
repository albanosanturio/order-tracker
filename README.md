# Order Tracker

A small order tracking app for the AI Dev Tools Zoomcamp observability homework. It includes a web page, API, tests, and a Docker Compose setup. You add telemetry, alerts, and an incident responder in Homework 4.

The main user flow is creating an order and checking its status. Three sample orders are created on first startup.

## Run it

You need Docker with Compose. To run the tests, you also need Python 3.11+ and `uv`.

```bash
docker compose up --build -d --wait
```

Open <http://127.0.0.1:8000>. The API is at `/api/orders`, and the health check is at `/healthz`. Data is stored in a Docker volume and survives container recreation.

If port 8000 is occupied, set `ORDER_TRACKER_PORT`, for example:

```bash
ORDER_TRACKER_PORT=18080 docker compose up --build -d --wait
```

Run tests with `uv run --frozen pytest -q`. Stop the app with `docker compose down`. Add `-v` only if you also want to delete the order data.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Web page |
| GET | `/healthz` | Database health check |
| GET | `/api/orders` | List orders |
| POST | `/api/orders` | Create an order |
| GET | `/api/orders/{id}` | Check an order |
| PATCH | `/api/orders/{id}` | Change an order status |

The app uses SQLite to keep setup small. Run one app container at a time. The course exercise is about detecting and handling an incident, not scaling the database.

## Telemetry

The app emits OpenTelemetry traces, metrics, and logs. `docker compose up` also starts an OpenTelemetry Collector, Prometheus, Loki, Tempo, and Grafana (config in `observability/`). The app ships all three signals to the Collector over OTLP, which fans them out: metrics to Prometheus, logs to Loki, traces to Tempo.

Every request gets an HTTP span and a `http_server_duration_milliseconds` metric tagged with the route (`http_target`) and status code (`http_status_code`). Order lookups (`GET /api/orders/{id}`) additionally get a dedicated `order_lookup` span, an `order_lookups_total` counter (tagged by `found`), and an info-level log line, all correlated by trace ID.

Open <http://127.0.0.1:3000> for Grafana (anonymous access, no login needed) and look at the "Order Tracker - Requests" dashboard for request counts and errors. Use Grafana's Explore view with the Loki or Tempo datasource to dig into individual logs or traces. Prometheus itself is at <http://127.0.0.1:9090> if you want to query metrics directly.

`docker compose logs app` still prints plain-text request logs for quick inspection without opening Grafana.

Unhandled exceptions (uvicorn's own error logging) are captured too, with the full traceback attached as structured metadata on the log entry, not just printed to the console.

## Alerting

A Grafana alert rule (`observability/grafana/provisioning/alerting/`) fires when any endpoint returns a 5xx response, checking a 5-minute window every minute. It's labeled per-endpoint, links back to the dashboard's error panel, and treats "no 5xx happened" as Normal rather than "no data". Alerts are routed to the incident responder below via a webhook contact point.

## Incident response

`incident-response/` is a separate service (port 8001) that receives Grafana's alert webhook at `POST /alerts`. On a firing alert it:

1. Pulls the logs and traces from Loki/Tempo around the alert's time window.
2. Saves a report (`report.json`) with the alert, logs, and traces to a Docker volume, under an incident ID.
3. Launches the Claude Code CLI in headless mode (`claude -p ... --dangerously-skip-permissions`), pointed at this repo, to investigate the root cause and write a root-cause analysis to `analysis.md` next to the report.

To let step 3 actually run, set `ANTHROPIC_API_KEY` in your shell or a `.env` file before `docker compose up` — without it, the assistant starts but fails to authenticate, and nothing else happens.

**Security note:** the repo is bind-mounted read-write into this container, and the assistant runs with `--dangerously-skip-permissions` (no approval prompts for any file edit or command). The prompt asks it to only write `analysis.md`, but that's not enforced — the agent can do more if it decides to. This is fine for a local/trusted demo; don't point this at a repo or credentials you wouldn't want an unsupervised agent to touch.
