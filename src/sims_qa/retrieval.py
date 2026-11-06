from collections import defaultdict
from typing import Any

import httpx

from .settings import Settings


class RetrievalError(RuntimeError):
    pass


class ESRetriever:
    """Two independent ES searches, fused in application code with RRF."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self.settings = settings
        self.http = http or httpx.AsyncClient(timeout=20, verify=settings.es_verify_tls)
        self._owns_http = http is None

    def _auth(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(self.settings.es_username, self.settings.es_password)

    async def check_connection(self) -> dict[str, str]:
        """Check cluster access without requiring an index or governance documents."""
        response = await self.http.get(self.settings.es_url.rstrip("/") + "/", auth=self._auth())
        response.raise_for_status()
        try:
            info = response.json()
            return {"cluster_name": info["cluster_name"], "version": info["version"]["number"]}
        except (ValueError, KeyError, TypeError) as exc:
            raise RetrievalError("ES 根接口响应结构不符合预期") from exc

    async def aclose(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    async def _search(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        url = self.settings.es_url.rstrip("/") + "/" + self.settings.es_index + "/_search"
        response = await self.http.post(url, auth=self._auth(), json=body)
        response.raise_for_status()
        try:
            return response.json()["hits"]["hits"]
        except (KeyError, TypeError) as exc:
            raise RetrievalError("ES 响应中缺少 hits.hits") from exc

    async def retrieve(self, question: str, vector: list[float], config_type: str) -> list[dict[str, Any]]:
        if config_type not in {"detail", "metric"}:
            raise ValueError("config_type must be detail or metric")
        filters = [
            {"term": {"config_type": config_type}},
            {"term": {"status": "enabled"}},
        ]
        keyword_body = {
            "size": self.settings.keyword_top_k,
            "query": {"bool": {"filter": filters, "must": [{"match": {self.settings.es_keyword_field: question}}]}},
        }
        vector_body = {
            "size": self.settings.vector_top_k,
            "knn": {
                "field": self.settings.es_vector_field,
                "query_vector": vector,
                "k": self.settings.vector_top_k,
                "num_candidates": max(50, self.settings.vector_top_k * 5),
                "filter": {"bool": {"filter": filters}},
            },
        }
        # Sequential requests keep the implementation compatible with modest ES clusters.
        keyword_hits = await self._search(keyword_body)
        vector_hits = await self._search(vector_body)
        ranks: dict[str, float] = defaultdict(float)
        documents: dict[str, dict[str, Any]] = {}
        for hits in (keyword_hits, vector_hits):
            for rank, hit in enumerate(hits, start=1):
                source = hit.get("_source") or {}
                config_id = source.get("config_id") or hit.get("_id")
                if not config_id:
                    continue
                if source.get("config_type") != config_type or source.get("status") != "enabled":
                    continue
                ranks[config_id] += 1 / (self.settings.rrf_k + rank)
                documents[config_id] = source
        ordered = sorted(ranks, key=ranks.get, reverse=True)[: self.settings.fused_top_k]
        return [documents[config_id] for config_id in ordered]
