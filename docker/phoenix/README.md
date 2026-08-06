# Local trace backend

Start Phoenix:

```bash
docker compose -f docker/phoenix/docker-compose.yml up -d
```

Point BuildSleuth at it:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006
```

BuildSleuth exports over OTLP HTTP and appends `/v1/traces` itself, so the base
URL is enough. Open the UI at <http://localhost:6006>. Unset the variable and
tracing turns itself off, no other change needed.

Ports: 6006 serves both the UI and the OTLP HTTP collector, 4317 serves OTLP
gRPC for anything else you want to send here.

## Any OTLP backend works

Nothing in `src/buildsleuth/telemetry/` is Phoenix-specific: it is the vanilla
OpenTelemetry SDK with hand-set GenAI semantic-convention attributes, so any
OTLP collector will do. Jaeger, for example:

```bash
docker run --rm -p 16686:16686 -p 4318:4318 jaegertracing/all-in-one:latest
```

Then use `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` and read the traces
at <http://localhost:16686>.
