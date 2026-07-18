# NetworkOps AI Agent Architecture

## System Overview

NetworkOps AI Agent v0.12.0 keeps three compatible orchestration generations:

- v0.1.0 keeps the general quality-control workflow and the dedicated single-agent diagnosis workflow;
- v0.2.0 adds a supervisor-led multi-agent workflow without replacing the v0.1 entry points.
- v0.3.0 adds an isolated checkpointed enterprise workflow without changing the earlier entry points.
- v0.4.0 adds local execution tracing, metrics, timelines, benchmark results, and an enhanced Streamlit console around the enterprise workflow.
- v0.5.0-alpha adds pluggable production-storage adapters without changing workflow state or API responses.
- v0.6.0 adds optional HS256 JWT authentication without replacing RBAC authorization.
- v0.7.0 adds an explicit production deployment adapter and single-host container stack.
- v0.8.0 formalizes the existing Enterprise Security Layer without adding a second authorization path.
- v0.9.0 formalizes the existing HS256 JWT Authentication Layer as the trusted identity source for RBAC.
- v0.10.0 adds an auth-owned identity lifecycle without changing workflow state or API contracts.
- v0.11.0 adds read-only governance projections, deterministic risk scoring, and ephemeral compliance reports over existing Audit and Trace facts.
- v0.12.0 adds a deterministic policy gate between RBAC authorization and execution side effects.

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

`AuditStore` and `TraceStore` are structural protocols. The existing `SQLiteAuditLog` and `SQLiteTraceStore` remain unchanged and satisfy those protocols directly. PostgreSQL uses separate `networkops_audit_events` and `networkops_trace_spans` tables; the official LangGraph PostgreSQL Checkpointer owns its own checkpoint tables. `REDIS_URL` remains Checkpoint-only. v0.10 uses a separate `IDENTITY_REDIS_URL` and auth-owned namespace; neither Redis domain is used for Agent Memory, queues, Trace, or Audit.

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
configuration, and a separate Identity Redis URL. Missing configuration stops startup;
there is no SQLite fallback.

```text
Client -> Nginx -> FastAPI
                     |-- PostgreSQL: Audit and Trace
                     |-- Redis: LangGraph Checkpoint
                     |-- Identity Redis: Session and credential lifecycle
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

## v0.8.0 Enterprise Security Layer

The Enterprise Workflow reuses the existing `UserContext`,
`AuthorizationError`, `require_permission()`, and `authorizing_role()` APIs.
Authorization runs before repair-plan creation, after approval interrupt resume,
and before execution tracing, Tool Audit, or executor side effects. These stages
require `CREATE_REPAIR_PLAN`, `APPROVE_REPAIR`, and `EXECUTE_REPAIR`
respectively.

When `rbac_contexts` is completely absent, direct local graph calls retain the
legacy behavior. Once an incident enters explicit RBAC mode, invalid mappings,
missing stage contexts, and insufficient permissions fail closed; checkpoint
resume cannot downgrade the incident to legacy mode.

Authorization decisions remain in the existing Audit schema. New Workflow
events store actor ID, authorizing role, and permission in `details`, use fixed
`authorize_repair_plan`, `authorize_approval`, or `authorize_execution` actions,
and use top-level `allowed` or `denied` outcomes. Governance is a read-only
projection of those records into permission, action, and decision.

`UserContext` exists only in LangGraph `RunnableConfig`. It is not part of
EnterpriseState, checkpoint values, API request or response schemas, or SSE
payloads. The layer does not add login, OAuth2, LDAP, SSO, a user database, or a
new permission model.

## v0.9.0 Authentication Layer

The Authentication Layer reuses the HS256/PyJWT implementation introduced in
v0.6.0. `JWTProvider` validates a Bearer token with the configured secret and
requires `sub`, `username`, `roles`, `iat`, and `exp`. A valid token becomes an
immutable `UserIdentity`, then a `UserContext`; the existing RBAC layer alone
decides what that identity may do.

```text
Bearer JWT
    -> JWTProvider
    -> UserIdentity
    -> UserContext
    -> RBAC Permission Check
    -> LangGraph RunnableConfig
    -> Enterprise Workflow
```

Missing, malformed, expired, future-issued, tampered, or incorrectly signed
tokens return HTTP 401 with a Bearer challenge. Authenticated callers without
the required permission return HTTP 403. Authentication Audit uses fixed
`authenticate_success` and `authenticate_failed` actions and stores only actor
ID, source, and decision; it does not store tokens, credentials, usernames,
email addresses, or request bodies.

Development keeps the explicit legacy provider path when authentication is not
configured. Production requires an operator-provided JWT secret of at least 32
bytes and never supplies a default. Authentication and legacy context providers
are mutually exclusive. Identity objects remain outside Workflow State,
Checkpoint, API schemas, and SSE payloads.

The v0.9 layer did not include refresh or revocation. The project still does not
implement OAuth2, OIDC, JWKS, LDAP, Active Directory, SSO, MFA, or a user
database. The approval payload `actor` remains a business label and is not
bound to the JWT subject.

## v0.10.0 Enterprise Identity Enhancement

The Authentication Layer now owns an independent `IdentityStore`. Development
and tests can use the in-memory implementation; production uses an auth-owned
Redis namespace configured through `IDENTITY_REDIS_URL`. It does not implement
the project Storage Protocol and cannot write Workflow State, Checkpoint,
Trace, or API payloads.

Session-bound access tokens default to 15 minutes. Refresh tokens default to
seven days, rotate atomically in Identity Redis, and revoke the entire session
when an already-rotated token is reused. The v0.9 `create_token()` and
`verify_token()` contract remains available for legacy tokens until their own
expiration.

API keys bind an immutable existing `UserIdentity`; they do not define a second
identity, role, permission, or scope model. Successful API-key authentication
continues through `UserContext`, the existing RBAC permission checks, and
RunnableConfig. Revoking a key changes only credential authentication state.

Audit records only non-secret references such as `session_id` and `key_id`.
Tokens, API keys, credential hashes, secret hashes, and fingerprints are not
projected into Audit, Trace, Metrics, Governance, State, Checkpoint, SSE, or API
responses. No identity-management HTTP routes are added in this release.

## v0.11.0 Governance & Compliance Layer

The top-level `governance` package composes the existing Audit Store, Trace
Store, authorization projection, and Incident Timeline. It does not participate
in LangGraph execution and cannot mutate Workflow, Agent, Repair, Identity,
RBAC, Checkpoint, Trace, or Metrics state.

```text
Audit + Trace
     |
     v
Security Event Center
     +--> deterministic RiskScorer
     +--> in-memory Compliance Report
     +--> query-time Prometheus counters
```

Governance methods are read-only unless they perform one of three explicit
append-only Audit operations: `sync_security_events()` writes only
`security_event_created`, risk calculation writes only
`risk_assessment_created`, and report generation writes only
`compliance_report_generated`. No Audit, Trace, Metrics, Checkpoint, or Storage
schema changes are required.

Security Events retain only minimal references to source Audit or Trace facts.
Risk scoring is a deterministic 0-100 ruleset based on operation type, target
device count, authorization-denial history, and repair-failure history; roles
are attribution only and do not change the score. Compliance reports are strict
in-memory models. Their full content is not persisted; Audit records only the
report ID, evidence counts, risk level, and a canonical SHA-256 digest.

The existing `/metrics` exposition adds low-cardinality Security Event,
authorization-denial, high-risk-operation, and compliance-report counters. No
new public Governance HTTP API, ABAC, tenant model, OAuth2, or
OIDC integration is introduced.

## v0.12.0 Agent Policy Engine

The `policy` package is an independent deterministic execution gate. It does
not add a LangGraph node or change EnterpriseState. Policy input and output are
runtime-only objects built after the existing execution RBAC check.

```text
Authentication -> UserContext -> RBAC -> Policy Engine
    -> RiskCheck / existing Approval -> Trace -> Tool Audit -> Executor
    -> Audit -> Governance
```

The registry is immutable after construction and maps every supported tool to
an operation explicitly. Conditions support only AND-combined operation, risk,
permission, role, and device-count constraints. Unknown tools and unmatched
contexts are denied. Every action is evaluated separately, while device-count
conditions use the deduplicated target set for the complete Repair Plan. When
several rules or Repair Actions apply, precedence is
`DENY > REQUIRE_APPROVAL > ALLOW`; a canonical SHA-256 makes identical inputs
produce the same decision identifier.

`REQUIRE_APPROVAL` reuses the existing interrupt/checkpoint approval flow.
`DENY` returns the existing blocked execution result before execution Trace,
Tool Audit, or executor side effects. Policy Audit uses fixed actions and
allowlisted details; Governance maps denial and approval-required facts to the
existing security-event types.

The policy-aware workflow factory is explicit and receives a `PolicyEngine`.
Legacy factories remain compatible when no engine is injected. Production
configuration requires `POLICY_ENGINE_ENABLED=true` and fails startup rather
than silently falling back. Policy does not replace Authentication, RBAC,
RiskCheck, Human Approval, Audit, or Governance, and Policy objects never enter
State, Checkpoint, API responses, or SSE.

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

The repository does not provide real device connectors or built-in change handlers. The v0.3 enterprise graph has interrupt/checkpoint approval and an allowlisted executor contract, but default execution is blocked. The Authentication Layer can validate legacy or session-bound JWTs and API keys before existing RBAC checks; it does not provide login, identity-management HTTP APIs, OAuth/OIDC, TLS, rate limiting, or a user database. The deployment adapter must remain behind a trusted gateway and platform security controls.
