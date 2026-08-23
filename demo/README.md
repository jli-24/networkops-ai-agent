# Network Fault Simulation Demo

There are two deliberately separate entry points:

- `python -m demo` is the complete showcase launcher. It validates the existing
  Enterprise configuration, reuses or starts the API and React Console, runs the
  scenario, then remains active until `Ctrl+C`.
- `python -m demo.run_demo` runs only the deterministic fault simulation below.
  It never starts or stops the API or Console.

The launcher never creates or prints a JWT, mutates the parent environment, or
writes `.env`. Its API child process defaults to
`demo.networkops_local_factory:create_workflow`, a thin adapter over the existing
Enterprise Workflow. It only stops processes that it created itself.

This standalone scenario demonstrates the existing NetworkOps AI Agent stack
without changing its production APIs or workflow contracts. It creates a local
Digital Twin containing `SW-01` and `SERVER-01`, simulates a complete link
failure, runs the checkpointed Enterprise Workflow, approves the high-risk
repair, restores the simulated link, and verifies recovery.

Run from the repository root:

```powershell
python -m demo.run_demo
```

The command writes the incident checkpoint, audit events, execution spans, and
legacy benchmark result to the same local paths configured for the Enterprise
application. Those projections can be displayed by the existing Operator
Console. Tests use an isolated temporary directory instead.

The scenario is deterministic and local-only. The allowlisted action updates an
in-memory Digital Twin; it does not connect to, configure, or restart a real
network device.

The demonstration covers:

- fixed `INC-DEMO-001` evidence and fault propagation;
- Supervisor, topology, log, diagnosis, RAG, repair, policy, approval, execute,
  and report stages;
- RBAC authorization facts and a high-risk `REQUIRE_APPROVAL` decision;
- Console-readable Trace, Governance, Policy, and Evaluation projections;
- post-execution verification of link state and packet loss.
