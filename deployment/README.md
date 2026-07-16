# Production Deployment Adapter

`deployment.app:create_app` is the explicit v0.7.0 ASGI entry point. It does not
ship a demo workflow. Operators must provide an importable, trusted
`NETWORKOPS_WORKFLOW_FACTORY=module:function` that satisfies the existing
`ObservedWorkflowFactory` contract.

## Required production settings

```text
APP_ENV=production
STORAGE_BACKEND=postgres
CHECKPOINT_BACKEND=redis
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
JWT_SECRET_KEY=<at least 32 random bytes>
NETWORKOPS_WORKFLOW_FACTORY=your_package.factory:create_workflow
PROMETHEUS_ENABLED=true
```

The production adapter fails during startup if any required setting is missing.
It never falls back to SQLite. Connection strings and secrets are not included
in configuration error messages.

## Local Compose stack

Copy `.env.example` to `.env`, fill every blank credential and workflow factory,
then run:

```bash
docker compose config
docker compose up --build
```

The gateway listens on `127.0.0.1:8080` by default and Grafana on
`127.0.0.1:3000`. PostgreSQL, Redis, Prometheus, and the API are internal to the
Compose network. Nginx does not expose `/metrics`; Prometheus reads it directly.

`GET /health` is deployment readiness. The legacy
`GET /api/v1/health` contract remains unchanged. This Compose stack is a
single-host reference deployment, not a high-availability design. TLS, secret
management, backups, orchestration, and authenticated Grafana access must be
handled by the deployment environment.
