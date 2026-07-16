# NetworkOps AI Agent Architecture

## System Overview

NetworkOps AI Agent v0.7.0 keeps three compatible orchestration generations:

- v0.1.0 keeps the general quality-control workflow and the dedicated single-agent diagnosis workflow;
- v0.2.0 adds a supervisor-led multi-agent workflow without replacing the v0.1 entry points.
- v0.3.0 adds an isolated checkpointed enterprise workflow without changing the earlier entry points.
- v0.4.0 adds local execution tracing, metrics, timelines, benchmark results, and an enhanced Streamlit console around the enterprise workflow.
- v0.5.0-alpha adds pluggable production-storage adapters without changing workflow state or API responses.
- v0.6.0 adds optional HS256 JWT authentication without replacing RBAC authorization.
- v0.7.0 adds an explicit production deployment adapter and single-host container stack.

All workflows are dependency injected. The default FastAPI application does not create a model, vector store, or workflow automatically.

## v0.2.0 Multi-Agent Architecture

```text
User / Network Operator
          |
          v
  Streamlit Console
          |
     HTTP + SSE
          |
          v
      FastAPI API
          |
          v
 Supervisor StateGraph
    |      |       |
    v      v       v
Topology  Log   Diagnosis
 Agent   Agent    Agent
                  |  |
                  |  +-- Fixed monitoring snapshot
                  +----- Chroma RAG
    |      |       |
    +------+-------+
           |
           v
      Repair Agent
     (plan only)
           |
           v
      Report Agent
           |
           v
 Text diagnosis report
```

The Supervisor uses a structured callback to create a validated task plan. It dispatches agents sequentially in dependency order and receives every result through a shared `MultiAgentState`. Agents do not call one another directly.

## Agent Responsibilities

- **Topology Agent** reads NetworkX paths and endpoint interfaces.
- **Log Agent** reads deterministic, redacted log snapshots.
- **Diagnosis Agent** reads monitoring evidence, performs RAG retrieval and query rewriting, and correlates evidence into a root cause hypothesis.
- **Repair Agent** creates a risk-classified plan with approval, verification, and rollback requirements. It never executes the plan.
- **Report Agent** produces and checks the final evidence-grounded text report.

The first multi-agent release uses one shared graph, sequential routing, and bounded handoffs. It does not use subgraphs, parallel agents, persistent checkpoints, or agent memory.

## v0.3.0 Enterprise Workflow

The enterprise graph reuses the specialist agent helpers, then adds `RiskCheck`, `Approval`, and `Execute` nodes. An async SQLite checkpointer persists each graph thread under its `incident_id`. High or critical plans, and plans that explicitly require approval, pause through a LangGraph interrupt.

Approval binds the incident, structured action digest, actor label, and 30-minute expiry. Execution is sequential, never retried automatically, and is possible only through explicitly injected allowlisted handlers. The default installation has no handlers and safely blocks execution. Agent, Tool, Decision, and Approval events are written to a separate redacted SQLite audit log.

## v0.4.0 Enterprise Observability

Each Enterprise API invocation creates a workflow Span. Initial execution and approval resume share one trace but use different run IDs. Agent, Tool, Decision, Approval, and Execute spans record parent relationships, attempts, timestamps, duration, status, and error codes. Built-in persistence recursively redacts known sensitive keys and stores summary attributes plus SHA-256 input/output summary digests in the local SQLite Trace Store.

In parallel, an optional `TraceCollector` appends sanitized Agent, Tool, RAG, approval-pause, resume, and repair-execution lifecycle events to the existing Audit Log. The built-in collector preserves insertion order and writes allowlisted summaries plus SHA-256 digests instead of full prompts, document bodies, or exception stacks. Key-based redaction is not a general DLP or tamper-evident audit system, so custom attributes must still avoid sensitive free text.

`create_sqlite_enterprise_app()` keeps the v0.3 two-argument `workflow_factory(checkpointer, audit_log)` contract. v0.4 integrations that need Trace, Metrics, and Collector dependencies use the separate keyword-based `observed_workflow_factory`; exactly one factory is configured and no signature introspection is used.

The observability API provides cursor-based incident listing, per-incident Span Trace and Timeline views, a low-cardinality metrics summary, and Prometheus text exposition. A replaceable `MetricsStore` also exposes an Enterprise JSON snapshot; its default SQLite implementation aggregates existing Span and Audit records at query time and creates no metrics table. The separate Enterprise Trace endpoint returns Audit-backed lifecycle events. The Timeline projection merges Span lifecycle events with the existing Audit Log. Offline JSONL benchmarks use deterministic rule scoring and save JSON results for the Dashboard; the Web API never runs a benchmark job.

