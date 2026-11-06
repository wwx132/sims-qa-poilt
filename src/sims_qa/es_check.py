"""Check Elasticsearch connectivity independently of the ask workflow."""

import asyncio

import httpx

from .retrieval import ESRetriever, RetrievalError
from .settings import Settings


async def check() -> int:
    settings = Settings()
    missing = [
        name
        for name, value in (
            ("ES_URL", settings.es_url),
            ("ES_USERNAME", settings.es_username),
            ("ES_PASSWORD", settings.es_password),
        )
        if not value
    ]
    if missing:
        print("缺少 ES 连接配置：" + "、".join(missing))
        return 2

    retriever = ESRetriever(settings)
    try:
        info = await retriever.check_connection()
    except httpx.HTTPStatusError as exc:
        print(f"ES 认证或请求失败：HTTP {exc.response.status_code}")
        return 1
    except httpx.TimeoutException:
        print("ES 连接超时")
        return 1
    except (httpx.RequestError, RetrievalError) as exc:
        print(f"ES 连接失败：{exc}")
        return 1
    finally:
        await retriever.aclose()

    print(f"ES 连接成功：集群 {info['cluster_name']}，版本 {info['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
