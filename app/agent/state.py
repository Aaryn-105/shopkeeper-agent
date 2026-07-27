"""TypedDict state for the 12-node NL2SQL LangGraph workflow.

`node_history` uses an Annotated reducer so that parallel branches (recall_*,
filter_*) can both append without colliding.
"""
from __future__ import annotations
import operator
from typing import Annotated, Any, Optional, TypedDict


class AgentState(TypedDict, total=False):
    # input
    query: str
    request_id: str

    # extract_keywords
    keywords: list[str]

    # recall_column / recall_metric / recall_value
    retrieved_columns: list[dict[str, Any]]
    retrieved_metrics: list[dict[str, Any]]
    retrieved_values: list[dict[str, Any]]

    # merge_retrieved_info
    merged_table_infos: dict[str, dict[str, Any]]
    # SRS canonical 4.2.5 outputs (new in phase 6.5)
    table_infos: dict[str, dict[str, Any]]
    metric_infos: list[dict[str, Any]]

    # filter_table / filter_metric
    filtered_table_infos: dict[str, dict[str, Any]]
    filtered_metric_infos: list[dict[str, Any]]

    # add_extra_context
    extra_context: dict[str, Any]

    # generate_sql / correct_sql
    sql: str
    sql_corrected: bool

    # validate_sql
    sql_error: Optional[str]
    validate_attempts: int

    # run_sql
    result: Optional[dict[str, Any]]

    # cross-cutting: parallel-safe reducer so two branches can append
    node_history: Annotated[list[dict[str, Any]], operator.add]
    error: Optional[str]
    cache_hit: bool
    started_at: float