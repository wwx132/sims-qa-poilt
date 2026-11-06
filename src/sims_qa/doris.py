from __future__ import annotations

from typing import Any

import anyio

from .settings import Settings


class QueryError(RuntimeError):
    pass


class DorisClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _execute(self, sql: str) -> tuple[list[dict[str, Any]], bool]:
        try:
            import pymysql
        except ImportError as exc:
            raise QueryError("缺少 pymysql；请安装项目依赖") from exc
        connection = pymysql.connect(
            host=self.settings.doris_host,
            port=self.settings.doris_port,
            user=self.settings.doris_user,
            password=self.settings.doris_password,
            database=self.settings.doris_database,
            connect_timeout=self.settings.doris_timeout_seconds,
            read_timeout=self.settings.doris_timeout_seconds,
            write_timeout=self.settings.doris_timeout_seconds,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                rows = list(cursor.fetchmany(self.settings.max_result_rows + 1))
            truncated = len(rows) > self.settings.max_result_rows
            return rows[: self.settings.max_result_rows], truncated
        finally:
            connection.close()

    async def execute(self, sql: str) -> tuple[list[dict[str, Any]], bool]:
        return await anyio.to_thread.run_sync(self._execute, sql)
