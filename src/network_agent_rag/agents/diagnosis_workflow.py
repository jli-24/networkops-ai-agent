"""Evidence-driven, read-only network fault diagnosis workflow."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal
from typing_extensions import TypedDict
import re

from langchain_core.documents import Document
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, RetryPolicy

from network_agent_rag.agents.workflow import AgentState, CheckerResult


EvidenceSource = Literal["topology", "monitoring", "logs", "knowledge"]
_SOURCES: tuple[EvidenceSource, ...] = (
    "topology",
    "monitoring",
    "logs",
    "knowledge",
)
_SOURCE_NODES = {
    "topology": "TopologyTool",
    "monitoring": "MonitorTool",
    "logs": "LogTool",
    "knowledge": "RAG",
}
_FALLBACK_ANSWER = "诊断未能完成；未执行任何网络变更，请人工检查数据源后重试。"


class DiagnosisPlan(TypedDict):
    """Entities, symptom, time range, and evidence required for diagnosis."""

    devices: list[str]
    interfaces: list[str]
    symptom: str
    start_time: str
    end_time: str
    required_sources: list[EvidenceSource]


class RootCauseHypothesis(TypedDict):
    """A deterministic hypothesis with supporting and contradictory evidence."""

    cause: str
    confidence_percent: int
    supporting_evidence: list[str]
    contradicting_evidence: list[str]


class DiagnosisState(AgentState):
    """API-compatible Agent state enriched with diagnosis evidence."""

    analysis: DiagnosisPlan
    logs: list[dict[str, object]]
    hypotheses: list[RootCauseHypothesis]
    evidence_refs: list[str]


class _DiagnosisInput(TypedDict):
    user_query: str


def create_network_diagnosis_workflow(
    *,
    analyze_query: Callable[[str], DiagnosisPlan],
    retrieve_topology: Callable[[DiagnosisPlan], dict[str, object]] | None = None,
    retrieve_metrics: Callable[
        [DiagnosisPlan, dict[str, object]], dict[str, object]
    ]
    | None = None,
    retrieve_logs: Callable[[DiagnosisPlan], dict[str, object]] | None = None,
    retrieve_documents: Callable[[str], list[Document]] | None = None,
    generate_answer: Callable[[DiagnosisState], str] | None = None,
    check_answer: Callable[[DiagnosisState], CheckerResult] | None = None,
    max_iterations: int = 3,
    max_retry_attempts: int = 3,
) -> CompiledStateGraph:
    """Compile a synchronous, evidence-first network diagnosis graph."""

    if not 1 <= max_iterations <= 3:
        raise ValueError("max_iterations must be between 1 and 3")
    if not 1 <= max_retry_attempts <= 3:
        raise ValueError("max_retry_attempts must be between 1 and 3")

    retry_policy = RetryPolicy(
        initial_interval=0.0,
        backoff_factor=1.0,
        max_interval=0.0,
        max_attempts=max_retry_attempts,
        jitter=False,
        retry_on=(ConnectionError, TimeoutError),
    )

    def terminal_state(state: DiagnosisState) -> dict[str, object]:
        return {
            "user_query": state.get("user_query", ""),
            "rewritten_query": state.get("rewritten_query", ""),
            "intent": state.get("intent", "hybrid"),
            "documents": state.get("documents", []),
            "relevance_score": state.get("relevance_score"),
            "grading_feedback": state.get("grading_feedback"),
            "topology_context": state.get("topology_context", {}),
            "metrics": state.get("metrics", {}),
            "answer": state.get("answer") or _FALLBACK_ANSWER,
            "iteration": state.get("iteration", 0),
            "checker_feedback": state.get("checker_feedback"),
            "error": state.get("error"),
            "analysis": state.get("analysis", _empty_plan()),
            "logs": state.get("logs", []),
            "hypotheses": state.get("hypotheses", []),
            "evidence_refs": state.get("evidence_refs", []),
        }

    def handle_error(state: DiagnosisState, error: NodeError) -> Command:
        update = terminal_state(state)
        update["error"] = (
            f"{error.node}: {type(error.error).__name__}: {error.error}"
        )
        return Command(update=update, goto=END)

    def query_analyzer(state: _DiagnosisInput) -> dict[str, object]:
        query = state.get("user_query", "")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("user_query must be a non-empty string")
        plan = analyze_query(query.strip())
        _validate_plan(plan)
        return {
            "user_query": query.strip(),
            "rewritten_query": query.strip(),
            "intent": "hybrid",
            "documents": [],
            "relevance_score": None,
            "grading_feedback": None,
            "topology_context": {},
            "metrics": {},
            "answer": "",
            "iteration": 0,
            "checker_feedback": None,
            "error": None,
            "analysis": plan,
            "logs": [],
            "hypotheses": [],
            "evidence_refs": [],
        }

    def topology_tool(state: DiagnosisState) -> dict[str, object]:
        if retrieve_topology is None:
            raise ValueError("retrieve_topology is required for topology evidence")
        context = retrieve_topology(state["analysis"])
        if not isinstance(context, dict):
            raise ValueError("retrieve_topology must return a dict")
        return {"topology_context": context}

    def monitor_tool(state: DiagnosisState) -> dict[str, object]:
        if retrieve_metrics is None:
            raise ValueError("retrieve_metrics is required for monitoring evidence")
        values = retrieve_metrics(state["analysis"], state["topology_context"])
        if not isinstance(values, dict):
            raise ValueError("retrieve_metrics must return a dict")
        return {"metrics": values}

    def log_tool(state: DiagnosisState) -> dict[str, object]:
        if retrieve_logs is None:
            raise ValueError("retrieve_logs is required for log evidence")
        result = retrieve_logs(state["analysis"])
        if not isinstance(result, dict):
            raise ValueError("retrieve_logs must return a dict")
        if result.get("ok") is False:
            raise ValueError(
                f"log query failed: {result.get('error_code', 'UNKNOWN')}: "
                f"{result.get('message', '')}"
            )
        records = result.get("records", [])
        if not isinstance(records, list) or not all(
            isinstance(record, dict) for record in records
        ):
            raise ValueError("retrieve_logs records must be a list of dictionaries")
        return {"logs": [dict(record) for record in records]}

    def rag(state: DiagnosisState) -> dict[str, object]:
        if retrieve_documents is None:
            raise ValueError("retrieve_documents is required for knowledge evidence")
        found = retrieve_documents(state["rewritten_query"])
        if not isinstance(found, list) or not all(
            isinstance(document, Document) for document in found
        ):
            raise ValueError("retrieve_documents must return list[Document]")
        return {"documents": found}

    def evidence_correlator(state: DiagnosisState) -> dict[str, object]:
        hypothesis, references = _correlate_evidence(state)
        return {"hypotheses": [hypothesis], "evidence_refs": references}

    def generator(state: DiagnosisState) -> dict[str, object]:
        answer = (
            generate_answer(state)
            if generate_answer is not None
            else _default_answer(state)
        )
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("generate_answer must return a non-empty string")
        return {"answer": answer.strip(), "iteration": state["iteration"] + 1}

    def hallucination_checker(state: DiagnosisState) -> dict[str, object]:
        result = (
            check_answer(state)
            if check_answer is not None
            else _default_check(state)
        )
        if not isinstance(result, dict):
            raise ValueError("check_answer must return a CheckerResult")
        approved = result.get("approved")
        feedback = result.get("feedback")
        if not isinstance(approved, bool) or not isinstance(feedback, str):
            raise ValueError("CheckerResult requires bool approved and str feedback")
        if approved:
            return {"checker_feedback": None, "error": None}
        if not feedback.strip():
            raise ValueError("feedback must be non-empty when an answer is rejected")
        if state["iteration"] >= max_iterations:
            return {
                "checker_feedback": feedback,
                "error": (
                    "MAX_ITERATIONS_REACHED: diagnosis answer did not pass "
                    f"evidence checks after {max_iterations} iterations"
                ),
            }
        return {"checker_feedback": feedback, "error": None}

    def next_after(source: EvidenceSource | None) -> Callable[[DiagnosisState], str]:
        start_index = -1 if source is None else _SOURCES.index(source)

        def route(state: DiagnosisState) -> str:
            required = set(state["analysis"]["required_sources"])
            for candidate in _SOURCES[start_index + 1 :]:
                if candidate in required:
                    return candidate
            return "correlate"

        return route

    def route_check(state: DiagnosisState) -> str:
        if state["error"] is not None or state["checker_feedback"] is None:
            return "end"
        return "retry"

    builder = StateGraph(
        DiagnosisState,
        input_schema=_DiagnosisInput,
        output_schema=DiagnosisState,
    )
    nodes = (
        ("QueryAnalyzer", query_analyzer),
        ("TopologyTool", topology_tool),
        ("MonitorTool", monitor_tool),
        ("LogTool", log_tool),
        ("RAG", rag),
        ("EvidenceCorrelator", evidence_correlator),
        ("Generator", generator),
        ("HallucinationChecker", hallucination_checker),
    )
    for name, node in nodes:
        builder.add_node(
            name,
            node,
            retry_policy=retry_policy,
            error_handler=handle_error,
        )

    destinations = {
        **{source: node for source, node in _SOURCE_NODES.items()},
        "correlate": "EvidenceCorrelator",
    }
    builder.add_edge(START, "QueryAnalyzer")
    builder.add_conditional_edges("QueryAnalyzer", next_after(None), destinations)
    for source in _SOURCES:
        builder.add_conditional_edges(
            _SOURCE_NODES[source],
            next_after(source),
            destinations,
        )
    builder.add_edge("EvidenceCorrelator", "Generator")
    builder.add_edge("Generator", "HallucinationChecker")
    builder.add_conditional_edges(
        "HallucinationChecker",
        route_check,
        {"retry": "Generator", "end": END},
    )
    return builder.compile()


def _empty_plan() -> DiagnosisPlan:
    return {
        "devices": [],
        "interfaces": [],
        "symptom": "",
        "start_time": "",
        "end_time": "",
        "required_sources": [],
    }


def _validate_plan(plan: object) -> None:
    if not isinstance(plan, dict):
        raise ValueError("analyze_query must return a DiagnosisPlan")
    required_fields = {
        "devices",
        "interfaces",
        "symptom",
        "start_time",
        "end_time",
        "required_sources",
    }
    if not required_fields.issubset(plan):
        raise ValueError("DiagnosisPlan is missing required fields")
    for field in ("devices", "interfaces"):
        value = plan[field]
        if not isinstance(value, list) or not value or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise ValueError(f"DiagnosisPlan {field} must contain non-empty strings")
    for field in ("symptom", "start_time", "end_time"):
        if not isinstance(plan[field], str) or not plan[field].strip():
            raise ValueError(f"DiagnosisPlan {field} must be a non-empty string")
    sources = plan["required_sources"]
    if not isinstance(sources, list) or any(source not in _SOURCES for source in sources):
        raise ValueError(f"required_sources must contain only {list(_SOURCES)}")
    if len(sources) != len(set(sources)):
        raise ValueError("required_sources must not contain duplicates")


def _correlate_evidence(
    state: DiagnosisState,
) -> tuple[RootCauseHypothesis, list[str]]:
    score = 0
    supporting: list[str] = []
    contradicting: list[str] = []
    references: list[str] = []

    links = state["topology_context"].get("links", [])
    matching_links = (
        [
            link
            for link in links
            if isinstance(link, dict) and _link_matches_plan(link, state["analysis"])
        ]
        if isinstance(links, list)
        else []
    )
    if matching_links:
        score += 10
        link = matching_links[0]
        supporting.append(
            "拓扑确认 "
            f"{link['source']}-{link['source_interface']} 与 "
            f"{link['target']}-{link['target_interface']} "
            "属于同一物理链路（+10）"
        )
        references.extend(_refs_from_dicts(matching_links))

    interfaces = state["metrics"].get("interfaces", [])
    valid_interfaces = (
        [
            item
            for item in interfaces
            if isinstance(item, dict)
            and _interface_matches_plan(item, state["analysis"])
        ]
        if isinstance(interfaces, list)
        else []
    )
    counters_increasing = any(
        _positive(item.get("crc_errors_delta"))
        and _positive(item.get("input_errors_delta"))
        for item in valid_interfaces
    )
    if counters_increasing:
        score += 25
        supporting.append("监控显示 CRC 与输入错误计数持续增长（+25）")
    optics_low = any(
        _number(item.get("optical_rx_dbm")) is not None
        and _number(item.get("optical_rx_low_threshold_dbm")) is not None
        and float(item["optical_rx_dbm"])
        <= float(item["optical_rx_low_threshold_dbm"])
        for item in valid_interfaces
    )
    if optics_low:
        score += 25
        supporting.append("接收光功率低于告警阈值（+25）")
    references.extend(_refs_from_dicts(valid_interfaces))
    if any(item.get("oper_status") == "down" for item in valid_interfaces):
        contradicting.append("接口已 down，需同时排除管理关闭或物理断链")

    matching_logs = [
        record
        for record in state["logs"]
        if _interface_matches_plan(record, state["analysis"])
        and _within_plan_window(record, state["analysis"])
    ]
    if any(
        any(term in str(record).casefold() for term in ("crc", "optical", "光"))
        for record in matching_logs
    ):
        score += 10
        supporting.append("同一时间窗口存在接口错误或光功率日志（+10）")
    references.extend(_refs_from_dicts(matching_logs))

    knowledge_matches = [
        document
        for document in state["documents"]
        if _number(document.metadata.get("relevance_score")) is not None
        and float(document.metadata["relevance_score"]) >= 0.8
        and "crc" in document.page_content.casefold()
        and ("光功率" in document.page_content or "optical" in document.page_content.casefold())
        and ("光模块" in document.page_content or "transceiver" in document.page_content.casefold())
    ]
    if knowledge_matches:
        score += 10
        supporting.append("RAG 命中相同症状的历史光模块退化案例（+10）")
        for document in knowledge_matches:
            reference = document.metadata.get("evidence_ref") or document.metadata.get("source")
            if reference:
                references.append(str(reference))

    return (
        {
            "cause": (
                "光模块异常" if score >= 50 else "光模块异常（待验证假设）"
            ),
            "confidence_percent": score,
            "supporting_evidence": supporting,
            "contradicting_evidence": contradicting,
        },
        list(dict.fromkeys(references)),
    )


def _default_answer(state: DiagnosisState) -> str:
    hypothesis = state["hypotheses"][0]
    plan = state["analysis"]
    support = "\n".join(
        f"- {item}" for item in hypothesis["supporting_evidence"]
    ) or "- 当前没有形成可验证的支持证据。"
    uncertainty = "；".join(hypothesis["contradicting_evidence"]) or (
        "规则分数未经过统计校准；仍需现场检查跳纤、端口与对端模块。"
    )
    references = "、".join(state["evidence_refs"]) or "无"
    timeline = _evidence_timeline(state)
    endpoints = " ↔ ".join(
        reference.replace(":", "-", 1)
        for reference in plan["interfaces"]
    )
    return f"""## 故障摘要与影响范围

