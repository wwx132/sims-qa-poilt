import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from sims_qa.ai import ConfigChoice
from sims_qa.trace import TraceWriter
from sims_qa.workflow import AskWorkflow


CONFIG = {
    "config_id": "metric.install_work_order_created_count",
    "config_type": "metric",
    "status": "enabled",
    "name": "创建安装工单数",
    "description": "按创建时间统计工单数",
    "query": {"tables": [{"physical_name": "pilot.sims_install_work_order"}]},
}


class FakeAI:
    def __init__(self):
        self.calls = []
        self.understand_inputs = []
        self.embedded_questions = []
        self.planned_questions = []

    async def understand(self, question, history, pending=None):
        self.calls.append("understand")
        self.understand_inputs.append((question, list(history), pending))
        if pending is not None and question in {"安装工单", "已完结的", "9月的"}:
            base = pending["base_question"]
            if question == "安装工单":
                question = base.replace("它", "安装工单")
            elif question == "已完结的":
                question = base.replace("工单数", "完结工单数")
            else:
                question = "9月" + base
        if "那10月" in question and history:
            question = history[-1]["standalone_question"].replace("9月", "10月")
        if "周报" in question:
            return SimpleNamespace(
                standalone_question=question, intent="non_query",
                needs_clarification=False, clarification_question=None,
            )
        if "它" in question and not history:
            return SimpleNamespace(
                standalone_question=question, intent="metric",
                needs_clarification=True, clarification_question="你想查询哪类工单？",
            )
        intent = "detail" if "哪些" in question else "metric"
        return SimpleNamespace(
            standalone_question=question, intent=intent,
            needs_clarification=False, clarification_question=None,
        )

    async def embed(self, question):
        self.calls.append("embed")
        self.embedded_questions.append(question)
        return [0.1, 0.2]

    async def choose_config(self, question, candidates):
        self.calls.append("choose")
        if len(candidates) > 1 and "创建" not in question and "完结" not in question:
            return ConfigChoice(
                action="clarify", clarification_question="你想统计创建的工单，还是已完结的工单？",
                option_ids=[c["config_id"] for c in candidates],
            )
        selected = next((c for c in candidates if "完结" in c["name"] and "完结" in question), candidates[0])
        return ConfigChoice(action="select", config_id=selected["config_id"])

    async def make_mql(self, question, config):
        self.calls.append("mql")
        self.planned_questions.append(question)
        return {"query_type": config["config_type"], "source": "pilot.sims_install_work_order"}

    async def make_sql(self, mql):
        self.calls.append("sql")
        return "SELECT COUNT(DISTINCT id) AS value FROM pilot.sims_install_work_order"

    async def summarize(self, question, rows, *, total_truncated):
        self.calls.append("answer")
        return f"查询结果：{rows[0]['value']} 张工单"


class FakeRetriever:
    def __init__(self, candidates=None):
        self.candidates = candidates if candidates is not None else [CONFIG]
        self.seen_type = None

    async def retrieve(self, question, vector, config_type):
        self.seen_type = config_type
        return self.candidates


