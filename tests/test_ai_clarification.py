import unittest
from unittest.mock import AsyncMock

from sims_qa.ai import ModelError, QwenAI
from sims_qa.settings import Settings


class ClarificationModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_understand_rejects_missing_clarification_question(self):
        ai = QwenAI(Settings(_env_file=None))
        ai._chat = AsyncMock(return_value=(
            '{"standalone_question":"它有多少？","intent":"metric",'
            '"needs_clarification":true,"clarification_question":null}'
        ))
        with self.assertRaises(ModelError):
            await ai.understand("它有多少？", [])
        await ai.http.aclose()

    async def test_understand_accepts_rewritten_clarification_answer(self):
        ai = QwenAI(Settings(_env_file=None))
        ai._chat = AsyncMock(return_value=(
            '{"standalone_question":"安装工单有多少？","intent":"metric",'
            '"needs_clarification":false,"clarification_question":null}'
        ))
        pending = {
            "source": "question",
            "base_question": "它有多少？",
            "clarification_question": "哪类工单？",
            "options": [],
        }
        result = await ai.understand("安装工单", [], pending)
        self.assertEqual(result.standalone_question, "安装工单有多少？")
        self.assertNotIn("uses_pending", result.model_dump())
        await ai.http.aclose()

    async def test_config_clarification_requires_real_distinct_candidates(self):
        ai = QwenAI(Settings(_env_file=None))
        ai._chat = AsyncMock(return_value=(
            '{"action":"clarify","config_id":null,'
            '"clarification_question":"创建还是完结？","option_ids":["a","unknown"]}'
        ))
        candidates = [
            {"config_id": "a", "name": "创建工单数"},
            {"config_id": "b", "name": "完结工单数"},
        ]
        with self.assertRaises(ModelError):
            await ai.choose_config("工单数", candidates)
        ai._chat.return_value = (
            '{"action":"clarify","config_id":null,'
            '"clarification_question":"创建还是完结？","option_ids":["a","b"]}'
        )
        choice = await ai.choose_config("工单数", candidates)
        self.assertEqual(choice.option_ids, ["a", "b"])
        await ai.http.aclose()
