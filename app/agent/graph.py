"""LangGraph wiring for the 12-node NL2SQL workflow.

Edges follow SRS 4.2:
  extract_keywords -> {recall_column, recall_metric, recall_value} (fan-out)
  recall_*         -> merge_retrieved_info  (fan-in)
  merge_retrieved_info -> {filter_table, filter_metric} (fan-out)
  filter_*         -> add_extra_context     (fan-in)
  add_extra_context -> generate_sql
  generate_sql     -> validate_sql
  validate_sql     -> run_sql                (when ok)
                  |-> correct_sql -> validate_sql (when fail; bounded retry)
  run_sql          -> END

Total nodes: exactly 12 (per SRS).
"""
from __future__ import annotations
from langgraph.graph import END, START, StateGraph

from app.agent.nodes.add_extra_context import add_extra_context
from app.agent.nodes.correct_sql import correct_sql
from app.agent.nodes.extract_keywords import extract_keywords
from app.agent.nodes.filter_metric import filter_metric
from app.agent.nodes.filter_table import filter_table
from app.agent.nodes.generate_sql import generate_sql
from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agent.nodes.recall_column import recall_column
from app.agent.nodes.recall_metric import recall_metric
from app.agent.nodes.recall_value import recall_value
from app.agent.nodes.run_sql import run_sql
from app.agent.nodes.validate_sql import validate_sql
from app.agent.state import AgentState


MAX_CORRECT_ATTEMPTS = 2


def _should_correct(state: AgentState) -> str:
    err = state.get("sql_error")
    attempts = int(state.get("validate_attempts") or 0)
    if err and attempts <= MAX_CORRECT_ATTEMPTS:
        return "correct_sql"
    # fall through to run_sql regardless (we don't want to loop forever)
    return "run_sql"


def build_graph():
    """Compile and return the LangGraph workflow."""
    g = StateGraph(AgentState)

    # ---- nodes ----
    g.add_node("extract_keywords", extract_keywords)
    g.add_node("recall_column", recall_column)
    g.add_node("recall_metric", recall_metric)
    g.add_node("recall_value", recall_value)
    g.add_node("merge_retrieved_info", merge_retrieved_info)
    g.add_node("filter_table", filter_table)
    g.add_node("filter_metric", filter_metric)
    g.add_node("add_extra_context", add_extra_context)
    g.add_node("generate_sql", generate_sql)
    g.add_node("validate_sql", validate_sql)
    g.add_node("correct_sql", correct_sql)
    g.add_node("run_sql", run_sql)

    # ---- edges ----
    g.add_edge(START, "extract_keywords")
    g.add_edge("extract_keywords", "recall_column")
    g.add_edge("extract_keywords", "recall_metric")
    g.add_edge("extract_keywords", "recall_value")

    g.add_edge("recall_column", "merge_retrieved_info")
    g.add_edge("recall_metric", "merge_retrieved_info")
    g.add_edge("recall_value", "merge_retrieved_info")

    g.add_edge("merge_retrieved_info", "filter_table")
    g.add_edge("merge_retrieved_info", "filter_metric")
    g.add_edge("filter_table", "add_extra_context")
    g.add_edge("filter_metric", "add_extra_context")

    g.add_edge("add_extra_context", "generate_sql")
    g.add_edge("generate_sql", "validate_sql")
    g.add_conditional_edges(
        "validate_sql",
        _should_correct,
        {"correct_sql": "correct_sql", "run_sql": "run_sql"},
    )
    g.add_edge("correct_sql", "validate_sql")
    g.add_edge("run_sql", END)

    return g.compile()


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph