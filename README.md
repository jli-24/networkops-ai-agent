# 🚀 NetworkOps AI Agent

> 基于 LangGraph + Agentic RAG 的企业网络智能运维智能体平台


<p align="center">

企业网络故障诊断 · 根因分析 · RAG知识检索 · Agent工作流

</p>

<p align="center">

<img src="https://img.shields.io/badge/Python-3.11-blue?style=flat-square">
<img src="https://img.shields.io/badge/LangGraph-Agent-green?style=flat-square">
<img src="https://img.shields.io/badge/RAG-Chroma-orange?style=flat-square">
<img src="https://img.shields.io/badge/Version-0.5.1--alpha-blueviolet?style=flat-square">
<img src="https://img.shields.io/badge/Test-214%20passed-success?style=flat-square">

</p>


## ✨ Highlights

### 🤖 LangGraph Agent Workflow

基于 LangGraph 构建状态化网络运维 Agent 工作流：

- AgentState 状态管理
- 条件节点路由
- Tool Calling 工具调用
- 多步骤故障诊断流程
- 根因分析与报告生成

v0.2.0 新增 Supervisor Multi-Agent 工作流，按依赖顺序协调 Topology、Log、Diagnosis、Repair 和 Report Agent；Repair Agent 只生成需人工批准的计划，不执行网络变更。


### 🔍 Agentic RAG Knowledge Retrieval

结合网络运维知识库，实现：

- Markdown / TXT / 文本型 PDF 文档加载
- BGE-M3 Embedding
- Chroma 向量检索
- 故障案例匹配
- 证据引用与知识辅助诊断


### 🌐 Network Reasoning

基于 NetworkX 构建网络拓扑分析能力：

- 网络设备关系建模
- 链路路径查询
- 设备状态分析
- 多源故障证据关联


### 🧭 Network Digital Twin Preview

v0.2.1 新增独立的内存型 Network Digital Twin 基础层：

- 使用 Pydantic 表示设备、物理链路和带 UTC 时间戳的网络快照
- 使用无向 NetworkX 图构建七节点校园网络拓扑
- 查询设备、链路和邻居状态
- 通过原子更新接口演化设备与链路状态
- v0.2.2 基于边缘网关可达性进行只读故障传播和影响评分

当前 Preview 不连接真实设备，不支持 SNMP、NETCONF、故障注入或真实性能仿真，也不执行自动配置修改；传播分析不会改写 Twin，且尚未接入现有 Agent 工作流。


### 🏢 Enterprise Workflow

v0.3.0 新增独立、可选的企业工作流：

- Async SQLite Checkpointer 按 `incident_id` 保存 LangGraph 状态
- 高风险或明确要求审批的计划通过 `interrupt()` 暂停
- approve/reject API 使用相同事件 ID 恢复执行
- 审批绑定结构化动作摘要并默认 30 分钟过期
- Agent、Tool、决策和审批事件写入脱敏 SQLite 审计日志
- 只有显式注入白名单执行器时才能执行结构化动作

旧 Multi-Agent 图、`/chat`、`/history` 和 Streamlit Demo 保持不变。默认没有执行器，因此不会修改真实网络。


### 📈 Enterprise Observability Platform

v0.4.0 在 Enterprise Workflow 外围增加本地可观测性能力：

- Workflow、Agent、Tool、Decision、Approval 与 Execute 父子 Span
- interrupt/resume 共享 trace，并以独立 run 区分恢复前后执行段
- Agent、Tool、RAG、审批、恢复和执行生命周期写入脱敏 Audit TraceEvent
- SQLite Trace Store、Incident Timeline 与低基数 Prometheus 指标
- 可替换 `MetricsStore`，默认从现有 SQLite Span 与 Audit 查询时聚合企业指标
- 版本化 JSONL Benchmark、确定性规则评分和离线 CLI
- Streamlit 事件、审批、Trace、Timeline、Metrics 与 Benchmark 标签页

内置 Trace 采集路径对已知敏感键递归脱敏，业务载荷只保留白名单摘要及输入/输出摘要的 SHA-256，不主动持久化完整 Prompt、检索文档或异常堆栈。该机制不是通用 DLP，也不提供防篡改保证；自定义 Audit/Trace 属性仍应避免写入敏感自由文本。`SQLiteMetricsStore` 不创建指标表，而是统计 Workflow/Agent 时延、Tool 与 RAG 调用、审批等待和修复结果；当前为全历史累计快照。SQLite 仍是单进程演示后端，不代表生产级分布式追踪平台。


### 🗄️ Production Storage Alpha

v0.5.0-alpha 在不移除 SQLite 的前提下增加可插拔存储：

- `AuditStore` 与 `TraceStore` 结构协议，现有 SQLite 类无需包装即可兼容；
- PostgreSQL Audit 与 Trace 实现，分别使用独立表；
- LangGraph Checkpoint 可独立选择 SQLite、PostgreSQL 或 Redis；
- `create_storage_enterprise_app()` 按显式配置装配三类存储域；
- 本地 Docker Compose 只启动 PostgreSQL 17 与 Redis 8 数据服务。

Checkpoint、Trace 与 Audit 始终职责分离，不共用业务表。Redis 本阶段仅用于 Checkpointer，不提供 Session Cache、Agent Memory、Queue 或 Pub/Sub。该版本面向新部署，不迁移既有 SQLite 数据；默认后端仍是 SQLite，且 PostgreSQL/Redis 连接失败时不会静默回退。`Storage Health API` 计划在 v0.5.1 提供。


### 🔐 RBAC Foundation Preview

v0.5.1-alpha Phase 1 提供严格的 `User` 模型、`Role` 与 `Permission` 定义，以及支持多角色权限合并的纯内存权限判断。

当前 RBAC 仅作为权限模型基础，不代表 Enterprise API 已受到保护。项目尚未实现登录认证、JWT、OAuth2、用户数据库、API 权限中间件或审批人身份验证。


