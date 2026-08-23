# NetworkOps AI Agent Demo Guide

## Scenario

The showcase simulates a complete `SW-01 → SERVER-01` link failure inside the
in-memory Network Digital Twin. It does not connect to or modify a real device.

The expected end-to-end evidence is deterministic:

| Projection | Expected value |
|---|---|
| Incident | `INC-DEMO-001` |
| Policy | `REQUIRE_APPROVAL` |
| Approval | `APPROVED` |
| Execution | `SUCCESS` |
| Verification | `PASSED` |

## Start the Showcase

Configure the existing Enterprise `.env` first. The launcher checks that
`JWT_SECRET_KEY` is present; it does not read, print, generate, or change the
secret. When `NETWORKOPS_WORKFLOW_FACTORY` is absent—or still uses the obsolete
unqualified local path—the API child process uses
`demo.networkops_local_factory:create_workflow` without changing the parent
environment.

From the repository root:

```bash
python -m demo
```

The launcher:

1. reuses a healthy Enterprise API on `127.0.0.1:8020`, or starts it;
2. reuses a healthy Console on `127.0.0.1:5173`, or starts it;
3. runs `python -m demo.run_demo`;
4. prints the API and Console addresses;
5. remains in the foreground until `Ctrl+C`.

Enter an operator-issued JWT manually in the Console. The Console retains the
token only in React memory. Stopping the launcher requests graceful shutdown for
up to five seconds and then force-cleans only process trees it created. Existing
services that were reused are never stopped.

## Interview Walkthrough

1. Inject the Digital Twin link failure.
2. Show creation of `INC-DEMO-001`.
3. Follow topology, log, monitoring, and RAG evidence collection.
4. Explain the ranked RCA and its evidence.
5. Inspect the structured, allowlisted repair action.
6. Approve the `REQUIRE_APPROVAL` policy decision.
7. Execute the simulated repair.
8. Confirm recovery and compare Audit, Trace, Governance, and Evaluation views.

## Engineering Rationale

### Why LangGraph

LangGraph makes diagnosis, repair planning, interruption, approval, execution,
and reporting explicit, checkpointable workflow stages. This gives the demo a
visible state transition model instead of hiding orchestration in one prompt.

### Why Human-in-the-loop

High-risk changes pause before side effects. Approval is bound to the structured
plan, so an operator can review the exact action while low-risk paths remain
automatable.

### Why RBAC

Authentication establishes identity; RBAC separates plan creation, approval,
and execution duties. The scenario demonstrates that an authenticated identity
still cannot perform an action outside its assigned permissions.

### Why Audit

Audit provides append-only operational facts for authorization, approval, tool
execution, and verification. Trace explains runtime causality, while Governance
projects those facts into security and compliance views.

## Troubleshooting

- Missing configuration: set the existing JWT configuration before launching.
  The local workflow factory is supplied only to the API child process; the
  launcher never writes `.env` or mutates the parent environment.
- Port occupied but unhealthy: stop or repair the unknown service yourself. The
  launcher deliberately refuses to kill it.
- API or Console startup timeout: inspect the child process output for dependency
  or configuration errors; startup is limited to 30 seconds.
- Scenario-only debugging: run `python -m demo.run_demo` without starting either
  service.
