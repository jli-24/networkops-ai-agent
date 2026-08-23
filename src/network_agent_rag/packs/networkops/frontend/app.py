"""Single-page Streamlit console for the network operations Agent."""

from __future__ import annotations

from typing import Any
from uuid import uuid4
import os

import streamlit as st

from network_agent_rag.packs.networkops.frontend.client import FastAPIClient, FrontendAPIError


DEFAULT_API_URL = "http://127.0.0.1:8000"
NODE_LABELS = {
    "QueryAnalyzer": "正在分析问题",
    "Router": "正在选择数据源",
    "Retrieval": "正在检索上下文",
    "DocumentGrader": "正在评估文档质量",
    "QueryRewrite": "正在改写检索问题",
    "TopologyTool": "正在查询链路拓扑与两端接口",
    "MonitorTool": "正在读取接口计数器与光功率",
    "LogTool": "正在检索同一时间窗口的接口日志",
    "RAG": "正在检索历史故障案例",
    "EvidenceCorrelator": "正在关联多源证据并计算规则分数",
    "Generator": "正在生成回答",
    "HallucinationChecker": "正在检查回答依据",
    "Supervisor": "Supervisor 正在规划并分派任务",
    "TopologyAgent": "Topology Agent 正在查询网络路径与链路接口",
    "LogAgent": "Log Agent 正在检索故障时间窗口日志",
    "DiagnosisAgent": "Diagnosis Agent 正在关联监控与知识库证据",
    "RepairAgent": "Repair Agent 正在生成需人工批准的修复计划",
    "ReportAgent": "Report Agent 正在生成证据化诊断报告",
    "RiskCheck": "正在评估修复风险",
    "Approval": "正在等待或处理人工审批",
    "Execute": "正在执行已审批的白名单动作",
}


def main() -> None:
    st.set_page_config(
        page_title="企业网络智能运维 Agent",
        page_icon="🌐",
        layout="wide",
    )
    _initialize_state()
    _render_sidebar()

    st.title("企业网络智能运维 Agent")
    st.caption("Agentic RAG · 网络知识、拓扑与实时监控联合分析")
    chat_tab, incident_tab, observability_tab, benchmark_tab = st.tabs(
        ["对话", "事件", "可观测性", "评测"]
    )

    with chat_tab:
        chat_column, detail_column = st.columns([2, 1], gap="large")
        with chat_column:
            st.subheader("AI 对话")
            _render_messages()
            if prompt := st.chat_input("请输入网络运维问题", max_chars=10_000):
                _send_message(prompt)
        with detail_column:
            response = st.session_state.last_response
            st.subheader("引用文档")
            _render_sources(response)
            st.subheader("网络拓扑路径")
            _render_topology(response)
            st.subheader("实时设备状态")
            _render_metrics(response)

    with incident_tab:
        _render_incident_console()
    with observability_tab:
        _render_observability_console()
    with benchmark_tab:
        _render_benchmark_console()