### 🧪 Engineering Quality

面向工程化开发：

- FastAPI 后端服务
- Streamlit 可视化界面
- unittest 自动化测试
- GitHub Actions CI
- Python 3.11 兼容


### 🔐 Safety First

默认安装采用安全阻断模式：

- 不包含真实设备配置执行器
- 不直接执行网络命令
- 高风险操作通过 LangGraph interrupt 暂停
- 审批仅对摘要匹配且未过期的结构化动作生效

## 🎬 Demo


### Network Fault Diagnosis


输入故障描述：

```text
SW1 到 SW2 链路丢包，请结合拓扑、日志和知识库分析
```

Agent 执行流程：

```text
✓ Analyze network topology

✓ Query device status

✓ Retrieve operation knowledge

✓ Correlate multi-source evidence

✓ Generate root cause hypothesis

✓ Produce diagnosis report
```

诊断输出：

```text
Root Cause:

SW1-SW2 optical module degradation


Evidence:

- CRC error counter increasing
- Low optical power detected
- Interface abnormal logs found
- Similar historical fault case retrieved


Recommendation:

Perform manual inspection during maintenance window.
```

---

## 🏗 System Architecture


NetworkOps AI Agent architecture:


```mermaid
flowchart TB
    User[Network Operator] --> UI[Streamlit Console]

    UI -->|HTTP + SSE| API[FastAPI Backend]

    API --> Supervisor[LangGraph Supervisor]

    Supervisor --> TopologyAgent[Topology Agent]
    Supervisor --> LogAgent[Log Agent]
    Supervisor --> Diagnosis[Diagnosis Agent]
    Supervisor --> Repair[Repair Agent]
    Supervisor --> Report[Report Agent]

    TopologyAgent --> NX[NetworkX Topology]
    LogAgent --> Logs[Redacted Logs]
    Diagnosis --> Monitor[Device Metrics]
    Diagnosis --> RAG[RAG Knowledge Retrieval]

    RAG --> Chroma[(Chroma Vector Database)]

    Diagnosis --> Repair
    Repair --> Report
```

## 🔄 Agent Workflow


NetworkOps AI Agent uses LangGraph to build a stateful diagnosis workflow.


### General Agentic RAG Workflow


```text
User Query

    ↓

Query Understanding

    ↓

Evidence Collection

    ↓

Network Topology Analysis

    ↓

Monitoring Data Analysis

    ↓

Log Analysis

    ↓

Knowledge Retrieval (RAG)

    ↓

Root Cause Analysis

    ↓

Diagnosis Report Generation
```
## 🧪 Test Result


NetworkOps AI Agent includes automated tests for:

- Agent workflow execution
- Network diagnosis logic
- RAG retrieval pipeline
- API interfaces
- Tool calling
- Audit-backed Agent execution TraceEvent


Current test status:

```text
Ran 215 tests (214 passed, 1 optional PostgreSQL integration test skipped)

OK
```
## 🚀 Roadmap


### v0.1.0 ✅ Initial Release

Completed:

- LangGraph Agent Workflow
- Agentic RAG Knowledge Retrieval
- Network Fault Diagnosis Pipeline
- NetworkX Topology Simulation
- FastAPI Backend
- Streamlit Demo
- Automated Testing (92 tests)


### v0.2.0 ✅ Multi-Agent Architecture

Completed:

- Supervisor Agent
- Diagnosis Agent
- Topology Agent
- Log Analysis Agent
- Repair Agent
- Report Agent
- Agent collaboration and dependency-aware task delegation
- Shared typed state and bounded handoffs
- Read-only repair planning with human-approval requirements


### v0.2.1 ✅ Network Digital Twin Foundation

Completed:

- Pydantic device, link and network-state models
- In-memory NetworkX topology modeling
- Deterministic seven-node campus topology
- Validated device and link state evolution interfaces


### v0.2.2 ✅ Fault Propagation Simulator

Completed:

- Validated device, service and link fault models
- Read-only gateway-reachability propagation analysis
- Deterministic affected device, link and service scope
- Normalized impact score and impact level


### v0.3.0 ✅ Enterprise Workflow

Completed:

- Async SQLite LangGraph Checkpointer
- Incident ID scoped persistence and interruption recovery
- High-risk Human-in-the-loop approval flow
- Digest-bound, expiring approve/reject decisions
- Allowlisted executor injection with safe default blocking
- Redacted SQLite audit logging


### v0.4.0 ✅ Enterprise Observability Platform

Completed:

- Agent / Tool / Workflow execution trace
- Metrics summary and Prometheus exposition
- Incident timeline and cursor-based incident listing
- Deterministic benchmark evaluation and result API
- Enhanced Streamlit incident, approval and observability console


### v0.5.0-alpha ✅ Production Storage

Completed:

- Pluggable AuditStore and TraceStore contracts
- PostgreSQL Audit and Trace stores with independent tables
- SQLite, PostgreSQL and Redis LangGraph Checkpointer adapters
- Explicit storage and checkpoint backend selection
- Local-development PostgreSQL and Redis Docker Compose


### v0.6.0 🚧 Network Digital Twin Evolution

Planning:

- Controlled fault injection and state recovery
- Propagation timelines and multi-fault scenarios
- Multi-Agent integration
- Repair impact analysis and post-change regression verification
- Optional SQLite-to-production-store migration tooling


### v1.0.0 🚧 Enterprise Platform

Planning:

- Multi-user support
- Role-based access control
- Persistent database
- Application container deployment
- Monitoring platform integration
- Production environment adaptation

**中文名称：** 企业网络智能运维 Agent 平台

**原始项目：** Network Fault Diagnosis and Automated Operations Agent

> NetworkOps AI Agent 是一个基于 LangGraph、RAG、NetworkX 与默认只读工具调用，用于企业/校园网络故障证据关联场景，实现联合诊断、审批恢复和可审计执行契约的智能运维平台原型。

