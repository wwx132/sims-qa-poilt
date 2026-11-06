"""SQL-only statement checks before the Doris execution node."""

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError


class SQLValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_sql(sql: str, max_rows: int) -> str:
    if max_rows < 1:
        raise SQLValidationError("invalid_row_limit", "返回行数上限必须大于零")
    if not sql.strip():
        raise SQLValidationError("empty_sql", "SQL 为空")
    try:
        statements = sqlglot.parse(sql, read="mysql")
    except (ParseError, TokenError) as exc:
        raise SQLValidationError("parse_failed", "SQL 语法无法解析") from exc
    if len(statements) != 1 or statements[0] is None:
        raise SQLValidationError("multiple_statements", "只允许一条 SQL")

    statement = statements[0]
    if not isinstance(statement, (exp.Select, exp.Union)):
        raise SQLValidationError("read_only", "只允许只读 SELECT 查询")
    forbidden = (
        exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter,
        exp.Merge, exp.Command, exp.Into, exp.Lock,
    )
    if any(isinstance(node, forbidden) for node in statement.walk()):
        raise SQLValidationError("read_only", "SQL 包含不允许的操作")
    # Read one extra row so the result layer can report truncation accurately.
    return f"SELECT * FROM ({statement.sql(dialect='mysql')}) AS pilot_result LIMIT {max_rows + 1}"
