import unittest

import httpx

from sims_qa.api import create_app
from sims_qa.settings import Settings
from sims_qa.workflow import AskOutcome


class FakeWorkflow:
    def __init__(self):
        self.session_ids = []
        self.exploration_flags = []

    async def ask_result(
        self, question: str, session_id: str | None = None,
        request_id: str | None = None, allow_exploration: bool = False,
    ) -> AskOutcome:
        self.session_ids.append(session_id)
        self.exploration_flags.append(allow_exploration)
        return AskOutcome(
            type="answer", text="结果：" + question, request_id=request_id,
            is_exploratory=allow_exploration,
        )


class APITests(unittest.IsolatedAsyncioTestCase):
    async def test_ask_returns_structured_result(self):
        workflow = FakeWorkflow()
        app = create_app(workflow=workflow, settings=Settings())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/ask", json={"question": "9月有多少工单？", "session_id": "chat-a"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "type": "answer", "text": "结果：9月有多少工单？",
            "request_id": response.headers["X-Request-ID"], "code": None,
            "is_exploratory": False,
        })
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        self.assertEqual(len(response.headers["X-Request-ID"]), 32)
        self.assertEqual(workflow.session_ids, ["chat-a"])
        self.assertEqual(workflow.exploration_flags, [False])

    async def test_exploration_flag_is_forwarded(self):
        workflow = FakeWorkflow()
        app = create_app(workflow=workflow, settings=Settings())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/ask", json={"question": "9月有多少工单？", "allow_exploration": True}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["is_exploratory"], True)
        self.assertEqual(workflow.exploration_flags, [True])

    async def test_unconfigured_service_returns_error_result(self):
        settings = Settings(
            _env_file=None,
            qwen_api_key="",
            qwen_model="",
            embedding_api_key="",
            embedding_model="",
            es_url="",
            es_index="",
            doris_host="",
            doris_user="",
            doris_database="",
        )
        app = create_app(settings=settings)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/ask", json={"question": "9月有多少工单？"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["type"], "error")
        self.assertEqual(response.json()["code"], "not_configured")
        self.assertIn("尚未配置完成", response.json()["text"])
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
