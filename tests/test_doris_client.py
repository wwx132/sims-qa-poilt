import unittest
from unittest.mock import patch

from sims_qa.doris import DorisClient
from sims_qa.settings import Settings


class FakeCursor:
    def __init__(self):
        self.sql = None
        self.fetch_count = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql):
        self.sql = sql

    def fetchmany(self, count):
        self.fetch_count = count
        return [{"id": 1}, {"id": 2}, {"id": 3}]


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True


class DorisClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_executes_validated_sql_without_config_and_trims_result(self):
        settings = Settings(_env_file=None, max_result_rows=2)
        connection = FakeConnection()
        sql = "SELECT * FROM (SELECT id FROM work_orders) AS pilot_result LIMIT 3"
        with patch("pymysql.connect", return_value=connection):
            rows, truncated = await DorisClient(settings).execute(sql)
        self.assertEqual(connection.cursor_instance.sql, sql)
        self.assertEqual(connection.cursor_instance.fetch_count, 3)
        self.assertEqual(rows, [{"id": 1}, {"id": 2}])
        self.assertTrue(truncated)
        self.assertTrue(connection.closed)
