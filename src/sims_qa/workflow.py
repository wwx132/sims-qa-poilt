"""Assemble the LangGraph and expose the question answering entry point."""

import inspect
from time import perf_counter
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .contracts import AskOutcome, AskState
from .nodes import WorkflowNodes
from .trace import TraceWriter


class AskWorkflow(WorkflowNodes):
    def __init__(
        self,
        ai: Any,
        retriever: Any,
        doris: Any,
        settings: Any | None = None,
        tracer: TraceWriter | None = None,
    ):
        self.ai = ai
        self.retriever = retriever
        self.doris = doris
        self.settings = settings
        self.tracer = tracer or TraceWriter(
            settings.trace_log_path if settings else "logs/trace.jsonl",
            log_questions=settings.trace_log_questions if settings else False,
        )
        graph = StateGraph(AskState)
        for name, handler in (
            ("prepare", self._prepare),
            ("understand", self._understand),
            ("retrieve", self._retrieve),
            ("select_config", self._select_config),
            ("explore", self._explore),
            ("plan", self._plan),
            ("generate_sql", self._generate_sql),
            ("validate_sql", self._validate_sql),
            ("execute", self._execute),
            ("answer", self._answer),
            ("format_output", self._format_output),
            ("remember", self._remember),
            ("clarify", self._clarify),
            ("reply", self._reply),
        ):
            graph.add_node(name, self._traced(name, handler))
        graph.add_edge(START, "prepare")
        graph.add_edge("prepare", "understand")
        for node, next_node in (
            ("understand", "retrieve"),
            ("plan", "generate_sql"),
            ("generate_sql", "validate_sql"),
            ("validate_sql", "execute"),
            ("execute", "answer"),
        ):
            graph.add_conditional_edges(
                node, self._after_step,
                {"continue": next_node, "reply": "reply", "clarify": "clarify"},
            )
        for node, next_node in (("retrieve", "select_config"), ("select_config", "plan")):
            graph.add_conditional_edges(
                node, self._after_lookup,
                {"continue": next_node, "explore": "explore", "reply": "reply", "clarify": "clarify"},
            )
        graph.add_edge("explore", "reply")
        graph.add_conditional_edges(
            "answer", self._after_step,
            {"continue": "format_output", "reply": "reply", "clarify": "clarify"},
        )
        graph.add_edge("format_output", "remember")
        graph.add_edge("remember", END)
        graph.add_edge("clarify", END)
        graph.add_edge("reply", END)
        self.graph = graph.compile()
        self.session_graph = graph.compile(checkpointer=InMemorySaver())

    @staticmethod
    def _after_step(state: AskState) -> str:
        if state.get("failure"):
            return "reply"
        return "clarify" if state.get("clarification_requested") else "continue"

    @staticmethod
    def _after_lookup(state: AskState) -> str:
        if state.get("failure") == "no_config" and state.get("allow_exploration"):
            return "explore"
        return AskWorkflow._after_step(state)

    def _traced(self, name: str, handler: Any) -> Any:
        async def run(state: AskState) -> AskState:
            started = perf_counter()
            try:
                update = handler(state)
                if inspect.isawaitable(update):
                    update = await update
            except Exception as exc:
                self.tracer.record(
                    event="stage",
                    request_id=state["request_id"],
                    stage=name,
                    status="error",
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                    error_type=type(exc).__name__,
                )
                raise

            failure = update.get("failure") or (state.get("failure") if name == "reply" else None)
            details: dict[str, Any] = {}
            if failure:
                details["failure_code"] = failure
            if name == "select_config" and update.get("config"):
                details["config_id"] = update["config"]["config_id"]
            if update.get("clarification_requested"):
                details["clarification_source"] = update["pending_clarification"]["source"]
            if name == "execute" and update.get("rows") is not None:
                details["row_count"] = len(update["rows"])
                details["truncated"] = update["truncated"]
            if name == "understand" and self.tracer.log_questions and update.get("standalone_question"):
                details["standalone_question"] = update["standalone_question"]
            self.tracer.record(
                event="stage",
                request_id=state["request_id"],
                stage=name,
                status="failed" if failure else "clarify" if update.get("clarification_requested") else "success",
                duration_ms=round((perf_counter() - started) * 1000, 2),
                **details,
            )
            return update

        return run

    async def ask_result(
        self, question: str, session_id: str | None = None, request_id: str | None = None,
        allow_exploration: bool = False,
    ) -> AskOutcome:
        request_id = request_id or uuid4().hex
        started = perf_counter()
        start_details = {"question": question} if self.tracer.log_questions else {}
        self.tracer.record(event="request_start", request_id=request_id, **start_details)
        inputs = {
            "request_id": request_id,
            "incoming_question": question,
            "allow_exploration": allow_exploration,
        }
        try:
            if session_id:
                state = await self.session_graph.ainvoke(
                    inputs, config={"configurable": {"thread_id": session_id}}
                )
            else:
                state = await self.graph.ainvoke(inputs)
        except Exception as exc:
            self.tracer.record(
                event="request_end",
                request_id=request_id,
                status="error",
                duration_ms=round((perf_counter() - started) * 1000, 2),
                error_type=type(exc).__name__,
            )
            raise
        reply_type = state.get("reply_type") or "answer"
        self.tracer.record(
            event="request_end",
            request_id=request_id,
            status="failed" if state.get("failure") else "clarify" if reply_type == "clarify" else "success",
            reply_type=reply_type,
            duration_ms=round((perf_counter() - started) * 1000, 2),
            failure_code=state.get("failure"),
            is_exploratory=bool(state.get("is_exploratory")),
        )
        return AskOutcome(
            type=reply_type,
            text=state["output_text"] if reply_type == "answer" else state["text"],
            request_id=request_id,
            code=state.get("failure"),
            is_exploratory=bool(state.get("is_exploratory")),
        )

    async def ask(
        self, question: str, session_id: str | None = None, request_id: str | None = None,
        allow_exploration: bool = False,
    ) -> str:
        return (await self.ask_result(question, session_id, request_id, allow_exploration)).text