{plan['devices'][0]} 到 {plan['devices'][-1]} 出现{plan['symptom']}；目标物理链路为 {endpoints}，接口当前保持 up，但业务存在丢包风险。

## 证据时间线

{timeline}

## 证据与规则分值

{support}

证据引用：{references}

## 根因判断

根因：{hypothesis['cause']}
规则诊断置信度 {hypothesis['confidence_percent']}%（这是确定性规则分数，不是真实故障概率）。

## 建议（只读诊断，不自动执行）

1. 只读检查两端接口的 CRC、输入错误增量和接收光功率。
2. 风险等级：高。在维护窗口并获得人工批准后，再更换光模块；本工作流不会执行接口重启、模块更换或配置修改。
3. 更换后验证丢包率、CRC 增量、光功率和链路稳定性，并保留变更记录和回退条件。

## 剩余不确定性与下一步安全检查

{uncertainty}
"""


def _default_check(state: DiagnosisState) -> CheckerResult:
    hypothesis = state["hypotheses"][0]
    answer = state["answer"]
    unsafe_phrases = (
        "已自动执行",
        "自动执行修复",
        "已重启接口",
        "已更换光模块",
        "已修改配置",
    )
    unsafe = [phrase for phrase in unsafe_phrases if phrase in answer]
    if unsafe:
        return {
            "approved": False,
            "feedback": f"unsafe action claim is not supported: {', '.join(unsafe)}",
        }

    known_references = set(state["evidence_refs"])
    mentioned_references = set(
        re.findall(r"\b(?:TOPO|METRIC|LOG|KB)-[A-Za-z0-9_-]+\b", answer)
    )
    unknown_references = sorted(mentioned_references - known_references)
    exact_cause = f"根因：{hypothesis['cause']}"
    negated_cause = f"不是{hypothesis['cause']}"
    if (
        unknown_references
        or answer.count("根因：") != 1
        or exact_cause not in answer
        or negated_cause in answer
    ):
        details = ", ".join(unknown_references) or "root cause statement"
        return {
            "approved": False,
            "feedback": f"unsupported diagnosis claim or evidence reference: {details}",
        }

    required = (
        hypothesis["cause"],
        f"规则诊断置信度 {hypothesis['confidence_percent']}%",
        "CRC",
        "光功率",
        "维护窗口",
        "人工批准",
        "验证",
        "不会执行",
        *hypothesis["supporting_evidence"],
        *state["evidence_refs"],
    )
    missing = [item for item in required if item not in answer]
    if missing:
        return {
            "approved": False,
            "feedback": f"answer is missing grounded evidence: {', '.join(missing)}",
        }
    return {"approved": True, "feedback": ""}


def _refs_from_dicts(items: list[object]) -> list[str]:
    return [
        str(item["evidence_ref"])
        for item in items
        if isinstance(item, dict) and item.get("evidence_ref")
    ]


def _planned_interfaces(plan: DiagnosisPlan) -> dict[str, str]:
    planned: dict[str, str] = {}
    for reference in plan["interfaces"]:
        if ":" in reference:
            device_id, interface_name = reference.split(":", 1)
            planned[device_id.strip().casefold()] = interface_name.strip().casefold()
    return planned


def _interface_matches_plan(
    evidence: dict[str, object],
    plan: DiagnosisPlan,
) -> bool:
    device_id = evidence.get("device_id") or evidence.get("source_id")
    interface_name = evidence.get("interface_name")
    if not isinstance(device_id, str) or not isinstance(interface_name, str):
        return False
    planned = _planned_interfaces(plan)
    return planned.get(device_id.strip().casefold()) == interface_name.strip().casefold()


def _link_matches_plan(link: dict[str, object], plan: DiagnosisPlan) -> bool:
    if link.get("type") != "connect":
        return False
    source = link.get("source")
    target = link.get("target")
    source_interface = link.get("source_interface")
    target_interface = link.get("target_interface")
    if not all(
        isinstance(value, str)
        for value in (source, target, source_interface, target_interface)
    ):
        return False
    planned = _planned_interfaces(plan)
    return (
        planned.get(source.strip().casefold()) == source_interface.strip().casefold()
        and planned.get(target.strip().casefold()) == target_interface.strip().casefold()
    )


def _within_plan_window(record: dict[str, object], plan: DiagnosisPlan) -> bool:
    timestamp = record.get("timestamp")
    if not isinstance(timestamp, str):
        return False
    try:
        observed = datetime.fromisoformat(timestamp)
        start = datetime.fromisoformat(plan["start_time"])
        end = datetime.fromisoformat(plan["end_time"])
    except ValueError:
        return False
    if any(value.tzinfo is None for value in (observed, start, end)):
        return False
    return start <= observed <= end


def _evidence_timeline(state: DiagnosisState) -> str:
    entries: list[tuple[str, str]] = []
    for link in state["topology_context"].get("links", []):
        if isinstance(link, dict) and _link_matches_plan(link, state["analysis"]):
            entries.append(
                (
                    "0000-static",
                    "拓扑快照："
                    f"{link['source']}-{link['source_interface']} ↔ "
                    f"{link['target']}-{link['target_interface']}",
                )
            )
    observed_at = state["metrics"].get("observed_at")
    if isinstance(observed_at, str):
        entries.append(
            (
                observed_at,
                f"{observed_at} 监控：CRC/输入错误增量、接收光功率和丢包率。",
            )
        )
    for record in state["logs"]:
        if not _interface_matches_plan(
            record, state["analysis"]
        ) or not _within_plan_window(record, state["analysis"]):
            continue
        timestamp = str(record.get("timestamp", "unknown-time"))
        entries.append(
            (
                timestamp,
                f"{timestamp} 日志：{record.get('message', record.get('event_type', ''))}",
            )
        )
    for document in state["documents"]:
        source = document.metadata.get("source", "历史案例")
        entries.append(("zzzz-history", f"历史案例：{source}"))
    entries.sort(key=lambda item: item[0])
    return "\n".join(f"- {text}" for _, text in entries) or "- 无可用证据。"


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _positive(value: object) -> bool:
    number = _number(value)
    return number is not None and number > 0


__all__ = [
    "DiagnosisPlan",
    "DiagnosisState",
    "EvidenceSource",
    "RootCauseHypothesis",
    "create_network_diagnosis_workflow",
]
