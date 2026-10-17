"""
temproven/context.py
────────────────────
Explicit execution context passed to services, gateway calls, and agents.
Eliminates globals, thread-locals, and singletons, ensuring full isolation
and reproducible fresh-session execution.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from temproven.clock import Clock
    from temproven.memory.episodic import EpisodicRecorder


class QueryCategory(str, Enum):
    """Category of Cypher query for honest metric tracking."""
    DOMAIN = "DOMAIN"              # Real diagnostic/enforcement queries (coverage, impact, quarantine, gates)
    BOOKKEEPING = "BOOKKEEPING"    # Overhead queries (events, handoffs, metrics persistence)


@dataclass
class MetricsScope:
    """Tracks named query counts and timing for one incident execution."""
    domain_queries: int = 0
    bookkeeping_queries: int = 0
    query_counts: dict[str, int] = field(default_factory=dict)
    query_elapsed_ms: dict[str, float] = field(default_factory=dict)
    steps_executed: int = 0
    steps_skipped: int = 0
    handoffs_count: int = 0
    start_perf_time: float = field(default_factory=time.perf_counter)
    end_perf_time: float | None = None

    def record_query(self, name: str, category: QueryCategory, elapsed_ms: float) -> None:
        if category == QueryCategory.DOMAIN:
            self.domain_queries += 1
        else:
            self.bookkeeping_queries += 1

        self.query_counts[name] = self.query_counts.get(name, 0) + 1
        self.query_elapsed_ms[name] = round(self.query_elapsed_ms.get(name, 0.0) + elapsed_ms, 2)

    def finish(self) -> float:
        """Mark completion and return elapsed ms."""
        if self.end_perf_time is None:
            self.end_perf_time = time.perf_counter()
        return round((self.end_perf_time - self.start_perf_time) * 1000, 2)


@dataclass
class RunContext:
    """Explicit runtime context passed into agent actions and service calls."""
    incident_id: str
    actor_agent: str
    clock: Clock
    metrics: MetricsScope | None = None
    recorder: EpisodicRecorder | None = None
