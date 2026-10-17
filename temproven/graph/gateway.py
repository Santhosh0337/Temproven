"""
temproven/graph/gateway.py
──────────────────────────
Single point of contact with FalkorDB.

DESIGN DECISIONS:
- All Cypher execution goes through this class. Nothing else imports `falkordb`
  directly.
- NO FALLBACK. If FalkorDB is not reachable, we raise DatabaseUnavailableError.
  The app shows an explicit error state. We never silently substitute an
  in-memory store.
- Parameterized queries only. Raw string concatenation of user input is
  forbidden. All query text lives in graph/queries.py as named constants.
- Per-call counters & categories: every call to query() increments counters.
  When an explicit RunContext is provided, queries are categorized as DOMAIN
  or BOOKKEEPING, tracking honest diagnostic work.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

import falkordb

from temproven import config
from temproven.context import QueryCategory

if TYPE_CHECKING:
    from temproven.context import RunContext


class DatabaseUnavailableError(Exception):
    """Raised when FalkorDB cannot be reached. Never silently swallowed."""
    pass


@dataclass
class QueryRecord:
    """Records one executed query for the learning metric."""
    name: str
    params_repr: str    # repr of params dict (no secrets)
    result_count: int
    elapsed_ms: float
    category: QueryCategory = QueryCategory.DOMAIN


BOOKKEEPING_PREFIXES = (
    "EVENT_",
    "HANDOFF_",
    "PLAYBOOK_PERSIST_",
    "PLAYBOOK_RUN_",
    "METRICS_",
    "SCHEMA_",
    "GET_TIMELINE",
    "RECONSTRUCT_",
    "CLEAR_GRAPH",
    "PING",
    "CHECK_INCIDENT_",
    "ENSURE_INCIDENT",
    "CLOSE_INCIDENT",
    "COUNT_",
)


class GraphGateway:
    """
    Manages one FalkorDB connection and one named graph.

    Usage:
        gw = GraphGateway()          # connects immediately, raises if down
        gw = GraphGateway(graph_name="temproven_test_abc123")  # throwaway graph
        results = gw.query(Q_PING, {}, name="PING")
        gw.close()
    """

    def __init__(self, graph_name: str | None = None) -> None:
        self.graph_name = graph_name or config.GRAPH_NAME
        self._client: falkordb.FalkorDB | None = None
        self._graph: Any = None
        self.query_log: list[QueryRecord] = []
        self.query_count: int = 0
        self.domain_query_count: int = 0
        self.bookkeeping_query_count: int = 0
        self._connect()

    # ── Connection ────────────────────────────────────────────────────────────

    def _connect(self) -> None:
        """
        Establish connection to FalkorDB. Raises DatabaseUnavailableError
        immediately if the host is not reachable — no retry, no fallback.
        """
        try:
            kwargs: dict[str, Any] = {
                "host": config.FALKORDB_HOST,
                "port": config.FALKORDB_PORT,
            }
            if config.FALKORDB_USERNAME:
                kwargs["username"] = config.FALKORDB_USERNAME
            if config.FALKORDB_PASSWORD:
                kwargs["password"] = config.FALKORDB_PASSWORD
            if config.FALKORDB_SSL:
                kwargs["ssl"] = True

            self._client = falkordb.FalkorDB(**kwargs)
            self._graph = self._client.select_graph(self.graph_name)
        except Exception as exc:
            raise DatabaseUnavailableError(
                f"Cannot connect to FalkorDB at "
                f"{config.FALKORDB_HOST}:{config.FALKORDB_PORT}. "
                f"Is the container running? Original error: {exc}"
            ) from exc

    # ── Query execution ───────────────────────────────────────────────────────

    def query(
        self,
        cypher: str,
        params: dict[str, Any] | None = None,
        name: str = "UNNAMED",
        category: QueryCategory | None = None,
        ctx: RunContext | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute a parameterized Cypher query and return results as a list of
        dicts (one dict per row, keyed by column name).

        Args:
            cypher: The Cypher query string (a constant from queries.py).
            params: Dict of named parameters. Never build this from raw user
                    input without validation.
            name:   Human-readable name recorded in the query log (for metric).
            category: Optional DOMAIN or BOOKKEEPING classification.
            ctx:    Optional RunContext tracking metrics and events for an incident.

        Raises:
            DatabaseUnavailableError: if the connection has been lost.
        """
        if self._graph is None:
            raise DatabaseUnavailableError("Gateway is not connected.")

        params = params or {}

        # Resolve category automatically if not supplied
        if category is None:
            if any(name.startswith(prefix) for prefix in BOOKKEEPING_PREFIXES):
                category = QueryCategory.BOOKKEEPING
            else:
                category = QueryCategory.DOMAIN

        t0 = time.perf_counter()

        try:
            result = self._graph.query(cypher, params)
        except Exception as exc:
            # Re-raise connection-level errors as DatabaseUnavailableError
            raise DatabaseUnavailableError(
                f"Query '{name}' failed: {exc}"
            ) from exc

        elapsed_ms = (time.perf_counter() - t0) * 1000

        # Materialise rows into plain dicts for easy use outside this module
        rows = []
        if result and result.result_set:
            headers = [
                col[1] if isinstance(col, (list, tuple)) and len(col) > 1 else str(col)
                for col in result.header
            ]
            for row in result.result_set:
                rows.append(dict(zip(headers, row)))

        self.query_count += 1
        if category == QueryCategory.DOMAIN:
            self.domain_query_count += 1
        else:
            self.bookkeeping_query_count += 1

        self.query_log.append(QueryRecord(
            name=name,
            params_repr=repr({k: v for k, v in params.items()}),
            result_count=len(rows),
            elapsed_ms=round(elapsed_ms, 2),
            category=category,
        ))

        # Update RunContext metrics if present
        if ctx is not None and ctx.metrics is not None:
            ctx.metrics.record_query(name, category, elapsed_ms)

            # Trigger QUERY_EXECUTED event ONLY for DOMAIN queries to prevent recursion
            if category == QueryCategory.DOMAIN and ctx.recorder is not None:
                ctx.recorder.record_query_executed(
                    gw=self,
                    ctx=ctx,
                    query_name=name,
                    param_keys=list(params.keys()),
                    result_count=len(rows),
                    elapsed_ms=round(elapsed_ms, 2),
                )

        return rows

    # ── Convenience ───────────────────────────────────────────────────────────

    def reset_counter(self) -> None:
        """Clear the query log and counters (call at the start of each act)."""
        self.query_log.clear()
        self.query_count = 0
        self.domain_query_count = 0
        self.bookkeeping_query_count = 0

    def close(self) -> None:
        """Release the connection."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
            self._graph = None
