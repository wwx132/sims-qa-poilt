"""Business steps of the question answering graph."""

from typing import Any, Literal

import httpx

from .contracts import AskState, FAILURE_MESSAGES, HistoryEntry, PendingClarification
from .sql_validation import SQLValidationError, validate_sql


class WorkflowNodes:
    HISTORY_LIMIT = 5

    ai: Any
    retriever: Any
    doris: Any
    settings: Any

    @staticmethod
    def _prepare(state: AskState) -> AskState:
        """Keep conversation context and clear fields from the previous turn."""
        return {
            "history": list(state.get("history") or []),
            "pending_clarification": state.get("pending_clarification"),
            "clarification_requested": False,
            "standalone_question": None,
            "intent": None,
            "candidates": None,
            "config": None,
            "is_exploratory": False,
            "mql": None,
            "sql": None,
            "validated_sql": None,
            "sql_validation_error": None,
            "rows": None,
            "truncated": None,
            "failure": None,
            "reply_type": None,
            "text": None,
            "output_text": None,
        }

    async def _understand(self, state: AskState) -> AskState:
        if self.settings is not None and self.settings.missing_services():
            return {"failure": "not_configured"}
        try:
            understood = await self.ai.understand(
                state["incoming_question"],
                state.get("history") or [],
                state.get("pending_clarification"),
            )
        except Exception:
            return {"failure": "understand_failed"}
        if understood.intent == "non_query":
            return {"intent": understood.intent, "pending_clarification": None, "failure": "non_query"}
        question = understood.standalone_question.strip()
        if understood.needs_clarification:
            return {
                "intent": understood.intent,
                "pending_clarification": self._pending(
                    "question", question or state["incoming_question"],
                    understood.clarification_question,
                ),
                "clarification_requested": True,
            }
        return {
            "standalone_question": question,
            "intent": understood.intent,
            "pending_clarification": None,
        }

    @staticmethod
    def _pending(
        source: Literal["question", "config_selection", "mql"],
        base_question: str,
        clarification_question: str,
        options: list[dict[str, str]] | None = None,
    ) -> PendingClarification:
        return {
            "source": source,
            "base_question": base_question,
            "clarification_question": clarification_question.strip(),
            "options": options or [],
        }

    async def _retrieve(self, state: AskState) -> AskState:
        question = state["standalone_question"]
        try:
            vector = await self.ai.embed(question)
        except (httpx.TimeoutException, TimeoutError):
            return {"failure": "embedding_timeout"}
        except Exception:
            return {"failure": "embedding_failed"}
        try:
            candidates = await self.retriever.retrieve(question, vector, state["intent"])
        except (httpx.TimeoutException, TimeoutError):
            return {"failure": "es_timeout"}
        except Exception:
            return {"failure": "es_failed"}
        if not candidates:
            return {"candidates": [], "failure": "no_config"}
        return {"candidates": candidates}

    async def _select_config(self, state: AskState) -> AskState:
        candidates = state["candidates"]
        try:
            choice = await self.ai.choose_config(state["standalone_question"], candidates)
        except Exception:
            return {"failure": "choose_failed"}
        if choice.action == "none":
            return {"failure": "no_config"}
        if choice.action == "clarify":
            options = [
                {
                    "config_id": c["config_id"],
                    "name": c.get("name") or c["config_id"],
                    "description": c.get("description") or "",
                }
                for c in candidates if c.get("config_id") in choice.option_ids
            ]
            return {
                "pending_clarification": self._pending(
                    "config_selection", state["standalone_question"],
                    choice.clarification_question, options,
                ),
                "clarification_requested": True,
            }
        config = next((c for c in candidates if c.get("config_id") == choice.config_id), None)
        if config is None or not isinstance(config.get("query"), dict):
            return {"failure": "es_failed"}
        return {"config": config}

    @staticmethod
    def _explore(state: AskState) -> AskState:
        # Reserved branch: exploration has no metadata source or MQL generation yet.
        return {"is_exploratory": True, "failure": "exploration_unavailable"}

    async def _plan(self, state: AskState) -> AskState:
        try:
            mql = await self.ai.make_mql(state["standalone_question"], state["config"])
        except Exception:
            return {"failure": "mql_failed"}
        if mql.get("error"):
            error = mql["error"]
            if isinstance(error, dict) and error.get("kind") == "missing_user_input":
                question = error.get("clarification_question")
                if isinstance(question, str) and question.strip():
                    return {
                        "pending_clarification": self._pending(
                            "mql", state["standalone_question"], question
                        ),
                        "clarification_requested": True,
                    }
            return {"mql": mql, "failure": "mql_incomplete"}
        return {"mql": mql}

    async def _generate_sql(self, state: AskState) -> AskState:
        try:
            return {"sql": await self.ai.make_sql(state["mql"])}
        except Exception:
            return {"failure": "sql_failed"}

    def _validate_sql(self, state: AskState) -> AskState:
        try:
            max_rows = self.settings.max_result_rows if self.settings else 50
            executable_sql = validate_sql(state["sql"], max_rows)
        except SQLValidationError as exc:
            return {
                "failure": "sql_validation_failed",
                "sql_validation_error": {"code": exc.code, "message": str(exc)},
            }
        return {"validated_sql": executable_sql, "sql_validation_error": None}

    async def _execute(self, state: AskState) -> AskState:
        try:
            rows, truncated = await self.doris.execute(state["validated_sql"])
        except Exception:
            return {"failure": "query_failed"}
        return {"rows": rows, "truncated": truncated}

    async def _answer(self, state: AskState) -> AskState:
        try:
            text = await self.ai.summarize(
                state["standalone_question"], state["rows"], total_truncated=state["truncated"]
            )
        except Exception:
            return {"failure": "answer_failed"}
        return {"text": text}

    @staticmethod
    def _format_output(state: AskState) -> AskState:
        return {"output_text": state["text"]}

    def _remember(self, state: AskState) -> AskState:
        entry: HistoryEntry = {
            "original_question": state["incoming_question"],
            "standalone_question": state["standalone_question"],
        }
        return {"history": [*(state.get("history") or []), entry][-self.HISTORY_LIMIT :]}

    @staticmethod
    def _clarify(state: AskState) -> AskState:
        return {
            "text": state["pending_clarification"]["clarification_question"],
            "reply_type": "clarify",
        }

    @staticmethod
    def _reply(state: AskState) -> AskState:
        return {"text": FAILURE_MESSAGES[state["failure"]], "reply_type": "error"}
