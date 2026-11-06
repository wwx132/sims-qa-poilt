"""The pilot's full MQL contract shared by planning and SQL generation."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class TableRef(BaseModel):
    physical_name: str
    alias: str


class JoinRule(BaseModel):
    join_type: str
    table: TableRef
    on: list[dict[str, str]]


class Projection(BaseModel):
    expression: str
    alias: str


class Predicate(BaseModel):
    field: str
    operator: str
    value: Any = None


class SortRule(BaseModel):
    expression: str
    direction: Literal["ASC", "DESC"]


class MQLPlan(BaseModel):
    config_id: str
    query_type: Literal["detail", "metric"]
    source: TableRef
    select: list[Projection] = Field(min_length=1)
    joins: list[JoinRule] = Field(default_factory=list)
    where: list[Predicate] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    having: list[str] = Field(default_factory=list)
    order_by: list[SortRule] = Field(default_factory=list)
    limit: int = Field(ge=1)
    calculation: dict[str, Any] | None = None


SCHEMA_EXAMPLE = {
    "config_id": "metric.example",
    "query_type": "metric",
    "source": {"physical_name": "pilot.sims_install_work_order", "alias": "wo"},
    "select": [{"expression": "COUNT(DISTINCT wo.id)", "alias": "metric_value"}],
    "joins": [],
    "where": [
        {"field": "wo.create_time", "operator": ">=", "value": "2026-09-01 00:00:00"},
        {"field": "wo.create_time", "operator": "<", "value": "2026-10-01 00:00:00"},
    ],
    "group_by": [],
    "having": [],
    "order_by": [],
    "limit": 50,
    "calculation": {"components": [{"component_id": "count", "value_expression": "COUNT(DISTINCT wo.id)"}], "formula": "count"},
}
