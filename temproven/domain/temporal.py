"""
temproven/domain/temporal.py
────────────────────────────
Bitemporal interval logic and coverage classification.

TIME SEMANTICS:
- All instants are integer epoch seconds in UTC (for Cypher & comparison)
  plus ISO-8601 UTC string for human display.
- Valid-time intervals are half-open: [start, end).
- Adjacent intervals touch without gap: [a, b) and [b, c) merge to [a, c).
- A usage [uf, uu) is covered by [vf, ve) iff vf <= uf AND uu <= ve.
- Zero-length or inverted usage intervals are invalid data -> UNKNOWN.
- VALID TIME (when fact is true in the world) is independent of
  RECORDED TIME (when system learned of the fact).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from temproven.graph.gateway import GraphGateway


# ── Time normalization helpers ──────────────────────────────────────────────

def parse_iso_to_epoch(val: str | int | float | None) -> int | None:
    """
    Parse an ISO-8601 timestamp string (with offset or Z) into integer Unix
    epoch seconds in UTC.
    If naive datetime is provided, UTC is assumed.
    If already an int/float, truncated to int.
    Returns None if val is None.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    if not isinstance(val, str):
        raise TypeError(f"Expected str or int, got {type(val)}")

    val = val.strip()
    if not val:
        return None

    # Handle Z suffix
    if val.endswith("Z"):
        val = val[:-1] + "+00:00"

    dt = datetime.fromisoformat(val)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return int(dt.timestamp())


def epoch_to_iso(epoch: int | float | None) -> str | None:
    """Convert integer epoch seconds to ISO-8601 UTC string with Z suffix."""
    if epoch is None:
        return None
    dt = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Coverage classification types ───────────────────────────────────────────

class CoverageStatus(str, Enum):
    COVERED_SINGLE = "COVERED (single)"
    COVERED_CHAIN = "COVERED (chain)"
    NOT_COVERED = "NOT_COVERED"
    UNKNOWN = "UNKNOWN"
    CONFLICTING = "CONFLICTING"


@dataclass
class EffectiveInterval:
    """A certificate validity interval effective at a specific recorded time T."""
    cert_id: str
    claim_id: str
    original_start: int | None
    original_end: int | None
    effective_start: int
    effective_end: int
    evidence_id: str | None
    revocations_applied: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CoverageResult:
    """Outcome of temporal certification verification for one InstrumentUsage."""
    status: CoverageStatus
    usage_id: str
    experiment_id: str | None
    instrument_id: str | None
    required_scope: str | None
    used_from: int | None
    used_until: int | None
    effective_intervals: list[EffectiveInterval] = field(default_factory=list)
    merged_intervals: list[tuple[int, int]] = field(default_factory=list)
    uncovered_gaps: list[tuple[int, int]] = field(default_factory=list)
    evidence_paths: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    @property
    def is_allowed(self) -> bool:
        """True only if usage is covered (single or chain)."""
        return self.status in (CoverageStatus.COVERED_SINGLE, CoverageStatus.COVERED_CHAIN)


# ── Interval arithmetic ─────────────────────────────────────────────────────

def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """
    Merge a collection of half-open intervals [start, end).
    Under half-open semantics:
    - [10, 20) and [20, 30) touch (end == start) -> merged to [10, 30).
    - [10, 25) and [20, 30) overlap -> merged to [10, 30).
    - [10, 20) and [22, 30) have gap [20, 22) -> not merged.
    Returns sorted, non-overlapping, non-touching intervals.
    """
    valid = [iv for iv in intervals if iv[0] < iv[1]]
    if not valid:
        return []

    sorted_ivs = sorted(valid, key=lambda x: (x[0], x[1]))
    merged: list[tuple[int, int]] = [sorted_ivs[0]]

    for cur_start, cur_end in sorted_ivs[1:]:
        prev_start, prev_end = merged[-1]
        if cur_start <= prev_end:
            # Overlapping or adjacent (touching)
            merged[-1] = (prev_start, max(prev_end, cur_end))
        else:
            merged.append((cur_start, cur_end))

    return merged


