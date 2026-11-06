"""State and response contracts shared by workflow nodes and entry points."""

from dataclasses import dataclass
from typing import Any, Literal, TypedDict


class HistoryEntry(TypedDict):
    original_question: str
    standalone_question: str


class PendingClarification(TypedDict):
    source: Literal["question", "config_selection", "mql"]
    base_question: str
    clarification_question: str
    options: list[dict[str, str]]


@dataclass(frozen=True)
class AskOutcome:
    type: Literal["answer", "clarify", "error"]
    text: str
    request_id: str
    code: str | None = None
    is_exploratory: bool = False


class SQLValidationIssue(TypedDict):
    code: str
    message: str


class AskState(TypedDict, total=False):
    request_id: str
    incoming_question: str
    allow_exploration: bool
    is_exploratory: bool
    history: list[HistoryEntry]
    pending_clarification: PendingClarification | None
    clarification_requested: bool
    standalone_question: str | None
    intent: str | None
    candidates: list[dict[str, Any]] | None
    config: dict[str, Any] | None
    mql: dict[str, Any] | None
    sql: str | None
    validated_sql: str | None
    sql_validation_error: SQLValidationIssue | None
    rows: list[dict[str, Any]] | None
    truncated: bool | None
    failure: str | None
    reply_type: Literal["answer", "clarify", "error"] | None
    text: str | None
    output_text: str | None


FAILURE_MESSAGES = {
    "not_configured": "当前问数服务尚未配置完成。",
    "non_query": "当前试点只支持 SIMS project 业务数据的明细查询和指标分析。",
    "understand_failed": "问题理解失败，请稍后重试。",
    "embedding_timeout": "问题向量化服务超时，请稍后重试。",
    "embedding_failed": "问题向量化服务暂时不可用，请稍后重试。",
    "es_timeout": "ES 检索超时，请稍后重试。",
    "es_failed": "ES 检索服务暂时不可用，请稍后重试。",
    "no_config": "当前没有可回答该问题的已启用治理配置。",
    "exploration_unavailable": "没有匹配到治理配置，也没有探索到可用于回答的信息。",
    "choose_failed": "治理配置选择失败，请稍后重试。",
    "mql_incomplete": "当前问题或治理配置缺少生成查询所需的信息。",
    "mql_failed": "MQL 生成失败，请稍后重试。",
    "sql_failed": "SQL 生成失败，请稍后重试。",
    "sql_validation_failed": "生成的 SQL 未通过校验，请稍后重试。",
    "query_failed": "Doris 查询失败，请稍后重试。",
    "answer_failed": "查询已完成，但结果说明生成失败。",
}
