import base64
import json
import unittest

import httpx

from sims_qa.retrieval import ESRetriever
from sims_qa.settings import Settings


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_cluster_check_uses_basic_auth_without_index(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"cluster_name": "es-cluster", "version": {"number": "9.5.4"}})

        settings = Settings(es_url="https://es.test:9200", es_username="elastic", es_password="secret")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            info = await ESRetriever(settings, client).check_connection()
        self.assertEqual(info, {"cluster_name": "es-cluster", "version": "9.5.4"})
        self.assertEqual(str(requests[0].url), "https://es.test:9200/")
        expected = "Basic " + base64.b64encode(b"elastic:secret").decode()
        self.assertEqual(requests[0].headers["Authorization"], expected)

    async def test_both_paths_filter_and_rrf(self):
        bodies = []
        auth_headers = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            auth_headers.append(request.headers.get("Authorization"))
            ids = ["a", "b"] if "query" in body else ["b", "a"]
            return httpx.Response(200, json={"hits": {"hits": [
                {"_id": i, "_source": {"config_id": i, "config_type": "metric", "status": "enabled", "query": {}}}
                for i in ids
            ]}})

        settings = Settings(es_url="http://es.test:9200", es_index="pilot", es_username="elastic", es_password="secret", keyword_top_k=2, vector_top_k=2)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = await ESRetriever(settings, client).retrieve("多少工单", [0.1, 0.2], "metric")
        self.assertEqual({r["config_id"] for r in results}, {"a", "b"})
        self.assertEqual(len(bodies), 2)
        expected = "Basic " + base64.b64encode(b"elastic:secret").decode()
        self.assertEqual(auth_headers, [expected, expected])
        self.assertEqual(bodies[0]["query"]["bool"]["filter"][0], {"term": {"config_type": "metric"}})
        self.assertEqual(bodies[1]["knn"]["filter"]["bool"]["filter"][1], {"term": {"status": "enabled"}})

    async def test_disabled_config_is_not_returned_even_if_es_sends_it(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"hits": {"hits": [
                {"_id": "old", "_source": {"config_id": "old", "config_type": "metric", "status": "disabled", "query": {}}}
            ]}})

        settings = Settings(es_url="http://es.test:9200", es_index="pilot")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = await ESRetriever(settings, client).retrieve("多少工单", [0.1, 0.2], "metric")
        self.assertEqual(results, [])