def compute_uncovered_gaps(
    usage_start: int,
    usage_end: int,
    merged_validity: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """
    Given a usage interval [usage_start, usage_end) and merged valid intervals,
    compute all sub-intervals of the usage not covered by any valid interval.
    """
    if usage_start >= usage_end:
        return []

    gaps: list[tuple[int, int]] = []
    current_cursor = usage_start

    for v_start, v_end in merged_validity:
        if v_end <= current_cursor:
            continue
        if v_start > current_cursor:
            # Gap before this validity interval starts
            gap_end = min(v_start, usage_end)
            if gap_end > current_cursor:
                gaps.append((current_cursor, gap_end))
            current_cursor = max(current_cursor, gap_end)
            if current_cursor >= usage_end:
                break
        # Cursor is inside this validity interval
        current_cursor = max(current_cursor, v_end)
        if current_cursor >= usage_end:
            break

    if current_cursor < usage_end:
        gaps.append((current_cursor, usage_end))

    return gaps


# ── Coverage classification logic ───────────────────────────────────────────

def classify_coverage(
    usage: dict[str, Any],
    candidate_rows: list[dict[str, Any]],
    as_of_recorded: int | None = None,
) -> CoverageResult:
    """
    Pure temporal reasoning function.
    Takes a usage definition and candidate certificate/claim rows from FalkorDB,
    evaluates validity at recorded time T, and classifies coverage.

    RULES:
    1. Zero-length or inverted usage -> UNKNOWN.
    2. Missing usage dates -> UNKNOWN.
    3. More than one effective claim on the same certificate at T -> CONFLICTING.
    4. Contradiction from non-verified claim recorded at or before T -> CONFLICTING.
    5. Missing validity dates on claims -> contributes nothing; if sole basis -> UNKNOWN.
    6. Revocations at T truncate validity (effective_at=None means full revocation).
    7. Single-interval containment checked first, then chained coverage.
    """
    usage_id = usage.get("usage_id") or usage.get("id") or "UNKNOWN_USAGE"
    experiment_id = usage.get("experiment_id")
    instrument_id = usage.get("instrument_id")
    required_scope = usage.get("required_scope")
    used_from = usage.get("used_from")
    used_until = usage.get("used_until")

    # 1. Validate usage interval
    if used_from is None or used_until is None:
        return CoverageResult(
            status=CoverageStatus.UNKNOWN,
            usage_id=usage_id,
            experiment_id=experiment_id,
            instrument_id=instrument_id,
            required_scope=required_scope,
            used_from=used_from,
            used_until=used_until,
            reason="Usage interval has null timestamps",
        )

    if used_until <= used_from:
        return CoverageResult(
            status=CoverageStatus.UNKNOWN,
            usage_id=usage_id,
            experiment_id=experiment_id,
            instrument_id=instrument_id,
            required_scope=required_scope,
            used_from=used_from,
            used_until=used_until,
            reason=f"Usage interval is zero-length or inverted: [{used_from}, {used_until})",
        )

    # Filter candidate rows for this usage
    matching_rows = [
        r for r in candidate_rows
        if (r.get("usage_id") == usage_id or not r.get("usage_id"))
    ]

    # No applicable certificates at all
    if not matching_rows or all(r.get("cert_id") is None for r in matching_rows):
        return CoverageResult(
            status=CoverageStatus.NOT_COVERED,
            usage_id=usage_id,
            experiment_id=experiment_id,
            instrument_id=instrument_id,
            required_scope=required_scope,
            used_from=used_from,
            used_until=used_until,
            uncovered_gaps=[(used_from, used_until)],
            reason="No applicable calibration certificates found for instrument and scope",
        )

    # 2. Group candidate claims by certificate and check effectiveness at T
    claims_by_cert: dict[str, list[dict[str, Any]]] = {}
    had_missing_dates = False

    for row in matching_rows:
        cert_id = row.get("cert_id")
        claim_id = row.get("claim_id")
        if not cert_id or not claim_id:
            continue

        # Condition (c): No superseders at T
        num_superseders = row.get("num_superseders", 0)
        if num_superseders > 0:
            # Superseded as of T -> not effective at T
            continue

        # Check contradictions
        contradictions = [
            c for c in row.get("contradictions", [])
            if c and c.get("id") is not None
        ]
        # Any contradiction recorded at or before T that is unverified
        unverified_contradictions = [
            c for c in contradictions
            if c.get("verified_at") is None or (as_of_recorded and c.get("verified_at") > as_of_recorded)
        ]
        if unverified_contradictions:
            return CoverageResult(
                status=CoverageStatus.CONFLICTING,
                usage_id=usage_id,
                experiment_id=experiment_id,
                instrument_id=instrument_id,
                required_scope=required_scope,
                used_from=used_from,
                used_until=used_until,
                evidence_paths=[{
                    "claim_id": claim_id,
                    "cert_id": cert_id,
                    "contradictions": unverified_contradictions,
                }],
                reason=f"Claim {claim_id} is contradicted by unverified evidence",
            )

        claims_by_cert.setdefault(cert_id, []).append(row)

    # 3. Conflict check: multiple effective claims on the same certificate
    for cert_id, claims in claims_by_cert.items():
        if len(claims) > 1:
            return CoverageResult(
                status=CoverageStatus.CONFLICTING,
                usage_id=usage_id,
                experiment_id=experiment_id,
                instrument_id=instrument_id,
                required_scope=required_scope,
                used_from=used_from,
                used_until=used_until,
                reason=f"Multiple effective claims found for certificate {cert_id} (data error)",
            )

    # 4. Construct effective validity intervals with revocation truncation
    effective_intervals: list[EffectiveInterval] = []

    for cert_id, claims in claims_by_cert.items():
        claim = claims[0]
        v_start = claim.get("valid_from")
        v_end = claim.get("valid_until")

        if v_start is None or v_end is None:
            had_missing_dates = True
            continue

        if v_end <= v_start:
            continue

        # Process revocations known at T
        revocations = [
            r for r in claim.get("revocations", [])
            if r and r.get("id") is not None
        ]
        # Full revocation if any revocation has null effective_at
        if any(r.get("effective_at") is None for r in revocations):
            # Certificate fully revoked
            continue

        # Truncate end by earliest known revocation effective_at
        effective_rev_ats = [
            r["effective_at"] for r in revocations
            if r.get("effective_at") is not None
        ]
        trunc_end = min([v_end] + effective_rev_ats) if effective_rev_ats else v_end

        if trunc_end <= v_start:
            # Revocation took effect before or at start of validity
            continue

        effective_intervals.append(EffectiveInterval(
            cert_id=cert_id,
            claim_id=claim["claim_id"],
            original_start=v_start,
            original_end=v_end,
            effective_start=v_start,
            effective_end=trunc_end,
            evidence_id=claim.get("evidence_id"),
            revocations_applied=revocations,
        ))

    # If no effective intervals could be formed
    if not effective_intervals:
        if had_missing_dates:
            return CoverageResult(
                status=CoverageStatus.UNKNOWN,
                usage_id=usage_id,
                experiment_id=experiment_id,
                instrument_id=instrument_id,
                required_scope=required_scope,
                used_from=used_from,
                used_until=used_until,
                reason="Applicable claims had missing validity timestamps",
            )
        return CoverageResult(
            status=CoverageStatus.NOT_COVERED,
            usage_id=usage_id,
            experiment_id=experiment_id,
            instrument_id=instrument_id,
            required_scope=required_scope,
            used_from=used_from,
            used_until=used_until,
            uncovered_gaps=[(used_from, used_until)],
            reason="No effective non-revoked validity interval covers this usage",
        )

    # Evidence paths helper
    def build_evidence_paths(intervals: list[EffectiveInterval]) -> list[dict[str, Any]]:
        return [
            {
                "cert_id": iv.cert_id,
                "claim_id": iv.claim_id,
                "evidence_id": iv.evidence_id,
                "original_validity": [iv.original_start, iv.original_end],
                "effective_validity": [iv.effective_start, iv.effective_end],
                "revocations": iv.revocations_applied,
            }
            for iv in intervals
        ]

    # 5. Check single-interval containment: vf <= used_from AND used_until <= ve
    for iv in effective_intervals:
        if iv.effective_start <= used_from and used_until <= iv.effective_end:
            return CoverageResult(
                status=CoverageStatus.COVERED_SINGLE,
                usage_id=usage_id,
                experiment_id=experiment_id,
                instrument_id=instrument_id,
                required_scope=required_scope,
                used_from=used_from,
                used_until=used_until,
                effective_intervals=[iv],
                merged_intervals=[(iv.effective_start, iv.effective_end)],
                uncovered_gaps=[],
                evidence_paths=build_evidence_paths([iv]),
                reason=f"Usage [{used_from}, {used_until}) fully covered by single certificate {iv.cert_id}",
            )

    # 6. Check chained coverage across multiple merged intervals
    raw_tuples = [(iv.effective_start, iv.effective_end) for iv in effective_intervals]
    merged = merge_intervals(raw_tuples)

    for m_start, m_end in merged:
        if m_start <= used_from and used_until <= m_end:
            return CoverageResult(
                status=CoverageStatus.COVERED_CHAIN,
                usage_id=usage_id,
                experiment_id=experiment_id,
                instrument_id=instrument_id,
                required_scope=required_scope,
                used_from=used_from,
                used_until=used_until,
                effective_intervals=effective_intervals,
                merged_intervals=merged,
                uncovered_gaps=[],
                evidence_paths=build_evidence_paths(effective_intervals),
                reason=f"Usage [{used_from}, {used_until}) covered by chained certificates: {merged}",
            )

    # 7. Not covered: compute exact gaps
    gaps = compute_uncovered_gaps(used_from, used_until, merged)
    return CoverageResult(
        status=CoverageStatus.NOT_COVERED,
        usage_id=usage_id,
        experiment_id=experiment_id,
        instrument_id=instrument_id,
        required_scope=required_scope,
        used_from=used_from,
        used_until=used_until,
        effective_intervals=effective_intervals,
        merged_intervals=merged,
        uncovered_gaps=gaps,
        evidence_paths=build_evidence_paths(effective_intervals),
        reason=f"Usage not fully covered; uncovered gaps: {gaps}",
    )


# ── Gateway query runner ────────────────────────────────────────────────────

def evaluate_usage_coverage(
    gw: GraphGateway,
    t: int,
    usage_id: str | None = None,
    experiment_id: str | None = None,
    instrument_id: str | None = None,
    ctx: Any | None = None,
) -> list[CoverageResult]:
    """
    Run Q_USAGE_COVERAGE_CANDIDATES on FalkorDB for recorded time $t,
    group results by usage, and classify each.
    """
    from temproven.graph.queries import Q_USAGE_COVERAGE_CANDIDATES

    params = {
        "t": t,
        "usage_id": usage_id,
        "experiment_id": experiment_id,
        "instrument_id": instrument_id,
    }
    rows = gw.query(Q_USAGE_COVERAGE_CANDIDATES, params, name="EVALUATE_USAGE_COVERAGE", ctx=ctx)

    # Group candidate rows by usage_id
    usages: dict[str, dict[str, Any]] = {}
    candidates_by_usage: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        u_id = row["usage_id"]
        if u_id not in usages:
            usages[u_id] = {
                "usage_id": u_id,
                "experiment_id": row.get("experiment_id"),
                "instrument_id": row.get("instrument_id"),
                "required_scope": row.get("required_scope"),
                "used_from": row.get("used_from"),
                "used_until": row.get("used_until"),
            }
        candidates_by_usage.setdefault(u_id, []).append(row)

    results = []
    for u_id, usage_info in usages.items():
        cand_rows = candidates_by_usage.get(u_id, [])
        res = classify_coverage(usage_info, cand_rows, as_of_recorded=t)
        results.append(res)

    return results
