"""
temproven/memory/semantic.py
────────────────────────────
Semantic Memory implementation for Temproven.

Manages durable factual beliefs, provenance, and belief-change explanations:
- Provenance tracking linking Claim nodes to Evidence nodes.
- Distinction between valid time (applies_from / applies_until) and knowledge time (learned_at / recorded_at).
- Bitemporal belief reconstruction without relying on cached Claim.status.
- Policy enforcement: authoritative evidence supersedes verified claims; unverified
  contradictions remain UNRESOLVED without causing automatic quarantine.
- Live downstream coverage effect computation between T_before and T_after.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from temproven.domain.temporal import evaluate_usage_coverage
from temproven.graph import queries

if TYPE_CHECKING:
    from temproven.graph.gateway import GraphGateway


@dataclass
class EvidenceSummary:
    """Provenance details for the evidence supporting a claim."""
    id: str
    source_type: str
    trust_level: str
    observed_at: int | None = None
    recorded_at: int | None = None


@dataclass
class BeliefClaim:
    """A factual claim node reconstructed with temporal bounds and evidence."""
    claim_id: str
    valid_from: int
    valid_until: int
    recorded_at: int
    verified_at: int | None
    is_effective: bool
    summary: str = ""
    evidence: EvidenceSummary | None = None


@dataclass
class UsageCoverageEffect:
    """Downstream usage coverage impact caused by a belief change."""
    usage_id: str
    experiment_id: str
    status_before: str
    status_after: str


@dataclass
class BeliefChangeExplanation:
    """Structured explanation of a belief change at a given recorded knowledge time."""
    subject_id: str
    as_of_recorded: int
    change_kind: str  # SUPERSEDED | UNRESOLVED_CONTRADICTION | NO_CHANGE | NO_PROVENANCE
    previous_belief: BeliefClaim | None
    current_belief: BeliefClaim | None
    learned_at: int | None
    applies_from: int | None
    applies_until: int | None
    rule_id: str
    rationale: str
    coverage_effects: list[UsageCoverageEffect] = field(default_factory=list)

    def render_text(self) -> str:
        """Render a deterministic, readable plain-text explanation (no LLM)."""
        lines = [
            f"=== Belief Change Explanation for Subject: {self.subject_id} ===",
            f"As Of Recorded Time: {self.as_of_recorded} (UTC: {datetime.fromtimestamp(self.as_of_recorded, tz=timezone.utc).isoformat()})",
            f"Change Kind:         {self.change_kind}",
            f"Applied Policy Rule: {self.rule_id}",
            f"Rationale:           {self.rationale}",
        ]

        if self.learned_at is not None:
            learned_iso = datetime.fromtimestamp(self.learned_at, tz=timezone.utc).isoformat()
            lines.append(f"Learned At (Knowledge Time): {self.learned_at} ({learned_iso})")

        if self.applies_from is not None and self.applies_until is not None:
            from_iso = datetime.fromtimestamp(self.applies_from, tz=timezone.utc).isoformat()
            until_iso = datetime.fromtimestamp(self.applies_until, tz=timezone.utc).isoformat()
            lines.append(f"Valid Time Window:           [{self.applies_from}, {self.applies_until}) ({from_iso} to {until_iso})")

        if self.previous_belief:
            pb = self.previous_belief
            ev_id = pb.evidence.id if pb.evidence else "NONE"
            lines.append(f"Previous Belief: Claim {pb.claim_id} valid [{pb.valid_from}, {pb.valid_until}) backed by Evidence {ev_id}")

        if self.current_belief:
            cb = self.current_belief
            ev_id = cb.evidence.id if cb.evidence else "NONE"
            lines.append(f"Current Belief:  Claim {cb.claim_id} valid [{cb.valid_from}, {cb.valid_until}) backed by Evidence {ev_id}")

        if self.coverage_effects:
            lines.append("Downstream Usage Coverage Effects:")
            for eff in self.coverage_effects:
                lines.append(f"  * Usage {eff.usage_id} ({eff.experiment_id}): {eff.status_before} -> {eff.status_after}")
        else:
            lines.append("Downstream Usage Coverage Effects: None (coverage unchanged)")

        return "\n".join(lines)


def explain_belief_change(
    gw: GraphGateway,
    subject_id: str,
    as_of_recorded: int | None = None,
) -> BeliefChangeExplanation:
    """
    Explain how factual beliefs about subject_id evolved as of knowledge time as_of_recorded.
    Bitemporally reconstructs truth from recorded_at, verified_at, [:SUPERSEDES], and [:CONTRADICTS]
    edges, never reading or filtering on cached Claim.status.
    """
    if as_of_recorded is None:
        as_of_recorded = int(datetime.now(tz=timezone.utc).timestamp())

    # Query all claims associated with subject
    rows = gw.query(
        queries.Q_GET_SUBJECT_CLAIMS,
        {"subject_id": subject_id},
        name="GET_SUBJECT_CLAIMS",
    )

    if not rows:
        return BeliefChangeExplanation(
            subject_id=subject_id,
            as_of_recorded=as_of_recorded,
            change_kind="NO_CHANGE",
            previous_belief=None,
            current_belief=None,
            learned_at=None,
            applies_from=None,
            applies_until=None,
            rule_id="subject-not-found",
            rationale=f"No claims found for subject {subject_id}.",
        )

    # 1. Provenance check: verify every claim recorded as of T has supporting Evidence
    for r in rows:
        if r["recorded_at"] <= as_of_recorded:
            if not r.get("evidence_id"):
                return BeliefChangeExplanation(
                    subject_id=subject_id,
                    as_of_recorded=as_of_recorded,
                    change_kind="NO_PROVENANCE",
                    previous_belief=None,
                    current_belief=None,
                    learned_at=r["recorded_at"],
                    applies_from=r.get("valid_from"),
                    applies_until=r.get("valid_until"),
                    rule_id="provenance-check-failed",
                    rationale=f"Claim {r['claim_id']} lacks verifiable supporting Evidence relationship.",
                )

    # 2. Filter claims recorded as of as_of_recorded
    known_claims: list[dict[str, Any]] = [
        r for r in rows if r["recorded_at"] <= as_of_recorded
    ]

    # Reconstruct claims and relationships as of knowledge time T
    claims_by_id: dict[str, BeliefClaim] = {}
    superseded_claims: set[str] = set()
    superseding_claims: dict[str, str] = {}  # new_claim_id -> old_claim_id
    contradictions: list[dict[str, Any]] = []

    for r in known_claims:
        cid = r["claim_id"]
        ev = None
        if r.get("evidence_id"):
            ev = EvidenceSummary(
                id=r["evidence_id"],
                source_type=r.get("evidence_source_type") or "UNKNOWN",
                trust_level=r.get("evidence_trust_level") or "UNKNOWN",
                observed_at=r.get("evidence_observed_at"),
                recorded_at=r.get("evidence_recorded_at"),
            )

        # Parse superseders active as of T
        raw_superseders = r.get("superseders") or []
        for s in raw_superseders:
            if s and s.get("superseder_id"):
                s_rec = s.get("recorded_at")
                s_ver = s.get("verified_at")
                if s_rec is not None and s_rec <= as_of_recorded and s_ver is not None and s_ver <= as_of_recorded:
                    superseded_claims.add(cid)
                    superseding_claims[s["superseder_id"]] = cid

        # Parse contradictions active as of T
        raw_contradictions = r.get("contradictions") or []
        for c in raw_contradictions:
            if c and c.get("contradictor_id"):
                c_rec = c.get("recorded_at")
                if c_rec is not None and c_rec <= as_of_recorded:
                    contradictions.append(c)

        is_verified = (r.get("verified_at") is not None and r["verified_at"] <= as_of_recorded)
        claims_by_id[cid] = BeliefClaim(
            claim_id=cid,
            valid_from=r["valid_from"],
            valid_until=r["valid_until"],
            recorded_at=r["recorded_at"],
            verified_at=r.get("verified_at"),
            is_effective=is_verified and (cid not in superseded_claims),
            summary=r.get("summary") or "",
            evidence=ev,
        )

    # Update is_effective after complete supersession pass
    for cid in superseded_claims:
        if cid in claims_by_id:
            claims_by_id[cid].is_effective = False

    # 3. Classify belief change
    # Case A: A superseding claim became effective
    if superseding_claims:
        # Find the latest superseding claim
        latest_sup_id = max(superseding_claims.keys(), key=lambda k: claims_by_id[k].recorded_at if k in claims_by_id else 0)
        curr_claim = claims_by_id.get(latest_sup_id)
        prev_claim_id = superseding_claims[latest_sup_id]
        prev_claim = claims_by_id.get(prev_claim_id)

        learned_at = curr_claim.recorded_at if curr_claim else as_of_recorded
        applies_from = curr_claim.valid_from if curr_claim else None
        applies_until = curr_claim.valid_until if curr_claim else None

        # Check coverage effects on downstream usages
        coverage_effects: list[UsageCoverageEffect] = []
        inst_rows = gw.query(
            queries.Q_GET_CERTIFICATE_INSTRUMENT,
            {"cert_id": subject_id},
            name="GET_CERT_INSTRUMENT",
        )
        if inst_rows and inst_rows[0].get("instrument_id"):
            inst_id = inst_rows[0]["instrument_id"]
            t_before = learned_at - 1
            t_after = learned_at
            cov_before = {u.usage_id: u for u in evaluate_usage_coverage(gw, t=t_before, instrument_id=inst_id)}
            cov_after = {u.usage_id: u for u in evaluate_usage_coverage(gw, t=t_after, instrument_id=inst_id)}
            for uid, u_bef in cov_before.items():
                u_aft = cov_after.get(uid)
                if u_aft and u_bef.status != u_aft.status:
                    coverage_effects.append(UsageCoverageEffect(
                        usage_id=uid,
                        experiment_id=u_bef.experiment_id,
                        status_before=u_bef.status.value,
                        status_after=u_aft.status.value,
                    ))

        return BeliefChangeExplanation(
            subject_id=subject_id,
            as_of_recorded=as_of_recorded,
            change_kind="SUPERSEDED",
            previous_belief=prev_claim,
            current_belief=curr_claim,
            learned_at=learned_at,
            applies_from=applies_from,
            applies_until=applies_until,
            rule_id="authoritative-evidence-supersedes-verified-claim",
            rationale="Authoritative calibration evidence supersedes prior validity claim after verified sensor drift/update.",
            coverage_effects=coverage_effects,
        )

    # Case B: An unverified contradiction exists
    if contradictions:
        # Verified claim remains effective
        effective_claims = [c for c in claims_by_id.values() if c.is_effective]
        curr_claim = effective_claims[0] if effective_claims else None
        contra = contradictions[0]

        return BeliefChangeExplanation(
            subject_id=subject_id,
            as_of_recorded=as_of_recorded,
            change_kind="UNRESOLVED_CONTRADICTION",
            previous_belief=None,
            current_belief=curr_claim,
            learned_at=contra.get("recorded_at"),
            applies_from=curr_claim.valid_from if curr_claim else None,
            applies_until=curr_claim.valid_until if curr_claim else None,
            rule_id="unverified-contradiction-never-overrides",
            rationale="Unverified contradictory claim recorded. Policy dictates unverified claims never cause quarantine or override verified claims; marked UNRESOLVED pending human review.",
            coverage_effects=[],
        )

    # Case C: Single established belief, no change
    effective_claims = [c for c in claims_by_id.values() if c.is_effective]
    curr_claim = effective_claims[0] if effective_claims else None

    return BeliefChangeExplanation(
        subject_id=subject_id,
        as_of_recorded=as_of_recorded,
        change_kind="NO_CHANGE",
        previous_belief=None,
        current_belief=curr_claim,
        learned_at=curr_claim.recorded_at if curr_claim else None,
        applies_from=curr_claim.valid_from if curr_claim else None,
        applies_until=curr_claim.valid_until if curr_claim else None,
        rule_id="initial-claim-remains-valid",
        rationale="No superseding or contradictory claims recorded as of this time.",
        coverage_effects=[],
    )
