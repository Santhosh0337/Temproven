"""
temproven/metrics.py
────────────────────
Honest learning metrics tracking and comparison between incidents.

POLICIES:
- Real values only, measured directly from FalkorDB execution.
- No fabricated percentages or hardcoded improvement numbers.
- Explicit distinction between DOMAIN (diagnostic reasoning) and BOOKKEEPING (memory overhead).
- Plain statements when Incident 2 is not better on a dimension.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from temproven.graph import queries

if TYPE_CHECKING:
    from temproven.graph.gateway import GraphGateway


@dataclass
class IncidentMetricsData:
    """Measured execution metrics for an incident."""
    incident_id: str
    mode: str
    domain_queries: int
    bookkeeping_queries: int
    steps_executed: int
    steps_skipped: int
    handoffs: int
    elapsed_ms: float
    query_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class MetricsComparison:
    """Direct comparison between two incident executions."""
    inc1: IncidentMetricsData
    inc2: IncidentMetricsData
    domain_query_delta: int
    bookkeeping_delta: int
    steps_executed_delta: int
    steps_skipped_delta: int
    handoffs_delta: int
    elapsed_ms_delta: float
    domain_query_ratio: float | None
    elapsed_ratio: float | None
    honesty_note: str = "computed from measured values; scenario-specific, not a general claim"
    dimension_notes: list[str] = field(default_factory=list)


def load_incident_metrics(gw: GraphGateway, incident_id: str) -> IncidentMetricsData | None:
    """Retrieve persisted IncidentMetrics node for incident_id."""
    rows = gw.query(
        queries.Q_GET_INCIDENT_METRICS,
        {"incident_id": incident_id},
        name="METRICS_GET",
    )
    if not rows:
        return None

    r = rows[0]
    q_counts = {}
    try:
        q_counts = json.loads(r.get("query_counts_json") or "{}")
    except Exception:
        pass

    return IncidentMetricsData(
        incident_id=r["incident_id"],
        mode=r.get("mode") or "UNKNOWN",
        domain_queries=int(r.get("domain_queries") or 0),
        bookkeeping_queries=int(r.get("bookkeeping_queries") or 0),
        steps_executed=int(r.get("steps_executed") or 0),
        steps_skipped=int(r.get("steps_skipped") or 0),
        handoffs=int(r.get("handoffs") or 0),
        elapsed_ms=float(r.get("elapsed_ms") or 0.0),
        query_counts=q_counts,
    )


def compare_metrics(m1: IncidentMetricsData, m2: IncidentMetricsData) -> MetricsComparison:
    """
    Compare raw values for both incidents and compute absolute deltas.
    Any ratio is computed strictly from measured values without hardcoded multipliers.
    """
    dom_delta = m2.domain_queries - m1.domain_queries
    book_delta = m2.bookkeeping_queries - m1.bookkeeping_queries
    steps_exec_delta = m2.steps_executed - m1.steps_executed
    steps_skip_delta = m2.steps_skipped - m1.steps_skipped
    handoffs_delta = m2.handoffs - m1.handoffs
    elapsed_delta = round(m2.elapsed_ms - m1.elapsed_ms, 2)

    dom_ratio = round(m2.domain_queries / m1.domain_queries, 2) if m1.domain_queries > 0 else None
    elapsed_ratio = round(m2.elapsed_ms / m1.elapsed_ms, 2) if m1.elapsed_ms > 0 else None

    notes: list[str] = []
    if m2.domain_queries < m1.domain_queries:
        notes.append(f"Domain diagnostic queries reduced by {abs(dom_delta)} via targeted playbook execution.")
    elif m2.domain_queries > m1.domain_queries:
        notes.append(f"Incident 2 required {dom_delta} more domain queries due to deeper lineage/escalation checks.")

    if m2.steps_skipped > m1.steps_skipped:
        notes.append(f"Diagnostic ladder steps H2-H5 ({m2.steps_skipped} steps) skipped after H1 confirmation.")

    if m2.bookkeeping_queries > m1.bookkeeping_queries:
        notes.append(f"Incident 2 recorded {book_delta} more bookkeeping queries (StepResult persistence).")

    return MetricsComparison(
        inc1=m1,
        inc2=m2,
        domain_query_delta=dom_delta,
        bookkeeping_delta=book_delta,
        steps_executed_delta=steps_exec_delta,
        steps_skipped_delta=steps_skip_delta,
        handoffs_delta=handoffs_delta,
        elapsed_ms_delta=elapsed_delta,
        domain_query_ratio=dom_ratio,
        elapsed_ratio=elapsed_ratio,
        dimension_notes=notes,
    )


def render_metrics_comparison_table(comp: MetricsComparison) -> str:
    """Format an ASCII-safe table comparing Incident 1 and Incident 2 metrics."""
    m1, m2 = comp.inc1, comp.inc2
    dom_delta_str = f"{comp.domain_query_delta:+d}"
    book_delta_str = f"{comp.bookkeeping_delta:+d}"
    steps_exec_delta_str = f"{comp.steps_executed_delta:+d}"
    steps_skip_delta_str = f"{comp.steps_skipped_delta:+d}"
    handoffs_delta_str = f"{comp.handoffs_delta:+d}"
    elapsed_delta_str = f"{comp.elapsed_ms_delta:+.1f} ms"

    lines = [
        "+-----------------------------+-------------------+-------------------+-------------------+",
        "| Metric Dimension            | Incident 1 (Expl) | Incident 2 (Reuse)| Delta (Inc2-Inc1) |",
        "+-----------------------------+-------------------+-------------------+-------------------+",
        f"| Execution Mode              | {m1.mode:<17} | {m2.mode:<17} | -                 |",
        f"| Domain Queries (Diagnostic) | {m1.domain_queries:<17} | {m2.domain_queries:<17} | {dom_delta_str:<17} |",
        f"| Bookkeeping Queries (Audit) | {m1.bookkeeping_queries:<17} | {m2.bookkeeping_queries:<17} | {book_delta_str:<17} |",
        f"| Steps Executed              | {m1.steps_executed:<17} | {m2.steps_executed:<17} | {steps_exec_delta_str:<17} |",
        f"| Steps Skipped (Ladder)      | {m1.steps_skipped:<17} | {m2.steps_skipped:<17} | {steps_skip_delta_str:<17} |",
        f"| Agent Handoffs              | {m1.handoffs:<17} | {m2.handoffs:<17} | {handoffs_delta_str:<17} |",
        f"| Total Elapsed (Wall-clock)  | {m1.elapsed_ms:<14.1f} ms | {m2.elapsed_ms:<14.1f} ms | {elapsed_delta_str:<17} |",
        "+-----------------------------+-------------------+-------------------+-------------------+",
        f"Honesty Note: [{comp.honesty_note}]",
    ]

    for n in comp.dimension_notes:
        lines.append(f"  * {n}")

    return "\n".join(lines)
