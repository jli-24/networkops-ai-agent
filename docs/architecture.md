# NetworkOps AI Agent Architecture


## System Overview

NetworkOps AI Agent is an Agentic RAG based network fault diagnosis system.

The system combines:

- LangGraph Agent Workflow
- RAG Knowledge Retrieval
- NetworkX Network Simulation
- Automated Diagnosis Pipeline


## Architecture

```text
                 User
                   |
                   |
              FastAPI API
                   |
                   |
          LangGraph Workflow
                   |
    --------------------------------
    |              |               |
Diagnosis     Knowledge        Network
 Agent        Retrieval        Tools
    |              |               |
    |          Chroma DB       NetworkX
    |                          Simulator## Core Components


### Agent Workflow

Responsible for:

- Task planning
- Tool calling
- State management
- Diagnosis reasoning


### RAG Module

Responsible for:

- Knowledge retrieval
- Fault case matching
- Operation document search


### Network Simulator

Provides:

- Network topology
- Device status
- Link analysis
- Fault simulation
    |
Root Cause Analysis

                   |
                   |
          Report Generation
```