"""Metadata repository (Phase 6.5 / V1.0).

A thin facade over MetadataClient that hides the underlying pymysql/pool
details from node code and centralises the queries the workflow needs:

    get_table(table_id) -> dict | None
    get_columns(table_id, names=None) -> list[dict]
    list_metrics() -> list[dict]
    get_metric_related_columns(metric_id) -> list[str]
    get_pk_fk_columns(table_id) -> list[dict]

Node code (merge_retrieved_info, filter_table, generate_sql) consumes this
facade instead of calling MetadataClient directly, which makes unit tests
easy (pass a MetaRepo with stub data instead of needing a live MySQL).
"""
from __future__ import annotations
import json
from typing import Any, Optional, Protocol


class _MetadataLike(Protocol):
    """Anything that quacks like app.clients.mysql_client.MetadataClient."""

    def list_tables(self) -> list[dict[str, Any]]: ...
    def list_columns(self, table_id: Optional[str] = None) -> list[dict[str, Any]]: ...
    def list_metrics(self) -> list[dict[str, Any]]: ...


# Standard PK/FK column names per SRS 4.2.5 / sample schema. Tables not listed
# here get no automatic PK/FK injection.
_STANDARD_PK_FK: dict[str, list[str]] = {
    "fact_order":  ["customer_id", "product_id", "date_id", "region_id"],
    "dim_customer": ["customer_id"],
    "dim_product":  ["product_id"],
    "dim_region":   ["region_id"],
    "dim_date":     ["date_id"],
}


def _parse_alias(value: Any) -> list[str]:
    """metric_info.related_columns can be a JSON list, a plain string, or None."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str):
        try:
            v = json.loads(value)
            if isinstance(v, list):
                return [str(x) for x in v if x]
        except Exception:
            pass
        return [t.strip() for t in value.replace(",", " ").split() if t.strip()]
    return [str(value)]


class MetaRepo:
    """Repository facade over the metadata database.

    All methods are intentionally tiny and side-effect-free so unit tests can
    swap in a stub.
    """

    def __init__(self, metadata: _MetadataLike) -> None:
        self._meta = metadata

    # ----- tables ----------------------------------------------------------

    def get_table(self, table_id: str) -> Optional[dict[str, Any]]:
        for t in self._meta.list_tables() or []:
            if t.get("id") == table_id:
                return t
        return None

    # ----- columns ---------------------------------------------------------

    def get_columns(
        self,
        table_id: str,
        names: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """Return columns for a table, optionally filtered to a name subset."""
        all_cols = self._meta.list_columns(table_id) or []
        if not names:
            return all_cols
        want = set(names)
        return [c for c in all_cols if c.get("name") in want]

    def get_column_by_name(self, table_id: str, name: str) -> Optional[dict[str, Any]]:
        for c in self._meta.list_columns(table_id) or []:
            if c.get("name") == name:
                return c
        return None

    def get_pk_fk_columns(self, table_id: str) -> list[dict[str, Any]]:
        """Return PK / FK column dicts for the given table per SRS 4.2.5 rule 6."""
        names = _STANDARD_PK_FK.get(table_id, [])
        if not names:
            return []
        out: list[dict[str, Any]] = []
        for c in self._meta.list_columns(table_id) or []:
            if c.get("name") in names:
                out.append(c)
        return out

    # ----- metrics ---------------------------------------------------------

    def list_metrics(self) -> list[dict[str, Any]]:
        return self._meta.list_metrics() or []

    def get_metric(self, metric_id: str) -> Optional[dict[str, Any]]:
        for m in self.list_metrics():
            if m.get("id") == metric_id:
                return m
        return None

    def get_metric_related_columns(self, metric_id: str) -> list[str]:
        m = self.get_metric(metric_id)
        if not m:
            return []
        return _parse_alias(m.get("related_columns"))


__all__ = ["MetaRepo"]