class FakeDoris:
    def __init__(self):
        self.sql = None

    async def execute(self, sql):
        self.sql = sql
        return [{"value": 12}], False


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_metric_full_path(self):
        ai, es, doris = FakeAI(), FakeRetriever(), FakeDoris()
        workflow = AskWorkflow(ai, es, doris)
        result = await workflow.ask_result("9月创建了多少工单？", session_id="output-a")
        state = await workflow.session_graph.aget_state({"configurable": {"thread_id": "output-a"}})
        self.assertEqual(result.text, "查询结果：12 张工单")
        self.assertEqual(state.values["output_text"], result.text)
        self.assertEqual(es.seen_type, "metric")
        self.assertEqual(ai.calls, ["understand", "embed", "choose", "mql", "sql", "answer"])
        self.assertEqual(state.values["validated_sql"], doris.sql)
        self.assertIn("LIMIT 51", doris.sql)

    async def test_detail_routes_to_detail_configs(self):
        ai, es, doris = FakeAI(), FakeRetriever([dict(CONFIG, config_type="detail")]), FakeDoris()
        await AskWorkflow(ai, es, doris).ask("9月有哪些工单？")
        self.assertEqual(es.seen_type, "detail")

    async def test_non_query_does_not_call_es_or_doris(self):
        ai, es, doris = FakeAI(), FakeRetriever(), FakeDoris()
        result = await AskWorkflow(ai, es, doris).ask("帮我写周报")
        self.assertIn("只支持", result)
        self.assertIsNone(es.seen_type)
        self.assertIsNone(doris.sql)

    async def test_no_config_stops_before_sql(self):
        ai, es, doris = FakeAI(), FakeRetriever([]), FakeDoris()
        result = await AskWorkflow(ai, es, doris).ask("9月创建了多少工单？")
        self.assertIn("没有可回答", result)
        self.assertIsNone(doris.sql)

    async def test_no_config_with_exploration_disabled_returns_no_config(self):
        workflow = AskWorkflow(FakeAI(), FakeRetriever([]), FakeDoris())
        result = await workflow.ask_result("9月创建了多少工单？")
        self.assertEqual(result.code, "no_config")
        self.assertFalse(result.is_exploratory)

    async def test_no_config_with_exploration_enabled_returns_placeholder(self):
        ai, doris = FakeAI(), FakeDoris()
        workflow = AskWorkflow(ai, FakeRetriever([]), doris)
        result = await workflow.ask_result("9月创建了多少工单？", allow_exploration=True)
        self.assertEqual(result.type, "error")
        self.assertEqual(result.code, "exploration_unavailable")
        self.assertIn("没有匹配到治理配置", result.text)
        self.assertIn("没有探索到", result.text)
        self.assertTrue(result.is_exploratory)
        self.assertEqual(ai.calls, ["understand", "embed"])
        self.assertIsNone(doris.sql)

    async def test_config_choice_none_can_enter_exploration(self):
        class NoChoiceAI(FakeAI):
            async def choose_config(self, question, candidates):
                self.calls.append("choose")
                return ConfigChoice(action="none")

        ai = NoChoiceAI()
        workflow = AskWorkflow(ai, FakeRetriever(), FakeDoris())
        result = await workflow.ask_result("9月创建了多少工单？", allow_exploration=True)
        self.assertEqual(result.type, "error")
        self.assertEqual(result.code, "exploration_unavailable")
        self.assertTrue(result.is_exploratory)
        self.assertEqual(ai.calls, ["understand", "embed", "choose"])

    async def test_governed_answer_stays_governed_when_exploration_allowed(self):
        workflow = AskWorkflow(FakeAI(), FakeRetriever(), FakeDoris())
        result = await workflow.ask_result("9月创建了多少工单？", allow_exploration=True)
        self.assertEqual(result.type, "answer")
        self.assertFalse(result.is_exploratory)

    async def test_es_timeout_stops_at_reply(self):
        import httpx

        class TimeoutRetriever(FakeRetriever):
            async def retrieve(self, question, vector, config_type):
                raise httpx.ReadTimeout("timed out")

        ai, doris = FakeAI(), FakeDoris()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            workflow = AskWorkflow(
                ai, TimeoutRetriever(), doris, tracer=TraceWriter(str(path))
            )
            result = await workflow.ask_result("9月创建了多少工单？", allow_exploration=True)
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertIn("ES 检索超时", result.text)
        self.assertFalse(result.is_exploratory)
        self.assertEqual(ai.calls, ["understand", "embed"])
        self.assertIsNone(doris.sql)
        self.assertEqual(events[-1]["failure_code"], "es_timeout")
        self.assertEqual(events[-1]["status"], "failed")
        self.assertNotIn("execute", [event.get("stage") for event in events])

    async def test_mql_incomplete_stops_before_sql(self):
        class IncompleteAI(FakeAI):
            async def make_mql(self, question, config):
                self.calls.append("mql")
                return {"error": "missing time rule"}

        ai, doris = IncompleteAI(), FakeDoris()
        result = await AskWorkflow(ai, FakeRetriever(), doris).ask("9月创建了多少工单？")
        self.assertIn("缺少生成查询所需的信息", result)
        self.assertEqual(ai.calls, ["understand", "embed", "choose", "mql"])
        self.assertIsNone(doris.sql)

    async def test_question_clarification_restarts_full_chain_and_records_raw_and_rewritten_history(self):
        ai, es, doris = FakeAI(), FakeRetriever(), FakeDoris()
        workflow = AskWorkflow(ai, es, doris)
        first = await workflow.ask_result("它有多少？", session_id="clarify-a")
        self.assertEqual(first.type, "clarify")
        self.assertEqual(first.text, "你想查询哪类工单？")
        self.assertEqual(ai.calls, ["understand"])
        first_state = await workflow.session_graph.aget_state({"configurable": {"thread_id": "clarify-a"}})
        self.assertIsNone(first_state.values["output_text"])

        second = await workflow.ask_result("安装工单", session_id="clarify-a")
        self.assertEqual(second.type, "answer")
        self.assertEqual(ai.understand_inputs[1][2]["base_question"], "它有多少？")
        self.assertEqual(es.seen_type, "metric")
        self.assertEqual(ai.embedded_questions, ["安装工单有多少？"])
        state = await workflow.session_graph.aget_state({"configurable": {"thread_id": "clarify-a"}})
        self.assertIsNone(state.values["pending_clarification"])
        self.assertEqual(state.values["history"][-1], {
            "original_question": "安装工单",
            "standalone_question": "安装工单有多少？",
        })

    async def test_config_ambiguity_clarifies_then_reruns_retrieval(self):
        completed = dict(CONFIG, config_id="metric.completed", name="完结工单数")
        ai = FakeAI()
        workflow = AskWorkflow(ai, FakeRetriever([CONFIG, completed]), FakeDoris())
        first = await workflow.ask_result("工单数是多少？", session_id="clarify-b")
        self.assertEqual(first.type, "clarify")
        self.assertIn("创建", first.text)
        self.assertEqual(ai.calls, ["understand", "embed", "choose"])
        self.assertEqual(ai.understand_inputs[0][2], None)

        second = await workflow.ask_result("已完结的", session_id="clarify-b")
        self.assertEqual(second.type, "answer")
        self.assertEqual(ai.embedded_questions, ["工单数是多少？", "完结工单数是多少？"])
        self.assertEqual(ai.understand_inputs[1][2]["source"], "config_selection")
        self.assertEqual(len(ai.understand_inputs[1][2]["options"]), 2)
        state = await workflow.session_graph.aget_state({"configurable": {"thread_id": "clarify-b"}})
        self.assertEqual(state.values["config"]["config_id"], "metric.completed")

    async def test_complete_new_question_discards_pending_clarification(self):
        ai = FakeAI()
        workflow = AskWorkflow(ai, FakeRetriever(), FakeDoris())
        await workflow.ask_result("它有多少？", session_id="clarify-c")
        result = await workflow.ask_result("9月创建了多少工单？", session_id="clarify-c")
        self.assertEqual(result.type, "answer")
        self.assertEqual(ai.understand_inputs[1][2]["base_question"], "它有多少？")
        self.assertEqual(ai.embedded_questions, ["9月创建了多少工单？"])
        state = await workflow.session_graph.aget_state({"configurable": {"thread_id": "clarify-c"}})
        self.assertIsNone(state.values["pending_clarification"])

    async def test_non_query_disposes_pending_before_rejecting(self):
        workflow = AskWorkflow(FakeAI(), FakeRetriever(), FakeDoris())
        await workflow.ask_result("它有多少？", session_id="clarify-d")
        result = await workflow.ask_result("帮我写周报", session_id="clarify-d")
        self.assertEqual(result.type, "error")
        self.assertEqual(result.code, "non_query")
        state = await workflow.session_graph.aget_state({"configurable": {"thread_id": "clarify-d"}})
        self.assertIsNone(state.values["pending_clarification"])

    async def test_mql_missing_user_input_asks_for_clarification(self):
        class MissingConditionAI(FakeAI):
            async def make_mql(self, question, config):
                self.calls.append("mql")
                if "9月" not in question:
                    return {"error": {"kind": "missing_user_input", "clarification_question": "查询哪个月份？"}}
                return {"query_type": config["config_type"]}

        ai, doris = MissingConditionAI(), FakeDoris()
        workflow = AskWorkflow(ai, FakeRetriever(), doris)
        first = await workflow.ask_result("创建工单数是多少？", session_id="clarify-e")
        self.assertEqual(first.type, "clarify")
        self.assertEqual(first.text, "查询哪个月份？")
        self.assertIsNone(doris.sql)
        second = await workflow.ask_result("9月的", session_id="clarify-e")
        self.assertEqual(second.type, "answer")
        self.assertEqual(ai.planned_questions, [])

    async def test_sql_validation_failure_stops_before_doris(self):
        class UnsafeSQLAI(FakeAI):
            async def make_sql(self, mql):
                self.calls.append("sql")
                return "DELETE FROM pilot.sims_install_work_order"

        doris = FakeDoris()
        workflow = AskWorkflow(UnsafeSQLAI(), FakeRetriever(), doris)
        result = await workflow.ask_result("9月创建了多少工单？", session_id="sql-guard")
        self.assertEqual(result.type, "error")
        self.assertEqual(result.code, "sql_validation_failed")
        self.assertIsNone(doris.sql)
        state = await workflow.session_graph.aget_state({"configurable": {"thread_id": "sql-guard"}})
        self.assertEqual(state.values["sql_validation_error"]["code"], "read_only")
        self.assertEqual(state.values["history"], [])

    async def test_es_failure_after_clarification_does_not_leave_pending(self):
        import httpx

        class FailingRetriever(FakeRetriever):
            async def retrieve(self, question, vector, config_type):
                raise httpx.ReadTimeout("timed out")

        workflow = AskWorkflow(FakeAI(), FailingRetriever(), FakeDoris())
        await workflow.ask_result("它有多少？", session_id="clarify-f")
        result = await workflow.ask_result("安装工单", session_id="clarify-f")
        self.assertEqual(result.code, "es_timeout")
        state = await workflow.session_graph.aget_state({"configurable": {"thread_id": "clarify-f"}})
        self.assertIsNone(state.values["pending_clarification"])
        self.assertEqual(state.values["history"], [])

    async def test_same_session_rewrites_before_retrieval_and_mql(self):
        ai = FakeAI()
        workflow = AskWorkflow(ai, FakeRetriever(), FakeDoris())
        await workflow.ask("9月各服务商创建了多少工单？", session_id="chat-a")
        await workflow.ask("那10月呢？", session_id="chat-a")

        expected = "10月各服务商创建了多少工单？"
        self.assertEqual(ai.understand_inputs[1][1], [{
            "original_question": "9月各服务商创建了多少工单？",
            "standalone_question": "9月各服务商创建了多少工单？",
        }])
        self.assertEqual(ai.embedded_questions[1], expected)
        self.assertEqual(ai.planned_questions[1], expected)

    async def test_sessions_are_isolated_and_no_session_is_stateless(self):
        ai = FakeAI()
        workflow = AskWorkflow(ai, FakeRetriever(), FakeDoris())
        await workflow.ask("9月创建了多少工单？", session_id="chat-a")
        await workflow.ask("那10月呢？", session_id="chat-b")
        await workflow.ask("那10月呢？")
        self.assertEqual(ai.understand_inputs[1][1], [])
        self.assertEqual(ai.understand_inputs[2][1], [])

    async def test_failed_turn_does_not_replace_successful_history(self):
        ai = FakeAI()
        retriever = FakeRetriever()
        workflow = AskWorkflow(ai, retriever, FakeDoris())
        await workflow.ask("9月创建了多少工单？", session_id="chat-a")
        retriever.candidates = []
        await workflow.ask("查一个没有配置的问题", session_id="chat-a")
        retriever.candidates = [CONFIG]
        await workflow.ask("那10月呢？", session_id="chat-a")
        self.assertEqual(len(ai.understand_inputs[2][1]), 1)
        self.assertEqual(ai.understand_inputs[2][1][0]["original_question"], "9月创建了多少工单？")

    async def test_history_is_limited_to_five_successful_turns(self):
        ai = FakeAI()
        workflow = AskWorkflow(ai, FakeRetriever(), FakeDoris())
        for month in range(1, 8):
            await workflow.ask(f"{month}月创建了多少工单？", session_id="chat-a")
        history = ai.understand_inputs[-1][1]
        self.assertEqual(len(history), 5)
        self.assertEqual(history[0]["original_question"], "2月创建了多少工单？")

    async def test_trace_links_stages_without_logging_question_by_default(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            workflow = AskWorkflow(
                FakeAI(), FakeRetriever(), FakeDoris(), tracer=TraceWriter(str(path))
            )
            await workflow.ask("9月创建了多少工单？", request_id="request-1")
            log_text = path.read_text(encoding="utf-8")
            events = [json.loads(line) for line in log_text.splitlines()]

        self.assertEqual(events[0]["event"], "request_start")
        self.assertEqual(events[-1]["event"], "request_end")
        self.assertTrue(all(event["request_id"] == "request-1" for event in events))
        self.assertEqual(events[-1]["status"], "success")
        self.assertTrue(all(event["duration_ms"] >= 0 for event in events if "duration_ms" in event))
        self.assertEqual(
            [event["stage"] for event in events if event["event"] == "stage"],
            ["prepare", "understand", "retrieve", "select_config", "plan", "generate_sql", "validate_sql", "execute", "answer", "format_output", "remember"],
        )
        self.assertNotIn("9月创建了多少工单？", log_text)
