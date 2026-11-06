import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from .mql import MQLPlan, SCHEMA_EXAMPLE
from .settings import Settings


class ModelError(RuntimeError):
    pass


class QuestionUnderstanding(BaseModel):
    standalone_question: str
    intent: Literal["detail", "metric", "non_query"]
    needs_clarification: bool
    clarification_question: str | None = None


class ConfigChoice(BaseModel):
    action: Literal["select", "none", "clarify"]
    config_id: str | None = None
    clarification_question: str | None = None
    option_ids: list[str] = Field(default_factory=list)


def _json_from_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ModelError("模型返回的不是 JSON 对象")
    return value


class QwenAI:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self.settings = settings
        self.http = http or httpx.AsyncClient(timeout=60)

    async def _chat(self, system: str, user: str, *, json_mode: bool = True) -> str:
        url = self.settings.qwen_base_url.rstrip("/") + "/chat/completions"
        body: dict[str, Any] = {
            "model": self.settings.qwen_model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        response = await self.http.post(
            url,
            headers={"Authorization": f"Bearer {self.settings.qwen_api_key}"},
            json=body,
        )
        response.raise_for_status()
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError("千问响应结构不符合预期") from exc

    async def understand(
        self,
        question: str,
        history: list[dict[str, str]],
        pending: dict[str, Any] | None = None,
    ) -> QuestionUnderstanding:
        system = (
            "你负责理解 SIMS project 问数会话。本轮输入优先；近期成功问题只用于补全省略。"
            "若有 pending，判断本轮是在回答其澄清问题，还是重新提出完整问题或换话题。"
            "若本轮是在回答 pending，结合 pending.base_question、澄清问题、选项和本轮输入"
            "形成新的独立问题；若是完整重问或新话题，不要继承旧澄清。"
            "输出 JSON："
            '{"standalone_question":"...","intent":"detail|metric|non_query",'
            '"needs_clarification":false,"clarification_question":null}。'
            "问数问题本身有明显指代或任务歧义且无法形成独立问题时，needs_clarification=true，"
            "clarification_question 只问最需要补充的一件事；standalone_question 保留目前已知的问题。"
            "不要因为没有时间范围就默认澄清，业务必填条件由治理配置决定。"
            "不要沿用历史查询结果，也不要推测未提供的业务口径。"
            "逐条记录、列表、有哪些、某条详情是 detail；数量、总和、平均、比例、趋势、分组、排行是 metric；"
            "与查询 SIMS 数据无关的是 non_query，非问数不澄清。混合问题先选择主要诉求。不要生成 SQL。"
        )
        payload = {"question": question, "history": history, "pending": pending}
        data = _json_from_text(await self._chat(system, json.dumps(payload, ensure_ascii=False)))
        result = QuestionUnderstanding.model_validate(data)
        if result.needs_clarification and not (result.clarification_question or "").strip():
            raise ModelError("模型没有给出澄清问题")
        if result.intent != "non_query" and not result.needs_clarification and not result.standalone_question.strip():
            raise ModelError("模型没有生成完整问题")
        return result

    async def embed(self, question: str) -> list[float]:
        url = self.settings.embedding_base_url.rstrip("/") + "/embeddings"
        response = await self.http.post(
            url,
            headers={"Authorization": f"Bearer {self.settings.embedding_api_key}"},
            json={"model": self.settings.embedding_model, "input": question},
        )
        response.raise_for_status()
        try:
            vector = response.json()["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError("Embedding 响应结构不符合预期") from exc
        if not isinstance(vector, list) or not vector:
            raise ModelError("Embedding 返回了空向量")
        return vector

    async def choose_config(self, question: str, candidates: list[dict[str, Any]]) -> ConfigChoice:
        brief = [
            {
                "config_id": c.get("config_id"),
                "name": c.get("name"),
                "description": c.get("description"),
                "aliases": c.get("aliases"),
                "example_questions": c.get("example_questions"),
            }
            for c in candidates
        ]
        system = (
            "从候选治理配置中选择最能回答问题的一条。只返回 JSON："
            '{"action":"select|none|clarify","config_id":null,'
            '"clarification_question":null,"option_ids":[]}。'
            "能明确选中时 action=select 并给出 config_id；无适用项时 action=none。"
            "只有两条或以上配置都合理、业务含义不同且问题无法区分时 action=clarify，"
            "给出面向用户的具体澄清问题，并在 option_ids 中列出 2 到 3 个相关配置 ID。"
            "不能仅因候选数量多就澄清。只能使用真实候选 ID，不能推测配置外的业务含义。"
        )
        data = _json_from_text(await self._chat(system, json.dumps({"question": question, "candidates": brief}, ensure_ascii=False)))
        choice = ConfigChoice.model_validate(data)
        ids = {c.get("config_id") for c in candidates}
        if choice.action == "select" and choice.config_id not in ids:
            raise ModelError("模型选择了候选之外的配置")
        if choice.action == "clarify":
            if not (choice.clarification_question or "").strip():
                raise ModelError("模型没有给出配置澄清问题")
            if not 2 <= len(set(choice.option_ids)) <= 3 or not set(choice.option_ids) <= ids:
                raise ModelError("模型给出的澄清选项不在候选配置中")
        return choice

    async def make_mql(self, question: str, config: dict[str, Any]) -> dict[str, Any]:
        system = (
            "你要根据用户问题和治理配置生成完整 MQL 查询计划，只返回 JSON 对象。"
            "MQL 必须严格遵循给出的 example_mql 结构；source 是物理表名与别名，select 是表达式和输出别名数组，"
            "joins 是关联数组，where 是结构化条件数组，group_by/having/order_by 是数组，limit 是整数。"
            "指标的 calculation 必须包含已选计算组件和公式；明细的 calculation 为 null。"
            "所有物理表、字段、关联、固定过滤和计算组件只能来自配置。"
            "用户问题决定本次时间、筛选、分组和排序。时间范围用左闭右开。"
            "MQL 要包含生成 SQL 所需的全部信息。若缺少用户能够补充的必要条件，"
            '返回 {"error":{"kind":"missing_user_input","clarification_question":"具体问题"}}。'
            '若配置口径缺失或有其他问题，返回 {"error":{"kind":"config_incomplete"}}。'
            "不要猜测缺失的条件。"
        )
        payload = {"question": question, "config": config, "example_mql": SCHEMA_EXAMPLE}
        data = _json_from_text(await self._chat(system, json.dumps(payload, ensure_ascii=False)))
        if "error" in data:
            return data
        data["config_id"] = config["config_id"]
        data["query_type"] = config["config_type"]
        data["limit"] = min(data.get("limit", self.settings.max_result_rows), self.settings.max_result_rows)
        plan = MQLPlan.model_validate(data)
        return plan.model_dump()

    async def make_sql(self, mql: dict[str, Any]) -> str:
        system = (
            "你将完整 MQL 翻译为 Apache Doris 可执行的单条只读 SELECT SQL。"
            '只返回 JSON：{"sql":"SELECT ..."}。'
            "只能使用 MQL 中的表、字段、关联、固定条件和计算规则。"
            "不得使用 INSERT、UPDATE、DELETE、DDL 或多语句。"
        )
        data = _json_from_text(await self._chat(system, json.dumps(mql, ensure_ascii=False)))
        sql = data.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            raise ModelError("模型没有生成 SQL")
        return sql.strip()

    async def summarize(self, question: str, rows: list[dict[str, Any]], *, total_truncated: bool) -> str:
        system = (
            "你是 SIMS 问数结果说明助手。只根据提供的真实查询结果，用中文纯文本回答原问题。"
            "不要编造未返回的行或业务含义。若结果为空就直说；若结果被截断要说明只展示部分记录。"
            "不要使用 Markdown 表格。"
        )
        payload = {"question": question, "rows": rows, "truncated": total_truncated}
        return (await self._chat(system, json.dumps(payload, ensure_ascii=False, default=str), json_mode=False)).strip()
