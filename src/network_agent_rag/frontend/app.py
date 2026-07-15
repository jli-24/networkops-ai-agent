"""Single-page Streamlit console for the network operations Agent."""

from __future__ import annotations

from typing import Any
from uuid import uuid4
import os

import streamlit as st

from network_agent_rag.frontend.client import FastAPIClient, FrontendAPIError


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


def _initialize_state() -> None:
    defaults = {
        "active_session_id": uuid4().hex,
        "backend_url": os.getenv("NETWORK_AGENT_API_URL", DEFAULT_API_URL),
        "messages": [],
        "last_response": None,
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
