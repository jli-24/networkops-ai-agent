# NetworkOps AI Agent Workflow

## Diagnosis Pipeline

The diagnosis workflow uses conditional LangGraph edges to collect only the evidence sources requested by the query analysis result.

```text
User Query
    |
    v
QueryAnalyzer
    |
    v
Requested Evidence Sources
    |
    +---- NetworkX topology
    |
    +---- Fixed monitoring snapshot
    |
    +---- Redacted log snapshot
    |
    +---- RAG knowledge retrieval
    |
    v
Evidence correlation
    |
    v
Root cause hypothesis
    |
    v
Generator
    |
    v
HallucinationChecker
    |
    +---- rejected --> Generator (maximum three generations)
    |
    v
Text diagnosis report
```

## Evidence Handling

- Unrequested evidence nodes are skipped through conditional edges.
- Topology, monitoring, logs, and retrieved documents remain separate in workflow state.
- Fixed snapshot diagnosis keeps the demonstration deterministic and reproducible.
- Evidence references support the diagnosis conclusion and recommendations.

## Quality and Retry Behavior

- Reader nodes retry only `ConnectionError` and `TimeoutError`, with at most three attempts.
- The checker validates that conclusions, confidence, and recommendations are supported by collected evidence.
- A rejected answer returns to the generator, with at most three answer generations.
- Contract violations and exhausted retries terminate with a structured error instead of an unbounded loop.

## Output and Safety

The Text diagnosis report is the text returned through the chat API and displayed by Streamlit. It is not a PDF or Markdown file export.

The workflow is read-only. It does not execute device commands, modify configurations, inject faults, restart interfaces, or perform automatic repair.