## 目录

- [Current Implementation](#current-implementation)
- [1. 项目简介](#1-项目简介)
- [2. 项目背景](#2-项目背景)
- [3. 核心设计原则](#3-核心设计原则)
- [4. 系统架构](#4-系统架构)
- [5. 功能特性](#5-功能特性)
- [6. 支持故障类型](#6-支持故障类型)
- [7. Agent 工作流程](#7-agent-工作流程)
- [8. 技术栈](#8-技术栈)
- [9. 项目目录结构](#9-项目目录结构)
- [10. Engineering Skills](#10-engineering-skills)
- [11. Detailed Roadmap](#11-detailed-roadmap)
- [12. Quick Start](#12-quick-start)
- [13. License](#13-license)

## Current Implementation

当前版本已经实现：

- 两套依赖注入式 LangGraph 工作流：通用七节点质量闭环与专用八节点网络诊断图；
- v0.2.0 Supervisor Multi-Agent 图：按需协调 Topology、Log、Diagnosis、Repair 与 Report Agent；
- v0.2.1 Network Digital Twin Foundation：设备、无向物理链路、UTC 状态快照和内存状态演化接口；
- v0.2.2 Fault Propagation Simulator：只读网关可达性分析、受影响设备/链路/服务范围和归一化影响评分；
- v0.3.0 Enterprise Workflow：SQLite checkpoint、事件恢复、运行时审批、白名单执行器注入和脱敏审计；
- v0.4.0 Enterprise Observability：父子执行 Span、指标、事件时间线、离线 Benchmark 与增强 Streamlit Dashboard；
- 带文本层的 PDF、Markdown、TXT 文档加载，标题层级与 CLI 命令块保留；
- BGE-M3 Embedding、Chroma 集合重建与语义检索；
- 基于 NetworkX 的设备、关系、最短路径和双向接口查询；
- 固定且可复现的设备、接口、告警和脱敏日志快照；
- SW1–SW2 光模块退化演示，按多源证据计算规则诊断置信度；
- FastAPI 健康检查、SSE 节点进度、聊天接口和进程内历史；
- Streamlit 对话、Enterprise incident、审批、Trace、时间线、指标、评测结果与网络上下文展示；
- Python `unittest` 自动测试、85% branch coverage 门禁及确定性 Embeddings test double 离线测试策略。

当前版本默认仍是**不连接真实设备的诊断与修复规划原型**。Enterprise Workflow 只执行显式注入的白名单动作处理器；仓库不提供 SSH、Ansible、SNMP、NETCONF 或真实设备修改实现。真实 LLM、监控平台、知识源和执行器都必须由部署方注入。

### 当前限制与安全边界

- **PDF**：仅支持能够直接提取文本的文本型 PDF；不支持扫描 PDF，当前未集成 OCR。
- **Chroma**：当前采用集合重建模式。每次创建向量库都会清空并重建同名集合，不是完整的增量式持久化知识库；增量索引与集合生命周期管理属于后续规划。
- **API**：当前 FastAPI 接口仅用于本地演示，未实现用户认证、权限控制、限流或 CORS 策略。审批字段 `actor` 只是审计标签，不是经过认证的用户身份。禁止将当前服务直接暴露到公网或其他非可信网络。
- **人工确认**：Enterprise Workflow 已实现 LangGraph `interrupt`、SQLite checkpoint 和 approve/reject 审计，但尚无用户认证、角色权限或审批人身份校验；旧工作流仍只生成基于规则的人工确认提示。
- **SQLite**：Checkpointer、审计和 Trace Store 适用于单进程本地演示，不提供多 worker 协调或高可用；生产部署应迁移到适合并发工作负载的数据库或标准遥测后端。
- **Observability**：`/metrics` 使用低基数标签；内置 Trace 仅记录递归键脱敏后的摘要和 hash。该实现不是防篡改审计或通用 DLP，自定义自由文本仍需调用方控制。当前没有 OTLP exporter、集中式时序数据库、跨服务追踪或长期保留策略。
- **执行幂等性**：工作流不会自动重试 Execute，但 SQLite checkpoint 与外部变更不构成分布式事务。部署方注入的动作处理器必须以 `action_id` 实现幂等性并自行完成真实变更验证。
- **Digital Twin Preview**：当前支持内存拓扑、状态演化和只读故障传播分析；不连接真实设备，不支持 SNMP、NETCONF、故障注入、传播时间线、真实性能仿真或自动配置修改，也尚未接入 Agent 工作流。

---

## 1. 项目简介

NetworkOps AI Agent 面向企业和校园网络运维场景，将知识检索、网络拓扑、监控指标与日志证据放入同一个可追踪的 Agent 状态机中。它不是把大模型包装成聊天框，而是用条件图执行结构化查询、关联结果、生成诊断假设，并检查答案是否有证据支撑。通用图允许注入查询分析器来决定意图；当前 SW1–SW2 演示使用固定规则计划，始终按需检查四类证据，并非现成的 LLM 自主工具选择。

当前闭环覆盖：

```text
故障理解
  ↓
拓扑 / 指标 / 日志 / 知识采集
  ↓
证据关联与根因假设
  ↓
修复计划与结构化动作
  ↓
风险检查
  ↓
高风险 interrupt 审批 / 低风险继续
  ↓
白名单执行器（默认未配置并阻断）
  ↓
审计与文本报告
```

Enterprise Workflow 与旧工作流并存；v0.4 可观测性覆盖本地 Enterprise incident，但真实网络动作、身份认证和自动回退仍未实现，见 [Detailed Roadmap](#11-detailed-roadmap)。

## 2. 项目背景

传统网络故障处理通常依赖人工串联多个系统：

```text
用户反馈 → 查看设备 → 检查接口与日志 → 查阅手册/历史工单 → 定位原因 → 制定变更 → 验证恢复
```

常见问题包括：

- 排查链路长，拓扑、监控、日志和知识分散；
- 根因定位依赖资深工程师经验；
- 告警描述现象，却不能自动建立跨数据源因果关联；
- 配置命令风险高，不能让生成模型绕过审批直接执行；
- 诊断过程缺少统一状态、证据引用和可重复测试。

本项目以 Agentic RAG 为核心，把“选择数据源、收集证据、形成假设、检查答案”建模为显式 LangGraph 节点与条件边，从而让诊断过程更可观察、更可测试。

## 3. 核心设计原则

### 3.1 Agent First

Agent 不等于聊天机器人。当前工作流具备：

- `AgentState` / `DiagnosisState` 共享状态；
- 意图分析与条件路由；
- 由状态和条件边控制的拓扑、监控、日志和 RAG 数据源选择；
- 低质量检索时的 Query Rewrite；
- 答案不合格时的生成循环；
- 仅针对 `ConnectionError` 和 `TimeoutError` 的有限重试；
- 最大三轮质量预算，避免无限循环。

### 3.2 Tool Safety

当前监控与日志工具采用 LangChain `StructuredTool` 契约，输入字段固定并经过验证；拓扑与 RAG 通过可注入回调连接工作流。业务错误返回结构化结果，而不是把任意命令交给模型执行。仓库提供的是 Tool Calling 契约和编排框架；演示入口使用确定性回调，不包含 LLM 驱动的自主工具选择。

```text
LangGraph Agent
      ↓
Typed Callbacks / StructuredTool
      ↓
NetworkX / Fixed Monitoring Snapshot / Redacted Logs / Chroma
```

当前安全边界：

- 日志工具只接受数据源、时间范围、查询文本和数量限制；
- 不接受原始设备命令，不执行 SSH 或配置变更；
- 证据带有 `TOPO-*`、`METRIC-*`、`LOG-*`、`KB-*` 引用；
- 默认检查器拒绝未知证据编号，以及“已自动执行”“自动执行修复”“已重启接口”“已更换光模块”“已修改配置”等越权表述；
- 参数错误、未知设备和未知接口返回明确错误码。

当前 API 仍未实现用户认证、权限控制、限流和 CORS 策略，仅限本地演示。Enterprise Workflow 已提供 SQLite 审计和白名单执行器契约，但没有内置真实动作处理器；审批人字段不能替代身份认证。

### 3.3 Evidence-based Diagnosis

诊断输出区分事实与推断：

```text
Evidence（事实）
- SW1-Gi0/1 与 SW2-Gi0/24 属于同一物理链路
- CRC 和输入错误计数持续增长
- 接收光功率低于阈值
- 同一时间窗口存在接口错误日志
- 历史案例包含相同症状

Reasoning（推断）
- 多类独立证据共同支持光模块退化假设
- 规则诊断置信度为 80%，但不是统计校准后的真实概率
```

缺失任何证据时，分数会下降；证据不足时，结论会标记为“待验证假设”。

### 3.4 Human-in-the-loop

v0.3.0 Enterprise Workflow 使用 SQLite Checkpointer 和 LangGraph `interrupt()` 实现可暂停、可恢复审批。审批绑定事件 ID、结构化动作摘要和 30 分钟有效期；reject 后直接生成未执行报告。旧 v0.1/v0.2 工作流仍保持原有的规则提示行为。

| 风险级别 | 当前处理方式 | 示例 |
| --- | --- | --- |
| LOW | 未强制审批；仅在注入白名单处理器后执行 | 查询或演示环境状态更新 |
| MEDIUM | 未强制审批；计划可显式要求人工批准 | 有限范围的演示变更 |
| HIGH / CRITICAL | 必须暂停并等待摘要匹配的人工决策 | 任何可能影响网络服务的动作 |

当前没有审批身份认证、RBAC 或内置真实网络执行器。`actor` 仅供审计记录；执行节点不会重试状态变更，且遇到首个失败动作立即停止。

### 3.5 Reproducibility

当前仓库支持：

- Python 3.11 本地运行；
- 固定拓扑、指标和日志快照；
- 确定性 Embeddings test double 测试，不下载模型；
- 标准库 `unittest` 全量回归；
- `compileall` 与 `pip check` 验证。

仓库提供仅含 PostgreSQL/Redis 数据服务的本地开发 Compose，但尚无应用 Dockerfile；基础 GitHub Actions CI 已启用。

## 4. 系统架构

<!-- Architecture diagram: current implementation -->

```mermaid
flowchart TB
    User[User / Network Operator] --> UI[Streamlit Console]
    UI -->|HTTP + SSE| API[FastAPI]
    API --> Supervisor[Supervisor StateGraph]

    Supervisor --> Topology[Topology Agent]
    Supervisor --> Logs[Log Agent]
    Supervisor --> Diagnosis[Diagnosis Agent]
    Supervisor --> Repair[Repair Agent]
    Supervisor --> Report[Report Agent]

    Topology --> Supervisor
    Logs --> Supervisor
    Diagnosis --> Supervisor
    Repair --> Supervisor
    Report --> Supervisor

    Topology --> NX[(NetworkX Topology)]
    Logs --> Redacted[(Redacted Log Snapshot)]

    Diagnosis --> Snapshot[(Fixed Monitoring Snapshot)]
    Diagnosis --> Chroma[(Chroma + BGE-M3)]
    Report --> API

    API --> History[(In-memory History)]
```

当前架构说明：

- FastAPI 通过应用工厂接收已编译工作流；默认模块级应用不虚构 Agent，聊天接口会返回 HTTP 503；
- v0.1 专用演示入口保留原单 Agent 诊断图；v0.2 显式入口装配 Supervisor Multi-Agent、NetworkX、固定监控/日志和 Chroma；
- v0.3 Enterprise Workflow 使用独立 `/incidents` API、SQLite checkpoint 和审计库，不改变旧聊天接口；
- v0.4 在 Enterprise API 中增加 Trace、Timeline、Metrics、Benchmark 结果接口，并保持旧入口兼容；
- v0.5-alpha 通过独立应用工厂按需装配 SQLite/PostgreSQL Audit/Trace 与 SQLite/PostgreSQL/Redis Checkpoint；
- 通用工作流允许部署方注入真实生成器、检查器和数据源；
- PostgreSQL 仅承载 Checkpoint、Trace 与 Audit 基础设施数据，不是设备资产或业务数据库；当前没有故障注入器或真实设备客户端。Multi-Agent 采用单一共享状态图、顺序调度，不使用子图或并行 Agent。Chroma 当前采用清空同名集合后重新写入的集合重建模式，不支持增量索引。

## 5. 功能特性

| 功能 | 状态 | 描述 |
| --- | --- | --- |
| LangGraph Agent Workflow | ✅ Current | 条件边、循环、有限重试和结构化错误终止 |
| Supervisor Multi-Agent | ✅ Current | 依赖感知路由、共享 TypedDict 状态、有限交接和专业 Agent 降级处理 |
| 网络拓扑建模 | ✅ Current | 从 JSON 加载设备与关系，查询最短路径、邻居和双向接口 |
| Network Digital Twin | ✅ Current | 内存设备/链路状态、UTC 快照与状态演化基础接口 |
| 故障传播分析 | ✅ Current | 只读网关可达性分析及设备、链路、服务影响评分 |
| 监控 Tool | ✅ Current | 查询固定设备、接口和告警快照 |
| 日志 Tool | ✅ Current | 时间范围过滤、脱敏记录、证据引用和结构化错误 |
| RAG | ✅ Current | 文本型 PDF/Markdown/TXT、结构化切片、BGE-M3、Chroma 集合重建与检索；无 OCR |
| Root Cause Hypothesis | ✅ Current | 基于五类规则证据生成根因假设与置信度 |
| Hallucination Checker | ✅ Current | 校验根因、规则分数、证据编号和安全声明 |
| FastAPI SSE | ✅ Current | `start → node* → answer/error` 节点级流式事件 |
| Streamlit Console | ✅ Current | 对话、事件、审批、Trace、Timeline、指标、评测及网络上下文展示 |
| 进程内会话历史 | ✅ Current | 按会话隔离；重启清空，多 worker 不共享 |
| Enterprise Checkpoint | ✅ Current | 默认 Async SQLite；可选 PostgreSQL/Redis，按 incident ID 隔离与恢复 |
| Human-in-the-loop | ✅ Current | 高风险 interrupt、摘要绑定、30 分钟有效期、approve/reject 恢复 |
| Audit Logging | ✅ Current | Agent、Tool、决策和审批的脱敏记录；支持 SQLite/PostgreSQL |
| Agent Execution Trace | ✅ Current | incident/trace/run/span 关联、父子层级、耗时、尝试次数与错误码 |
| Metrics System | ✅ Current | 事件、调用、风险、审批、执行及 RAG 质量的低基数汇总和 Prometheus 文本输出 |
| Incident Timeline | ✅ Current | Trace 与 Audit UTC 合并、稳定排序与事件查询 |
| Benchmark Evaluation | ✅ Current | JSONL 数据集、确定性评分、离线 CLI 与结果 API |
| 白名单执行器契约 | ✅ Current | 仅执行结构化动作；默认无处理器并安全阻断 |
| 故障注入 | 🧭 Planned | 设备离线、端口关闭、拥塞和组合故障注入 |
| 真实网络自动修复 | 🧭 Planned | 认证、厂商适配、自动验证和经过测试的回退 |
| Incident Report 文件导出 | 🧭 Planned | 结构化事件报告与 PDF/Markdown 导出 |
| Docker Compose | 🧪 Alpha | 仅提供本地 PostgreSQL 与 Redis 数据服务，不包含应用容器 |

## 6. 支持故障类型

当前“支持”包括固定快照查询、演示诊断和只读传播分析，不代表能够向环境注入故障。

| 场景 | 当前支持度 | 示例 |
| --- | --- | --- |
| SW1–SW2 高丢包/光模块退化 | 完整演示 | 链路保持 up，但 CRC、输入错误增长且接收光功率越界 |
| 设备在线/降级/离线 | 状态查询样例 | `core-sw-01` 在线、`edge-rtr-01` 降级、`access-sw-01` 离线 |
| 接口 up/down | 状态查询样例 | 查询管理状态、运行状态、流量和丢包率 |
| 告警过滤 | 查询样例 | 按设备、严重级别和活动状态过滤固定告警 |
| device/service/link/performance Fault | 只读传播分析 | 输入规范化 Fault，计算失去边缘网关可达性的设备及影响范围 |
| 服务停止 | 未实现 | Roadmap：服务探测 Tool 与服务恢复审批 |
| 链路拥塞 | 未实现 | Roadmap：时序指标、容量基线和拥塞诊断 |
| 多故障组合 | 未实现 | Roadmap：故障注入与多假设排序 |

## 7. Agent 工作流程

### 7.1 通用 Agentic RAG 质量闭环

```text
QueryAnalyzer → Router
                    ├─ general → Generator
                    └─ network intent → Retrieval

Retrieval → DocumentGrader
                 ├─ relevant → Generator
                 └─ low score → QueryRewrite → Router

Generator → HallucinationChecker
                 ├─ approved → END
                 └─ rejected → Generator（共享最多三轮预算）
```

通用图通过回调注入分析、检索、生成和检查能力，不绑定模型供应商。

### 7.2 网络诊断专用图

```text
QueryAnalyzer
  → TopologyTool
  → MonitorTool
  → LogTool
  → RAG
  → EvidenceCorrelator
  → Generator
  → HallucinationChecker
       ├─ approved → END
       └─ rejected → Generator（最多三轮）
```

`DiagnosisPlan.required_sources` 控制条件边，未请求的证据节点会被跳过。读取节点仅对连接错误和超时进行最多三次重试。

## 8. 技术栈

| 分类 | 当前技术 |
| --- | --- |
| Runtime | Python 3.11 |
| Backend | FastAPI、Uvicorn、Pydantic、pydantic-settings |
| Agent | LangGraph、LangChain Core、StructuredTool |
| Network | NetworkX `MultiDiGraph` |
| RAG | LangChain Text Splitters、BGE-M3、Sentence Transformers、Chroma |
| Documents | PyMuPDF4LLM（仅文本型 PDF、无 OCR）、Markdown、TXT |
| Frontend | Streamlit、标准库 `urllib` SSE 客户端 |
| Testing | `unittest`、pytest（可选开发依赖）、确定性 Embeddings test double、`compileall`、`pip check` |
| Persistence | Chroma 集合重建；Checkpoint 支持 SQLite/PostgreSQL/Redis；Audit/Trace 支持 SQLite/PostgreSQL；聊天历史仍为进程内存 |
| Deployment | 本地 Python 进程；Compose 仅提供本地 PostgreSQL/Redis，不包含应用容器 |

项目不使用 SQLAlchemy 或 Alembic；PostgreSQL 适配直接使用 psycopg 3 参数化 SQL，pytest 作为可选开发依赖，仓库已有基础 GitHub Actions CI。

## 9. 项目目录结构

```text
.
├── knowledge/
│   └── diagnosis/
│       └── optical_module_degradation.md
├── topology/
│   └── sw1_sw2.json
├── docs/
│   └── superpowers/
│       ├── plans/
│       └── specs/
├── src/network_agent_rag/
│   ├── agents/
│   │   ├── enterprise/
│   │   │   ├── approval.py
│   │   │   ├── execution.py
│   │   │   ├── state.py
│   │   │   └── workflow.py
│   │   ├── multi_agent/
│   │   │   ├── state.py
│   │   │   ├── supervisor.py
│   │   │   └── workflow.py
│   │   ├── diagnosis_workflow.py
│   │   ├── log_tools.py
│   │   ├── monitoring_tools.py
│   │   └── workflow.py
│   ├── api/
│   │   ├── enterprise.py
│   │   ├── enterprise_schemas.py
│   │   ├── history.py
│   │   ├── router.py
│   │   └── schemas.py
│   ├── core/
│   │   └── config.py
│   ├── audit/
│   │   ├── models.py
│   │   └── store.py
│   ├── domain/
│   │   └── topology.py
│   ├── digital_twin/
│   │   ├── fault.py
│   │   ├── impact.py
│   │   ├── models.py
│   │   ├── network_model.py
│   │   ├── propagation.py
│   │   ├── simulator.py
│   │   └── state.py
│   ├── frontend/
│   │   ├── app.py
│   │   └── client.py
│   ├── infrastructure/
│   │   └── __init__.py
│   ├── rag/
│   │   ├── documents.py
│   │   └── vector_store.py
│   ├── demo.py
│   ├── demo_main.py
│   ├── multi_agent_demo.py
│   ├── multi_agent_main.py
│   └── main.py
├── tests/
├── .env.example
├── .python-version
├── README.md
└── requirements.txt
```

`infrastructure/` 当前仅声明未来外部系统集成边界，没有数据库或监控平台客户端实现。

## 10. Engineering Skills

### 10.1 Superpowers

| 方法 | 在项目中的用途 |
| --- | --- |
| brainstorming | 明确 Agent 定位、用户场景和安全边界 |
| writing-plans | 将 RAG、工作流、API、前端和诊断场景拆成可验证阶段 |
| test-driven-development | 先定义工作流、Tool、SSE 和诊断规则的行为测试 |
| systematic-debugging | 作为故障定位方法论；当前仓库未集成专用调试框架 |
| requesting-code-review | 在功能完成后检查实现与需求一致性 |

这些 skills 属于工程协作方法，不是运行时依赖。

### 10.2 Ponytail

Ponytail 用于控制工程复杂度：优先复用标准库和现有依赖，避免为演示场景引入数据库、消息队列或额外前端框架，并持续检查文档是否描述了不存在的能力。

### 10.3 AI Agent Engineering

- LangGraph StateGraph 与条件边；
- Agent State Management；
- Structured Tool Calling；
- Agentic RAG 与 Query Rewrite；
- Evidence Correlation；
- Hallucination Checking；
- 有限循环、重试与结构化错误终止。

### 10.4 Software Engineering

- `src/` 模块化工程结构；
- Pydantic API 契约；
- 应用工厂与依赖注入；
- 标准库 `unittest` 回归测试；
- 中文技术文档与可复现演示。

仓库已配置 GitHub Actions CI；容器发布、静态检查和生产部署流水线仍属于后续规划。

## 11. Detailed Roadmap

Phase 1 已在 v0.2.0 实现；Phase 2 的 Foundation 与只读传播分析分别在 v0.2.1、v0.2.2 实现；v0.3.0 已加入独立 Enterprise Workflow；v0.4.0 已加入本地 Enterprise Observability；v0.5.0-alpha 增加可插拔生产存储适配。完整 Digital Twin、生产身份体系和真实设备执行仍为 **Planned**。

### Phase 1 — Supervisor Multi-Agent（Completed in v0.2.0）

- Supervisor Agent；
- Diagnosis Agent；
- Topology Agent；
- Log Agent；
- Repair Agent；
- Report Agent；
- Agent 间依赖感知任务委派、共享状态和失败降级；
- Repair Agent 仅生成计划，不执行变更；
- 首版为顺序单图，不包含并行、子图或 Checkpointer。

### Phase 2 — Network Digital Twin（Foundation v0.2.1 / Propagation v0.2.2）

当前支持：

- 网络设备与无向物理链路建模；
- 带 UTC 时间戳的网络状态快照；
- 设备与链路状态演化基础接口；
- 固定七节点校园网络拓扑。
- 五类规范化 Fault 模型；
- 基于边缘网关可达性的只读传播范围分析；
- 设备、链路和服务影响评分。

v0.6.0 规划：

- 可控故障注入；
- 故障恢复、传播时间线和多故障组合；
- 与 Multi-Agent 诊断流程集成；
- 配置变更沙箱；
- 修复前影响评估与修复后回归验证。

### Phase 3 — Enterprise Workflow（Completed in v0.3.0）

- Async SQLite Checkpointer 与 incident ID 隔离；
- LangGraph interrupt 暂停及 `Command(resume=...)` 恢复；
- high/critical 和显式强制审批策略；
- 动作摘要、审批人标签和 30 分钟有效期；
- 白名单执行器注入、单动作顺序执行和失败即停；
- Agent、Tool、Decision、Approval 脱敏审计。

限制：SQLite 仅适合单进程演示；没有用户认证、RBAC、真实设备处理器、自动验证或自动回退。

### Phase 4 — Enterprise Observability（Completed in v0.4.0）

- Workflow、Agent、Tool、Decision、Approval 与 Execute 父子 Span；
- interrupt/resume 共用 trace，不同调用使用独立 run；
- SQLite Trace Store、Incident Timeline 与低基数 Prometheus 指标；
- JSONL Benchmark、确定性评分、离线 CLI 和结果查询 API；
- Streamlit Enterprise incident、审批、Trace、Timeline、Metrics 和 Benchmark 标签页。

限制：本地 SQLite Trace Store 不适合高并发；当前没有 OpenTelemetry/OTLP、集中式指标后端、认证或长期保留策略。

### Phase 5 — Production Storage（Alpha in v0.5.0）

- Audit/Trace 通过 Protocol 支持 SQLite 与 PostgreSQL；
- Checkpoint 独立支持 SQLite、PostgreSQL 与 Redis；
- 三类存储职责分离，不共用表；
- PostgreSQL/Redis Compose 仅服务本地开发。

限制：不包含 SQLite 数据迁移、自动故障转移、跨后端复制或 Storage Health API；后者计划在 v0.5.1，迁移工具计划在 v0.6/v1.0。

### Phase 6 — Network Digital Twin Evolution（Planned）

- 可控故障注入、恢复与多故障时间线；
- Digital Twin 与 Multi-Agent 诊断集成；
- 修复前影响评估与修复后回归验证；
- 配置变更沙箱。

### Phase 7 — Open Source Release（Planned）

- 应用 Dockerfile 与完整部署 Compose；
- 发布自动化、代码格式与静态检查；
- 安全策略、贡献指南与 Issue 模板；
- Chroma 增量索引与集合生命周期管理；
- 可选 OpenTelemetry、真实 Prometheus/Zabbix/ELK/LLM 适配器。

## 12. Quick Start

### 12.1 环境要求

- Python 3.11；
- 首次运行完整演示时可访问 Hugging Face；
- BGE-M3 模型约 4.6 GB，请预留磁盘空间；
- 文档处理仅支持带文本层的 PDF，不支持扫描 PDF，且不包含 OCR 能力；
- Windows PowerShell，或能够设置 `PYTHONPATH=src` 的 Linux/macOS shell。

### 12.2 安装依赖

Windows PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
$env:PYTHONPATH = "src"
```

Linux/macOS：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
export PYTHONPATH=src
```

需要运行包含 pytest 的完整开发验证时，可在已激活的虚拟环境中安装项目及开发依赖：

```powershell
python -m pip install -e ".[dev]"
```

### 12.3 启动基础 API

```powershell
uvicorn network_agent_rag.main:app --host 127.0.0.1 --port 8000
```

基础入口提供健康检查和 OpenAPI，但没有注入工作流；调用聊天接口会返回 HTTP 503。当前 API 没有用户认证、权限控制、限流或 CORS 策略，仅用于本地演示，禁止直接暴露到公网或其他非可信网络。

```text
GET http://127.0.0.1:8000/api/v1/health
OpenAPI http://127.0.0.1:8000/docs
```

### 12.4 启动 SW1–SW2 完整演示

```powershell
$env:PYTHONPATH = "src"
uvicorn network_agent_rag.demo_main:app --host 127.0.0.1 --port 8000
```

首次启动会下载 BGE-M3，并在 `data/chroma/sw1_sw2_demo/` 创建 Chroma 数据。当前实现会在每次初始化时清空并重建同名集合，不会执行增量索引；演示入口不建议开启 `--reload`，避免重复初始化模型和向量库。

### 12.5 启动 v0.2 Multi-Agent 演示

```powershell
$env:PYTHONPATH = "src"
uvicorn network_agent_rag.multi_agent_main:app --host 127.0.0.1 --port 8000
```

该入口保留与 v0.1 相同的 FastAPI/SSE 契约，但节点进度会显示 Supervisor、Topology、Log、Diagnosis、Repair 和 Report Agent。Repair Agent 只生成 `not_executed` 修复计划。

### 12.6 启动 Streamlit

保持 FastAPI 运行，在第二个终端执行：

```powershell
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "src"
$env:NETWORK_AGENT_API_URL = "http://127.0.0.1:8000"
streamlit run src/network_agent_rag/frontend/app.py
```

浏览器访问：`http://127.0.0.1:8501`

推荐演示问题：

```text
SW1 到 SW2 链路丢包，请结合拓扑、日志和知识库分析
```

### 12.7 调用 SSE API

```bash
curl -N -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"demo-1","query":"分析 SW1 到 SW2 的链路丢包"}'
```

查询进程内历史：

```bash
curl "http://127.0.0.1:8000/api/v1/history?session_id=demo-1"
```

### 12.8 Enterprise Incident API

企业接口只会由 `create_enterprise_app()`、`create_sqlite_enterprise_app()` 或 `create_storage_enterprise_app()` 显式挂载；默认 `network_agent_rag.main:app` 不创建工作流、数据库或执行器。

`create_sqlite_enterprise_app()` 的 factory 契约是显式且互斥的：旧 v0.3 集成继续使用 `workflow_factory(checkpointer, audit_log)`；需要注入可观测依赖的 v0.4 集成使用 `observed_workflow_factory(checkpointer, *, audit_log=None, trace_store=None, metrics_store=None, trace_collector=None)`。调用方必须二选一，框架不会按参数数量猜测调用方式。

`create_storage_enterprise_app()` 保持同一组 factory 契约，并独立读取 `STORAGE_BACKEND=sqlite|postgres` 与 `CHECKPOINT_BACKEND=sqlite|postgres|redis`。选择 PostgreSQL 时必须提供 `DATABASE_URL`；选择 Redis Checkpoint 时必须提供 `REDIS_URL`。非法配置或 setup 失败会中止启动，不会打印连接串，也不会自动降级到 SQLite。

创建事件：

```bash
curl -N -X POST http://127.0.0.1:8000/api/v1/incidents \
  -H "Content-Type: application/json" \
  -d '{"session_id":"demo-1","incident_id":"INC-1001","query":"分析 SW1 到 SW2 的链路丢包"}'
```

当 SSE 返回 `approval_required` 后，使用其中的 `plan_digest` 审批并继续流式执行：

```bash
curl -N -X POST http://127.0.0.1:8000/api/v1/incidents/INC-1001/approval \
  -H "Content-Type: application/json" \
  -d '{"decision":"approve","actor":"local-operator","plan_digest":"<64-char digest>"}'
```

查询状态和审计：

```bash
curl http://127.0.0.1:8000/api/v1/incidents/INC-1001
curl http://127.0.0.1:8000/api/v1/incidents/INC-1001/audit
```

`actor` 未经过身份认证，不能作为生产审批身份。真实执行器必须由部署方显式注入并在可信网络中使用。

### 12.9 Observability 与 Benchmark

查询事件、Trace、Timeline 和指标：

```bash
curl "http://127.0.0.1:8000/api/v1/observability/incidents?limit=20"
curl http://127.0.0.1:8000/api/v1/observability/incidents/INC-1001/trace
curl http://127.0.0.1:8000/api/v1/enterprise/incidents/INC-1001/trace
curl http://127.0.0.1:8000/api/v1/observability/incidents/INC-1001/timeline
curl http://127.0.0.1:8000/api/v1/enterprise/metrics
curl http://127.0.0.1:8000/api/v1/observability/metrics/summary
curl http://127.0.0.1:8000/metrics
```

`/enterprise/metrics` 返回基于现有 SQLite Span 与 Audit TraceEvent 查询时聚合的全历史 JSON 快照；`/observability/metrics/summary` 和 `/metrics` 保持原兼容契约。`/observability/.../trace` 返回供 Metrics 与 Timeline 使用的父子 Span；`/enterprise/.../trace` 返回 Audit 中按写入顺序保存的生命周期 TraceEvent。内置采集器只写入白名单摘要、递归键脱敏结果与 hash，不主动写入完整查询、Prompt、文档正文或异常堆栈；这不替代调用方的数据分类、DLP 或防篡改审计措施。

Benchmark 在进程外运行。部署方提供 `module:function` 形式的确定性观察回调，回调接收 `BenchmarkCase` 并返回 `BenchmarkObservation` 字段：

```powershell
python -m network_agent_rag.evaluation `
  --dataset benchmarks/network_operations_v1.jsonl `
  --dataset-name network-operations `
  --dataset-version 1 `
  --evaluator your_package.evaluator:observe_case `
  --results data/evaluations
```

结果可通过 `GET /api/v1/benchmarks/runs` 查询。Web API 不运行耗时 Benchmark，也不包含 LLM-as-a-Judge。

### 12.10 运行测试

```powershell
$env:PYTHONPATH = "src"
pytest
python -m unittest discover -s tests -v
python -m coverage run --branch --source=network_agent_rag -m unittest discover -s tests -v
python -m coverage report --fail-under=85
python -m compileall src tests
python -m pip check
```

测试使用确定性 Embeddings test double（包含轻量 FakeEmbeddings 实现），不下载 BGE-M3，也不会访问真实网络设备。

### 12.11 本地 PostgreSQL 与 Redis

根目录 `docker-compose.yml` 仅提供 PostgreSQL 17 与 Redis 8 数据服务，不包含后端或前端应用容器。复制 `.env.example` 为本地 `.env` 并修改开发密码后运行：

```bash
docker compose up -d postgres redis
docker compose ps
```

随后选择所需后端，例如：

```env
STORAGE_BACKEND=postgres
CHECKPOINT_BACKEND=redis
DATABASE_URL=postgresql://networkops:changed-password@127.0.0.1:5432/networkops
REDIS_URL=redis://:changed-password@127.0.0.1:6379/0
```

Compose 中的默认口令仅用于本机开发，不能用于共享或生产环境。Redis 8 用作 LangGraph Checkpointer 时需要 RedisJSON 与 RediSearch；本阶段不将 Redis 用作缓存、Memory 或队列。

## 13. License

NetworkOps AI Agent 的原创代码采用 [MIT License](LICENSE)，版权归 `2026 NetworkOps AI Agent Contributors` 所有。

第三方组件和 BGE-M3 模型继续适用各自许可证，MIT License 不会替代或重新许可这些第三方内容。完整清单与兼容性结论见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

特别说明：PDF 解析依赖 PyMuPDF4LLM，其许可为 GNU AGPL v3 / Artifex 商业双许可。使用 PDF 功能时必须遵守适用的 AGPL 义务或取得有效商业许可；不能将整个可安装依赖栈描述为纯 MIT。企业、闭源或网络服务场景应在发布前单独完成许可证评估。