The Streamlit console keeps the original chat and evidence view while adding incident creation, approval, Trace, Timeline, metrics, and benchmark tabs. SQLite remains a single-process demonstration backend; OpenTelemetry, OTLP export, centralized time-series storage, and retention policies are not implemented. Optional JWT authentication was added in v0.6.0.

## v0.5.0-alpha Production Storage

Storage is split into three independent domains:

```text
                    Enterprise Workflow
                            |
          -----------------------------------------
          |                  |                    |
      Checkpoint           Trace                Audit
          |                  |                    |
 SQLite/Postgres/Redis  SQLite/Postgres     SQLite/Postgres
```

`AuditStore` and `TraceStore` are structural protocols. The existing `SQLiteAuditLog` and `SQLiteTraceStore` remain unchanged and satisfy those protocols directly. PostgreSQL uses separate `networkops_audit_events` and `networkops_trace_spans` tables; the official LangGraph PostgreSQL Checkpointer owns its own checkpoint tables. Redis is supported only as a LangGraph Checkpointer and is not used for Agent Memory, caching, queues, Pub/Sub, Trace, or Audit.

Audit and Trace retain synchronous interfaces for v0.4 compatibility. FastAPI's asynchronous incident and SSE paths offload these calls to worker threads; synchronous observability routes are already executed in FastAPI's thread pool. PostgreSQL serializes per-node attempt allocation with a transaction-scoped advisory lock and completes spans under a row lock.

`create_sqlite_enterprise_app()` preserves the v0.3/v0.4 SQLite path. The separate `create_storage_enterprise_app()` selects Audit/Trace storage with `STORAGE_BACKEND` and Checkpoint storage with `CHECKPOINT_BACKEND`. Missing URLs, invalid backends, or setup failures stop application startup; there is no silent fallback. v0.5.0-alpha targets new deployments and does not migrate existing SQLite data.

The v0.7.0 Docker Compose extends this storage foundation with an internal API,
Nginx gateway, Prometheus, and Grafana. It requires operator-supplied credentials
and an external production workflow factory; it does not provide automatic
failover, cross-backend replication, or SQLite data migration.

## v0.7.0 Production Deployment

The production ASGI entry point is `deployment.app:create_app`. It loads a
trusted `module:function` workflow factory, then delegates storage and
checkpoint lifecycle management to `create_storage_enterprise_app()`. Production
validation requires PostgreSQL for Audit/Trace, Redis for Checkpoint, JWT
configuration, and both connection URLs. Missing configuration stops startup;
there is no SQLite fallback.

```text
Client -> Nginx -> FastAPI
                     |-- PostgreSQL: Audit and Trace
                     |-- Redis: LangGraph Checkpoint
Prometheus <- /metrics
Grafana    <- Prometheus
```

`GET /health` is a deployment readiness probe for PostgreSQL and Redis. The
legacy `/api/v1/health` response is unchanged. Deployment-only middleware adds
low-cardinality HTTP metrics and authorization-decision projections to the
existing Agent metrics. The container runs as a non-root user with one Uvicorn
worker. Nginx keeps SSE unbuffered and blocks public access to `/metrics`.

This is a single-host reference deployment. It does not include TLS certificate
management, Kubernetes, Helm, a service mesh, automatic scaling, managed secret
storage, database backups, or high availability.

## Existing v0.1.0 Workflows

- The general workflow supports intent routing, document grading, query rewriting, answer generation, and answer checking.
- The diagnosis workflow conditionally reads topology, fixed monitoring, redacted logs, and RAG documents before evidence correlation.
- Both factories and the original `demo_main:app` entry point remain available for rollback and compatibility testing.

## Data and API Boundaries

- NetworkX provides topology paths, neighbors, device data, and endpoint interface mappings.
- Monitoring and log StructuredTools return fixed, reproducible, read-only snapshots.
- Chroma collections are cleared and rebuilt at initialization; incremental indexing is not implemented.
- PDF ingestion supports text-based PDFs only and does not include OCR.
- FastAPI exposes health, SSE chat, and process-local history endpoints. An explicitly created enterprise app also exposes incident, approval, audit, Trace, Timeline, metrics, and benchmark-result endpoints.

## Safety

The repository does not provide real device connectors or built-in change handlers. The v0.3 enterprise graph has interrupt/checkpoint approval and an allowlisted executor contract, but default execution is blocked. v0.6.0 can authenticate Enterprise API callers with HS256 JWT and apply RBAC; it does not provide login, token refresh/revocation, TLS, rate limiting, or a user database. The deployment adapter must remain behind a trusted gateway and platform security controls.