def _initialize_state() -> None:
    defaults = {
        "active_session_id": uuid4().hex,
        "backend_url": os.getenv("NETWORK_AGENT_API_URL", DEFAULT_API_URL),
        "messages": [],
        "last_response": None,
        "observed_incidents": [],
        "selected_incident": None,
        "incident_status": None,
        "pending_approval": None,
        "selected_trace": None,
        "selected_timeline": None,
        "selected_audit": None,
        "metrics_summary": None,
        "benchmark_runs": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _render_sidebar() -> None:
    with st.sidebar:
        st.header("连接设置")
        with st.form("connection_form"):
            backend_url = st.text_input(
                "FastAPI 地址",
                value=st.session_state.backend_url,
            )
            session_id = st.text_input(
                "会话 ID",
                value=st.session_state.active_session_id,
            )
            submitted = st.form_submit_button(
                "连接并加载会话",
                width="stretch",
            )
        if submitted:
            _load_history(backend_url, session_id)
        st.caption(f"当前会话：`{st.session_state.active_session_id}`")


def _load_history(backend_url: str, session_id: str) -> None:
    try:
        client = FastAPIClient(backend_url)
        history = client.get_history(session_id)
        messages = history["messages"]
        if not all(
            isinstance(message, dict)
            and message.get("role") in {"user", "assistant"}
            and isinstance(message.get("content"), str)
            for message in messages
        ):
            raise FrontendAPIError(
                "INVALID_RESPONSE",
                "History contains invalid messages.",
            )
    except (FrontendAPIError, ValueError) as exc:
        st.error(_error_message(exc))
        return

    st.session_state.backend_url = client.base_url
    st.session_state.active_session_id = session_id.strip()
    st.session_state.messages = [
        {"role": message["role"], "content": message["content"]}
        for message in messages
    ]
    st.session_state.last_response = None
    st.success("会话历史已加载。")


def _render_incident_console() -> None:
    st.subheader("事件管理")
    st.caption("审批人 actor 当前未认证，仅可在可信本地环境中使用。")
    with st.form("incident_create_form"):
        query = st.text_area("事件问题", placeholder="例如：分析 SW1 到 SW2 的链路丢包")
        incident_id = st.text_input("事件 ID（可选）")
        submitted = st.form_submit_button("创建并运行事件")
    if submitted:
        _start_incident(query, incident_id)

    filter_columns = st.columns([2, 2, 1])
    status = filter_columns[0].selectbox(
        "状态筛选",
        ["全部", "running", "interrupted", "succeeded", "failed"],
    )
    risk = filter_columns[1].selectbox(
        "风险筛选", ["全部", "low", "medium", "high", "critical"]
    )
    if filter_columns[2].button("刷新事件", width="stretch"):
        _refresh_incidents(status, risk)

    incidents = st.session_state.observed_incidents
    if incidents:
        st.dataframe(incidents, width="stretch", hide_index=True)
        identifiers = [
            item["incident_id"]
            for item in incidents
            if isinstance(item, dict) and isinstance(item.get("incident_id"), str)
        ]
        if identifiers:
            selected = st.selectbox("选择事件", identifiers)
            if st.button("加载事件详情"):
                _load_incident(selected)
    else:
        st.info("暂无已加载事件。")

    if st.session_state.incident_status:
        st.markdown("#### 当前事件状态")
        st.json(st.session_state.incident_status)
    _render_approval()


def _start_incident(query: str, incident_id: str) -> None:
    if not query.strip():
        st.warning("事件问题不能为空。")
        return
    try:
        client = FastAPIClient(st.session_state.backend_url)
        for event in client.stream_incident(
            st.session_state.active_session_id,
            query.strip(),
            incident_id.strip() or None,
        ):
            if event.event == "approval_required":
                st.session_state.pending_approval = event.data
                st.session_state.selected_incident = event.data.get("incident_id")
            elif event.event == "answer":
                st.session_state.incident_status = event.data
                st.session_state.selected_incident = event.data.get("incident_id")
                st.session_state.pending_approval = None
            elif event.event == "error":
                raise FrontendAPIError(
                    str(event.data.get("code", "AGENT_ERROR")),
                    str(event.data.get("message", "Enterprise workflow failed.")),
                )
    except (FrontendAPIError, ValueError) as error:
        st.error(_error_message(error))


def _refresh_incidents(status: str = "全部", risk: str = "全部") -> None:
    try:
        payload = FastAPIClient(st.session_state.backend_url).list_incidents(
            status=None if status == "全部" else status,
            risk_level=None if risk == "全部" else risk,
        )
        items = payload.get("items")
        if not isinstance(items, list):
            raise FrontendAPIError("INVALID_RESPONSE", "Incident list is invalid.")
        st.session_state.observed_incidents = items
    except (FrontendAPIError, ValueError) as error:
        st.error(_error_message(error))


def _load_incident(incident_id: str) -> None:
    try:
        client = FastAPIClient(st.session_state.backend_url)
        st.session_state.selected_incident = incident_id
        st.session_state.incident_status = client.get_incident(incident_id)
        st.session_state.selected_trace = client.get_incident_trace(incident_id)
        st.session_state.selected_timeline = client.get_incident_timeline(incident_id)
        st.session_state.selected_audit = client.get_incident_audit(incident_id)
        risk = st.session_state.incident_status.get("risk_decision")
        if st.session_state.incident_status.get("enterprise_status") == "pending_approval" and isinstance(risk, dict):
            st.session_state.pending_approval = {
                "incident_id": incident_id,
                **risk,
                "actions": [],
            }
        else:
            st.session_state.pending_approval = None
    except (FrontendAPIError, ValueError) as error:
        st.error(_error_message(error))


def _render_approval() -> None:
    approval = st.session_state.pending_approval
    if not isinstance(approval, dict):
        return
    st.warning("该事件已暂停，等待人工审批。")
    if approval.get("actions"):
        st.json(approval["actions"])
    with st.form("approval_form"):
        actor = st.text_input("审批人标签")
        decision = st.selectbox("审批决定", ["approve", "reject"])
        comment = st.text_area("审批备注（可选）")
        submitted = st.form_submit_button("提交审批")
    if submitted:
        try:
            client = FastAPIClient(st.session_state.backend_url)
            for event in client.stream_approval(
                str(approval.get("incident_id", "")),
                decision=decision,
                actor=actor,
                plan_digest=str(approval.get("plan_digest", "")),
                comment=comment.strip() or None,
            ):
                if event.event == "answer":
                    st.session_state.incident_status = event.data
                    st.session_state.pending_approval = None
                elif event.event == "error":
                    raise FrontendAPIError(
                        str(event.data.get("code", "AGENT_ERROR")),
                        str(event.data.get("message", "Approval failed.")),
                    )
        except (FrontendAPIError, ValueError) as error:
            st.error(_error_message(error))


def _render_observability_console() -> None:
    st.subheader("Agent Execution Trace")
    if st.button("刷新可观测性数据"):
        try:
            st.session_state.metrics_summary = FastAPIClient(
                st.session_state.backend_url
            ).get_metrics_summary()
        except (FrontendAPIError, ValueError) as error:
            st.error(_error_message(error))
    metrics = st.session_state.metrics_summary
    if isinstance(metrics, dict):
        columns = st.columns(3)
        columns[0].metric("事件总数", metrics.get("incidents_total", 0))
        columns[1].metric("待审批", metrics.get("pending_approvals", 0))
        columns[2].metric("Span 总数", metrics.get("spans_total", 0))
        trend = metrics.get("incident_trend")
        if isinstance(trend, list) and trend:
            st.caption("事件趋势（UTC 小时）")
            st.line_chart(trend, x="bucket", y=["succeeded", "failed", "interrupted"])
        statuses = metrics.get("spans_by_status")
        if isinstance(statuses, dict) and statuses:
            st.caption("Span 状态分布")
            st.bar_chart(
                [{"status": key, "count": value} for key, value in statuses.items()],
                x="status",
                y="count",
            )
        distributions = {
            "风险等级": metrics.get("risk_levels"),
            "审批决定": metrics.get("approval_decisions"),
            "执行结果": metrics.get("execution_results"),
        }
        distribution_columns = st.columns(3)
        for column, (title, values) in zip(distribution_columns, distributions.items()):
            if isinstance(values, dict) and values:
                column.caption(title)
                column.bar_chart(
                    [{"value": key, "count": value} for key, value in values.items()],
                    x="value",
                    y="count",
                )
        rag_quality = metrics.get("rag_quality")
        if isinstance(rag_quality, dict):
            st.caption("RAG 质量汇总")
            quality_columns = st.columns(3)
            quality_columns[0].metric(
                "平均相关度", rag_quality.get("relevance_score_average", 0)
            )
            quality_columns[1].metric(
                "平均质量轮次", rag_quality.get("quality_iterations_average", 0)
            )
            quality_columns[2].metric(
                "平均证据数", rag_quality.get("evidence_count_average", 0)
            )
    else:
        st.info("尚未加载指标汇总。")

    trace = st.session_state.selected_trace
    spans = trace.get("spans", []) if isinstance(trace, dict) else []
    if spans:
        st.dataframe(spans, width="stretch", hide_index=True)
        durations = [
            {"span": item.get("name"), "duration_ms": item.get("duration_ms")}
            for item in spans
            if isinstance(item, dict) and isinstance(item.get("duration_ms"), (int, float))
        ]
        if durations:
            st.caption("节点耗时瀑布（按完成 Span）")
            st.bar_chart(durations, x="span", y="duration_ms")
    else:
        st.info("请选择并加载事件以查看执行 Trace。")

    st.subheader("Incident Timeline")
    timeline = st.session_state.selected_timeline
    events = timeline.get("events", []) if isinstance(timeline, dict) else []
    if events:
        st.dataframe(events, width="stretch", hide_index=True)
    else:
        st.info("暂无事件时间线。")
    audit = st.session_state.selected_audit
    if isinstance(audit, dict) and audit.get("events"):
        st.markdown("#### Audit Log")
        st.dataframe(audit["events"], width="stretch", hide_index=True)


def _render_benchmark_console() -> None:
    st.subheader("Benchmark Evaluation")
    st.caption("Benchmark 在 CLI 离线运行；Dashboard 只读取已保存结果。")
    if st.button("刷新评测结果"):
        try:
            payload = FastAPIClient(st.session_state.backend_url).list_benchmark_runs()
            items = payload.get("items")
            if not isinstance(items, list):
                raise FrontendAPIError("INVALID_RESPONSE", "Benchmark list is invalid.")
            st.session_state.benchmark_runs = items
        except (FrontendAPIError, ValueError) as error:
            st.error(_error_message(error))
    rows = []
    for run in st.session_state.benchmark_runs:
        if not isinstance(run, dict):
            continue
        summary = run.get("summary") if isinstance(run.get("summary"), dict) else {}
        rows.append(
            {
                "run_id": run.get("run_id"),
                "dataset": run.get("dataset_name"),
                "version": run.get("dataset_version"),
                "pass_rate": summary.get("pass_rate"),
                "p95_ms": summary.get("duration_ms_p95"),
                "groundedness": summary.get("groundedness"),
            }
        )
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.info("暂无评测结果。")


def _render_messages() -> None:
    if not st.session_state.messages:
        st.info("暂无对话。可以询问设备状态、链路故障或配置知识。")
        return
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])


