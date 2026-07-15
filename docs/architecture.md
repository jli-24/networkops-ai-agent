# NetworkOps AI Agent Architecture

## System Overview

NetworkOps AI Agent is a read-only Agentic RAG prototype for network fault diagnosis.

The current implementation combines:

- LangGraph state management, conditional routing, limited retries, and answer checking;
- RAG retrieval from text-based PDF, Markdown, and TXT documents;
- NetworkX topology loaded from JSON;
- Fixed snapshot diagnosis using deterministic monitoring metrics and redacted logs;
- Evidence correlation across topology, metrics, logs, and knowledge;
- Root cause hypothesis generation with rule-based confidence;
- Text diagnosis report delivery through FastAPI and Streamlit.

## Architecture

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
Compiled LangGraph Workflow
          |
          v
     QueryAnalyzer
          |
          v
Requested Evidence Sources
  |       |       |       |
  v       v       v       v
NetworkX  Fixed   Redacted RAG Retrieval
topology  metrics logs     (Chroma)
  |       |       |       |
  +-------+-------+-------+
          |
          v
  EvidenceCorrelator
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
          v
 Text diagnosis report
```

## Core Components

### API and User Interface

- FastAPI exposes health, SSE chat, and in-memory history endpoints.
- Streamlit displays conversations, source documents, topology paths, and fixed device metrics.
- The default FastAPI application does not create a workflow automatically; deployments inject a compiled graph.

### LangGraph Workflows

- The general workflow supports intent routing, document grading, query rewriting, answer generation, and answer checking.
- The diagnosis workflow conditionally reads requested evidence sources before correlating evidence and generating an answer.
- Connection and timeout failures receive limited retries; answer regeneration is bounded to prevent infinite loops.

### Evidence Sources

- NetworkX provides device relationships, paths, neighbors, and endpoint interface mappings.
- Monitoring and log tools return fixed, reproducible, read-only snapshots.
- Chroma stores rebuilt demo collections and provides semantic retrieval over network operation documents.

### Diagnosis Output and Safety

- Evidence correlation produces a Root cause hypothesis rather than a statistically calibrated failure probability.
- The Text diagnosis report contains evidence, uncertainty, read-only checks, and recommendations.
- The current implementation does not connect to real devices, inject faults, change configurations, or perform automatic repair.
