# NetworkOps AI Agent Architecture

## System Overview

NetworkOps AI Agent v0.2.0 is a read-only Agentic RAG prototype with two compatible orchestration generations:

- v0.1.0 keeps the general quality-control workflow and the dedicated single-agent diagnosis workflow;
- v0.2.0 adds a supervisor-led multi-agent workflow without replacing the v0.1 entry points.

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

## Existing v0.1.0 Workflows

- The general workflow supports intent routing, document grading, query rewriting, answer generation, and answer checking.
- The diagnosis workflow conditionally reads topology, fixed monitoring, redacted logs, and RAG documents before evidence correlation.
- Both factories and the original `demo_main:app` entry point remain available for rollback and compatibility testing.

## Data and API Boundaries

- NetworkX provides topology paths, neighbors, device data, and endpoint interface mappings.
- Monitoring and log StructuredTools return fixed, reproducible, read-only snapshots.
- Chroma collections are cleared and rebuilt at initialization; incremental indexing is not implemented.
- PDF ingestion supports text-based PDFs only and does not include OCR.
- FastAPI exposes health, SSE chat, and process-local history endpoints.

## Safety

The current implementation does not connect to real devices, execute commands, change configurations, restart interfaces, replace modules, or perform automatic repair. Human approval is a rule in the generated plan, not a LangGraph interrupt/checkpoint approval workflow.
