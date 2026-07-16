# NetworkOps AI Agent Workflow

## v0.2.0 Supervisor Flow

The multi-agent workflow uses conditional LangGraph edges around one Supervisor node.

```text
User Query
    |
    v
Supervisor
    |
    +---- Topology Agent (when required) ----+
    |                                        |
    +---- Log Agent (when required) ---------+
    |                                        |
    +---- Diagnosis Agent -------------------+
    |        | monitoring + RAG              |
    |        | document grade/rewrite        |
    |        + evidence correlation          |
    |                                        |
    +---- Repair Agent (when requested) -----+
    |        + plan only                     |
    |                                        |
    +---- Report Agent ----------------------+
             + evidence check                |
                                             v
                                            END
```

Every specialist returns to the Supervisor. The Supervisor selects the next pending agent in the validated order `topology → logs → diagnosis → repair → report`. Unrequested topology and log agents are skipped.

## Shared State

`MultiAgentState` keeps the incident ID, user query, task plan, pending and completed agents, topology context, device evidence, log evidence, retrieved documents, diagnosis result, repair plan, report, bounded quality iterations, and per-agent errors.

The state also retains the v0.1 API fields. Device evidence is mirrored to `metrics`, retrieved knowledge remains in `documents`, and the final report is mirrored to `answer` so the existing FastAPI response contract remains unchanged.

## Quality and Retry Behavior

- Diagnosis RAG can grade and rewrite a low-quality query for at most three retrieval attempts.
- Report checking can reject and regenerate an unsupported report for at most three attempts.
- Agent handoffs have a separate upper bound to prevent routing loops.
- Nodes retry only `ConnectionError` and `TimeoutError`, with at most three attempts.
- Topology and log failures degrade to partial-evidence diagnosis; a diagnosis failure skips repair planning; a report failure returns a safe fallback.

## v0.1.0 Compatibility

The original general and diagnosis workflows remain unchanged as public entry points. The v0.1 demonstration starts through `network_agent_rag.demo_main:app`; the new multi-agent demonstration starts through `network_agent_rag.multi_agent_main:app`.

## v0.3.0 Enterprise Flow

```text
Diagnosis → Repair Plan → Risk Check
                              ├─ low/medium → Execute
                              └─ high/critical or forced approval
                                          ↓
                                      Approval interrupt
                                      ├─ approve → Execute
                                      └─ reject → Report
Execute → Report → END
```

The enterprise API uses `incident_id` as the LangGraph thread ID. Starting an incident streams node progress until either a final answer or an `approval_required` event. Approval resumes the same checkpoint with `Command(resume=...)`. Approval is valid only for the matching structured action digest and before its expiry.

## v0.4.0 Observability Flow

```text
Enterprise API invocation
    ↓
Workflow Span (trace ID + run ID)
    ↓
Agent / Tool / Decision / Approval / Execute child Spans
    ↓
SQLite Trace Store (default) ──→ Metrics summary and /metrics
    │
    └── + Audit Log ──→ Incident Timeline
                         ↓
                    Streamlit Dashboard

Agent / Tool / RAG / Approval / Resume / Execute lifecycle
    ↓
Sanitized Audit TraceEvent ──→ Enterprise Trace API
```

The pre-approval invocation and approval-resume invocation use different run IDs under the same incident trace. Benchmark evaluation runs offline through an injected `module:function` callback; only saved results are exposed to the Dashboard API.

## Output and Safety

The Report Agent returns text through the chat API and Streamlit. It does not export a PDF or Markdown file. The Repair Agent only returns a `not_executed` plan with human-approval, verification, and rollback requirements.

The v0.1 and v0.2 workflows never execute changes. The v0.3 workflow can call only explicitly injected allowlisted handlers; none are included by default. It does not execute free-form report text or generated network commands.
