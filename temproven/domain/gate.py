"""
temproven/domain/gate.py
────────────────────────
Backend publication gate.

RULES:
- `attempt_publish(report_id, actor)` re-evaluates live at clock.now().
- Checks:
  (a) Is any used dataset QUARANTINED?
  (b) Re-evaluates fresh temporal coverage over all upstream experiments.
      An unprocessed lapse is still caught and blocked.
- Result ALLOW only if ALL upstream usages are COVERED and no dataset is quarantined.
- Result BLOCK if quarantined data or NOT_COVERED usage exists.
- Result REVIEW_REQUIRED if any usage is UNKNOWN or CONFLICTING.
- Always writes a PublicationAttempt with reason and evidence path.
- ONLY ALLOW changes report status to PUBLISHED.
- UI button is merely a caller; backend enforces all rules even if UI is bypassed.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

from temproven.domain.temporal import CoverageStatus, evaluate_usage_coverage
from temproven.graph import queries

if TYPE_CHECKING:
    from temproven.clock import Clock
    from temproven.graph.gateway import GraphGateway


class GateDecision(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass
class GateResult:
    decision: GateDecision
    report_id: str
    actor: str
    prior_status: str
    new_status: str
    reason: str
    evidence_path: list[dict[str, Any]] = field(default_factory=list)
    at: int = 0
    attempt_id: str = ""

    @property
    def is_allowed(self) -> bool:
        return self.decision == GateDecision.ALLOW


class PublicationGate:
    """Backend service that governs report publication based on graph dependencies."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def attempt_publish(
        self,
        gw: GraphGateway,
        report_id: str,
        actor: str,
        attempt_id: str | None = None,
    ) -> GateResult:
        """
        Evaluate live graph evidence and determine if a report may be published.
        """
        now = self.clock.now_epoch()
        if attempt_id is None:
            attempt_id = f"attempt_{report_id}_{uuid.uuid4().hex[:8]}"


        # 1. Fetch report details and upstream dependency tree
        rows = gw.query(
            queries.Q_REPORT_UPSTREAM_DEPENDENCIES,
            {"report_id": report_id},
            name="GATE_FETCH_REPORT_DEPS",
        )

        if not rows or not rows[0].get("report_id"):
            raise ValueError(f"Report '{report_id}' does not exist in graph")

        report_info = rows[0]
        prior_status = report_info.get("report_status") or "DRAFT"
        used_datasets = [d for d in report_info.get("used_datasets", []) if d and d.get("id")]
        upstream_usages = [u for u in report_info.get("upstream_usages", []) if u and u.get("usage_id")]

        # 2. Check: Any dataset QUARANTINED?
        quarantined = [d for d in used_datasets if d.get("status") == "QUARANTINED"]
        if quarantined:
            reason = f"Report blocked: used dataset(s) quarantined: {[d['id'] for d in quarantined]}"
            return self._record_attempt(
                gw,
                attempt_id=attempt_id,
                report_id=report_id,
                actor=actor,
                decision=GateDecision.BLOCK,
                prior_status=prior_status,
                new_status=prior_status,
                reason=reason,
                evidence_path=[{"quarantined_datasets": quarantined}],
                now=now,
            )

        # 3. Check: Fresh coverage check across ALL upstream experiment usages
        coverage_evidences: list[dict[str, Any]] = []
        has_not_covered = False
        has_unknown = False
        has_conflicting = False
        blocked_reasons: list[str] = []

        for usage in upstream_usages:
            u_id = usage["usage_id"]
            exp_id = usage.get("exp_id")

            cov_results = evaluate_usage_coverage(
                gw,
                t=now,
                usage_id=u_id,
                experiment_id=exp_id,
            )

            for cr in cov_results:
                coverage_evidences.append({
                    "usage_id": cr.usage_id,
                    "experiment_id": cr.experiment_id,
                    "status": cr.status.value,
                    "gaps": cr.uncovered_gaps,
                    "evidence_paths": cr.evidence_paths,
                })

                if cr.status == CoverageStatus.NOT_COVERED:
                    has_not_covered = True
                    blocked_reasons.append(
                        f"Usage {cr.usage_id} on {cr.experiment_id} is NOT_COVERED (gaps: {cr.uncovered_gaps})"
                    )
                elif cr.status == CoverageStatus.UNKNOWN:
                    has_unknown = True
                    blocked_reasons.append(
                        f"Usage {cr.usage_id} on {cr.experiment_id} has UNKNOWN certification coverage"
                    )
                elif cr.status == CoverageStatus.CONFLICTING:
                    has_conflicting = True
                    blocked_reasons.append(
                        f"Usage {cr.usage_id} on {cr.experiment_id} has CONFLICTING certification coverage"
                    )

        # Determine decision
        if has_not_covered:
            decision = GateDecision.BLOCK
            reason = f"Report blocked due to invalid upstream instrument coverage: {'; '.join(blocked_reasons)}"
            new_status = prior_status
        elif has_unknown or has_conflicting:
            decision = GateDecision.REVIEW_REQUIRED
            reason = f"Report requires manual review: {'; '.join(blocked_reasons)}"
            new_status = prior_status
        else:
            decision = GateDecision.ALLOW
            reason = f"All upstream datasets active and all {len(upstream_usages)} usages validly covered"
            new_status = "PUBLISHED"
            # Update report status in graph
            gw.query(
                queries.Q_UPDATE_REPORT_STATUS,
                {"report_id": report_id, "new_status": "PUBLISHED"},
                name="GATE_PUBLISH_REPORT",
            )

        return self._record_attempt(
            gw,
            attempt_id=attempt_id,
            report_id=report_id,
            actor=actor,
            decision=decision,
            prior_status=prior_status,
            new_status=new_status,
            reason=reason,
            evidence_path=coverage_evidences,
            now=now,
        )

    def _record_attempt(
        self,
        gw: GraphGateway,
        attempt_id: str,
        report_id: str,
        actor: str,
        decision: GateDecision,
        prior_status: str,
        new_status: str,
        reason: str,
        evidence_path: list[dict[str, Any]],
        now: int,
    ) -> GateResult:
        """Persist PublicationAttempt node into graph for traceability."""
        gw.query(
            queries.Q_RECORD_PUBLICATION_ATTEMPT,
            {
                "attempt_id": attempt_id,
                "report_id": report_id,
                "actor": actor,
                "result": decision.value,
                "reason": reason,
                "evidence_path_json": json.dumps(evidence_path),
                "at": now,
            },
            name="RECORD_PUBLICATION_ATTEMPT",
        )

        return GateResult(
            decision=decision,
            report_id=report_id,
            actor=actor,
            prior_status=prior_status,
            new_status=new_status,
            reason=reason,
            evidence_path=evidence_path,
            at=now,
            attempt_id=attempt_id,
        )