def _send_message(prompt: str) -> None:
    query = prompt.strip()
    if not query:
        st.warning("问题不能为空。")
        return

    st.session_state.last_response = None
    st.session_state.messages.append({"role": "user", "content": query})
    with st.chat_message("user"):
        st.markdown(query)

    response: dict[str, object] | None = None
    with st.chat_message("assistant"):
        with st.status("Agent 正在处理...", expanded=True) as status:
            try:
                client = FastAPIClient(st.session_state.backend_url)
                for event in client.stream_chat(
                    st.session_state.active_session_id,
                    query,
                ):
                    if event.event == "node":
                        node = event.data.get("node")
                        label = NODE_LABELS.get(str(node), f"已完成节点：{node}")
                        status.write(label)
                    elif event.event == "answer":
                        response = event.data
                    elif event.event == "error":
                        raise FrontendAPIError(
                            str(event.data.get("code", "AGENT_ERROR")),
                            str(event.data.get("message", "Agent execution failed.")),
                        )
                if response is None or not isinstance(response.get("answer"), str):
                    raise FrontendAPIError(
                        "INVALID_RESPONSE",
                        "Agent answer is missing.",
                    )
            except (FrontendAPIError, ValueError) as exc:
                status.update(label="处理失败", state="error", expanded=True)
                st.error(_error_message(exc))
                return
            status.update(label="分析完成", state="complete", expanded=False)

        answer = str(response["answer"])
        st.markdown(answer)
        if response.get("error"):
            st.warning(str(response["error"]))

    st.session_state.messages.append({"role": "assistant", "content": answer})
    st.session_state.last_response = response


def _render_sources(response: dict[str, object] | None) -> None:
    documents = response.get("source_documents", []) if response else []
    if not isinstance(documents, list) or not documents:
        st.info("暂无引用文档。")
        return

    for index, document in enumerate(documents, start=1):
        if not isinstance(document, dict):
            st.json(document)
            continue
        metadata = document.get("metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        with st.expander(_source_label(index, metadata)):
            st.markdown(str(document.get("page_content", "")))
            st.caption("文档元数据")
            st.json(metadata)


def _source_label(index: int, metadata: dict[str, Any]) -> str:
    parts = [str(metadata.get("source") or f"文档 {index}")]
    title = metadata.get("title_path") or metadata.get("section_title")
    if title:
        parts.append(str(title))
    if metadata.get("page") is not None:
        parts.append(f"p.{metadata['page']}")
    return " · ".join(parts)


def _render_topology(response: dict[str, object] | None) -> None:
    context = response.get("topology_context", {}) if response else {}
    if not isinstance(context, dict) or not context:
        st.info("暂无拓扑上下文。")
        return
    path = context.get("path")
    if isinstance(path, list) and path and all(
        isinstance(device, str) and device.strip() for device in path
    ):
        st.markdown(f"**路径：** {' → '.join(path)}")
        return
    st.json(context)


def _render_metrics(response: dict[str, object] | None) -> None:
    metrics = response.get("metrics", {}) if response else {}
    if not isinstance(metrics, dict) or not metrics:
        st.info("暂无实时设备指标。")
        return

    definitions = (
        ("status", "设备状态", ""),
        ("cpu_percent", "CPU", "%"),
        ("memory_percent", "内存", "%"),
        ("traffic_in_mbps", "入流量", " Mbps"),
        ("traffic_out_mbps", "出流量", " Mbps"),
        ("packet_loss_percent", "丢包率", "%"),
    )
    visible = [(key, label, suffix) for key, label, suffix in definitions if key in metrics]
    if visible:
        columns = st.columns(min(3, len(visible)))
        for index, (key, label, suffix) in enumerate(visible):
            columns[index % len(columns)].metric(label, f"{metrics[key]}{suffix}")

    interfaces = metrics.get("interfaces")
    if isinstance(interfaces, list) and interfaces:
        st.caption("接口状态")
        st.dataframe(interfaces, width="stretch", hide_index=True)
    elif isinstance(interfaces, dict) and interfaces:
        st.caption("接口状态")
        st.json(interfaces)

    known_keys = {key for key, _, _ in definitions} | {"interfaces"}
    remaining = {key: value for key, value in metrics.items() if key not in known_keys}
    if remaining:
        st.caption("其他指标")
        st.json(remaining)


def _error_message(error: FrontendAPIError | ValueError) -> str:
    if isinstance(error, FrontendAPIError):
        return f"{error.message}（{error.code}）"
    return str(error)


if __name__ == "__main__":
    main()
