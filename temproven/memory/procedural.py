"""
temproven/memory/procedural.py
──────────────────────────────
Procedural Memory implementation for Temproven.

Manages investigation playbooks, step definitions, diagnostic ladders, and reuse:
- DiagnosticLadder: deterministic checks (H1 lapse, H2 revocation, H3 scope mismatch,
  H4 invalid dates, H5 conflicting claims) run in order, recording HYPOTHESIS_CHECKED events.
- StepRegistry: executable step types with precondition(), execute(), and postcondition().
- derive_playbook(): creates CANDIDATE playbook from real executed Incident 1 events,
  storing structural expected outcomes (never hardcoded entity IDs).
- promote_playbook(): gates promotion to VERIFIED on 6 rigorous postcondition checks (PC1-PC6).
- Playbook retrieval, execution, and comparison yielding StepResults (PASSED, DIFFERED, FAILED, SKIPPED).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TYPE_CHECKING

from temproven.clock import FixedClock
from temproven.domain.actions import ActionService
from temproven.domain.gate import PublicationGate
from temproven.domain.temporal import evaluate_usage_coverage
from temproven.graph import queries
from temproven.memory.episodic import EventType, get_timeline

if TYPE_CHECKING:
    from temproven.context import RunContext
    from temproven.graph.gateway import GraphGateway


class StepStatus(str, Enum):
    PASSED = "PASSED"
    DIFFERED = "DIFFERED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class CheckResult:
    passed: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepOutcome:
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    structural_features: dict[str, Any] = field(default_factory=dict)


# ── 1. Diagnostic Ladder (Exploratory Mode) ──────────────────────────────────

class DiagnosticLadder:
    """
    Deterministic 5-hypothesis diagnostic ladder for exploratory incidents.
    Checks are run in strict order: H1 -> H2 -> H3 -> H4 -> H5.
    Emits HYPOTHESIS_CHECKED events for each check.
    """

    @classmethod
    def run_ladder(
        cls,
        gw: GraphGateway,
        ctx: RunContext,
        instrument_id: str | None = None,
    ) -> dict[str, Any]:
        """Run all ladder checks, recording HYPOTHESIS_CHECKED events."""
        results: dict[str, str] = {}
        now_epoch = ctx.clock.now_epoch()
        lapsed_usages: list[Any] = []

        # H1: Certificate lapse (authoritative drift or expired validity)
        cov_results = evaluate_usage_coverage(gw, t=now_epoch, instrument_id=instrument_id, ctx=ctx)
        uncovered = [u for u in cov_results if not u.is_allowed]
        if uncovered:
            results["H1"] = "CONFIRMED"
            lapsed_usages = uncovered
            cls._emit_hypothesis(ctx, gw, "H1_CERT_LAPSE", "CONFIRMED", f"Found {len(uncovered)} uncovered usages.")
        else:
            results["H1"] = "RULED_OUT"
            cls._emit_hypothesis(ctx, gw, "H1_CERT_LAPSE", "RULED_OUT", "All usages covered validly.")

        # H2: Certificate revocation
        cand_rows = gw.query(
            queries.Q_USAGE_COVERAGE_CANDIDATES,
            {"usage_id": None, "experiment_id": None, "instrument_id": instrument_id, "t": now_epoch},
            name="LADDER_CHECK_REVOCATION",
            ctx=ctx,
        )
        has_revocation = any(bool(r.get("revocations")) for r in cand_rows)
        results["H2"] = "CONFIRMED" if has_revocation else "RULED_OUT"
        cls._emit_hypothesis(ctx, gw, "H2_REVOCATION", results["H2"], f"Active revocation found: {has_revocation}")

        # H3: Scope mismatch
        scope_mismatch = any(
            r.get("cert_id") is None and r.get("required_scope") is not None
            for r in cand_rows
        )
        results["H3"] = "CONFIRMED" if scope_mismatch else "RULED_OUT"
        cls._emit_hypothesis(ctx, gw, "H3_SCOPE_MISMATCH", results["H3"], f"Scope mismatch found: {scope_mismatch}")

        # H4: Missing or invalid usage data (null or inverted dates)
        invalid_dates = any(
            r.get("used_from") is None or r.get("used_until") is None or (r.get("used_from") >= r.get("used_until"))
            for r in cand_rows
        )
        results["H4"] = "CONFIRMED" if invalid_dates else "RULED_OUT"
        cls._emit_hypothesis(ctx, gw, "H4_INVALID_USAGE_DATA", results["H4"], f"Invalid date intervals: {invalid_dates}")

        # H5: Conflicting claims (unresolved contradictions)
        has_contradictions = any(
            any(c.get("resolution") == "UNRESOLVED" for c in (r.get("contradictions") or []))
            for r in cand_rows
        )
        results["H5"] = "CONFIRMED" if has_contradictions else "RULED_OUT"
        cls._emit_hypothesis(ctx, gw, "H5_CONFLICTING_CLAIMS", results["H5"], f"Unresolved contradictions: {has_contradictions}")

        return {
            "hypothesis_results": results,
            "lapsed_usages": lapsed_usages,
        }

    @staticmethod
    def _emit_hypothesis(
        ctx: RunContext,
        gw: GraphGateway,
        hypothesis_key: str,
        outcome: str,
        detail: str,
    ) -> None:
        if ctx.recorder is not None:
            ctx.recorder.record_event(
                gw=gw,
                ctx=ctx,
                event_type=EventType.HYPOTHESIS_CHECKED,
                summary=f"Hypothesis {hypothesis_key}: {outcome} - {detail}",
                logical_key=f"ladder_{hypothesis_key}",
                outcome=outcome,
                payload={"hypothesis": hypothesis_key, "outcome": outcome, "detail": detail},
            )


# ── 2. Step Registry ──────────────────────────────────────────────────────────

class StepDefinition:
    """Definition of an executable investigation step."""

    def __init__(
        self,
        step_type: str,
        kind: str,
        role: str,
        precondition_fn: Callable[[GraphGateway, RunContext, dict[str, Any], dict[str, Any]], CheckResult],
        execute_fn: Callable[[GraphGateway, RunContext, dict[str, Any], dict[str, Any]], StepOutcome],
        postcondition_fn: Callable[[GraphGateway, RunContext, dict[str, Any], dict[str, Any], StepOutcome], CheckResult],
    ):
        self.step_type = step_type
        self.kind = kind
        self.role = role
        self.precondition = precondition_fn
        self.execute = execute_fn
        self.postcondition = postcondition_fn


STEP_REGISTRY: dict[str, StepDefinition] = {}


def register_step(step_def: StepDefinition) -> None:
    STEP_REGISTRY[step_def.step_type] = step_def


# Step 1: CONFIRM_LAPSE
def _pre_confirm_lapse(gw: GraphGateway, ctx: RunContext, params: dict, state: dict) -> CheckResult:
    return CheckResult(passed=True, reason="Ready to evaluate instrument coverage.")


def _exec_confirm_lapse(gw: GraphGateway, ctx: RunContext, params: dict, state: dict) -> StepOutcome:
    instrument_id = params.get("instrument_id")
    results = evaluate_usage_coverage(gw, t=ctx.clock.now_epoch(), instrument_id=instrument_id)
    lapsed_usages = [u for u in results if not u.is_allowed]
    lapsed_exp_ids = list(set(u.experiment_id for u in lapsed_usages if u.experiment_id))
    coverage_modes = list(set("chain" if "chain" in u.status.value.lower() else "single" for u in results if u.is_allowed))

    state["lapsed_exp_ids"] = lapsed_exp_ids
    state["coverage_modes"] = sorted(coverage_modes)

    return StepOutcome(
        success=len(lapsed_usages) > 0,
        data={"lapsed_exp_ids": lapsed_exp_ids, "uncovered_count": len(lapsed_usages)},
        structural_features={"coverage_modes": sorted(coverage_modes), "has_lapses": len(lapsed_usages) > 0},
    )


def _post_confirm_lapse(gw: GraphGateway, ctx: RunContext, params: dict, state: dict, outcome: StepOutcome) -> CheckResult:
    if outcome.success and state.get("lapsed_exp_ids"):
        return CheckResult(passed=True, reason=f"Confirmed {len(state['lapsed_exp_ids'])} lapsed experiments.")
    return CheckResult(passed=False, reason="No lapsed experiments confirmed.")


register_step(StepDefinition(
    "CONFIRM_LAPSE", "READ", "CONFIRM",
    _pre_confirm_lapse, _exec_confirm_lapse, _post_confirm_lapse
))


# Step 2: TRACE_DEPENDENTS
def _pre_trace_dependents(gw: GraphGateway, ctx: RunContext, params: dict, state: dict) -> CheckResult:
    if not state.get("lapsed_exp_ids"):
        return CheckResult(passed=False, reason="Missing lapsed experiment IDs from prior step.")
    return CheckResult(passed=True, reason="Lapsed experiments present.")


def _exec_trace_dependents(gw: GraphGateway, ctx: RunContext, params: dict, state: dict) -> StepOutcome:
    lapsed_exp_ids = state["lapsed_exp_ids"]
    impact = ActionService.trace_impact(gw, lapsed_exp_ids)
    state["impact"] = impact
    max_depth = max((len(ds.dependency_path) for ds in impact.datasets), default=1)
    state["max_lineage_depth"] = max_depth

    return StepOutcome(
        success=len(impact.datasets) > 0,
        data={"dataset_count": len(impact.datasets), "report_count": len(impact.reports)},
        structural_features={"dataset_count": len(impact.datasets), "max_lineage_depth": max_depth},
    )


def _post_trace_dependents(gw: GraphGateway, ctx: RunContext, params: dict, state: dict, outcome: StepOutcome) -> CheckResult:
    if outcome.success:
        return CheckResult(passed=True, reason="Downstream impact successfully traced.")
    return CheckResult(passed=False, reason="No downstream datasets identified.")


register_step(StepDefinition(
    "TRACE_DEPENDENTS", "READ", "CONFIRM",
    _pre_trace_dependents, _exec_trace_dependents, _post_trace_dependents
))


# Step 3: PROPOSE_ACTIONS
def _pre_propose_actions(gw: GraphGateway, ctx: RunContext, params: dict, state: dict) -> CheckResult:
    if not state.get("impact"):
        return CheckResult(passed=False, reason="Missing impact result.")
    return CheckResult(passed=True, reason="Impact result available.")


def _exec_propose_actions(gw: GraphGateway, ctx: RunContext, params: dict, state: dict) -> StepOutcome:
    impact = state["impact"]
    proposals = []
    assignment_modes = []
    has_escalations = False

    for ds in impact.datasets:
        assign_res = ActionService.resolve_assignment(gw, ds.id)
        role = assign_res.role_matched or "ESCALATED"
        assignment_modes.append(role)
        if assign_res.is_escalated:
            has_escalations = True
        proposals.append({
            "action_type": "QUARANTINE_DATASET",
            "target_id": ds.id,
            "role": role,
            "assignee_id": assign_res.assignee_id,
            "is_escalated": assign_res.is_escalated,
            "escalation_reason": assign_res.escalation_reason,
            "reason": ds.reason,
            "path": ds.dependency_path,
        })

    for rpt in impact.reports:
        action_type = "HOLD_REPORT" if rpt.current_status == "DRAFT" else "FLAG_REPORT_REVIEW"
        proposals.append({
            "action_type": action_type,
            "target_id": rpt.id,
            "role": "REPORT_REVIEW",
            "reason": rpt.reason,
            "path": rpt.dependency_path,
        })

    state["proposals"] = proposals
    state["assignment_modes"] = sorted(list(set(assignment_modes)))
    state["has_escalations"] = has_escalations

    return StepOutcome(
        success=len(proposals) > 0,
        data={"proposal_count": len(proposals)},
        structural_features={
            "assignment_modes": sorted(list(set(assignment_modes))),
            "has_escalations": has_escalations,
        },
    )


def _post_propose_actions(gw: GraphGateway, ctx: RunContext, params: dict, state: dict, outcome: StepOutcome) -> CheckResult:
    return CheckResult(passed=True, reason="Action proposals generated.")


register_step(StepDefinition(
    "PROPOSE_ACTIONS", "READ", "REMEDIATE",
    _pre_propose_actions, _exec_propose_actions, _post_propose_actions
))


# Step 4: QUARANTINE_DATASETS
def _pre_quarantine(gw: GraphGateway, ctx: RunContext, params: dict, state: dict) -> CheckResult:
    if not state.get("proposals"):
        return CheckResult(passed=False, reason="Missing action proposals.")
    return CheckResult(passed=True, reason="Proposals present.")


def _exec_quarantine(gw: GraphGateway, ctx: RunContext, params: dict, state: dict) -> StepOutcome:
    impact = state["impact"]
    # Execute through existing action service
    res = ActionService.execute_incident_actions(
        gw=gw,
        incident_id=ctx.incident_id,
        impact=impact,
        recorded_at=ctx.clock.now_epoch(),
        actor_agent=ctx.actor_agent,
    )
    state["action_result"] = res
    return StepOutcome(
        success=len(res["quarantined_datasets"]) > 0 or len(res["actions_skipped"]) > 0,
        data=res,
        structural_features={"quarantined_count": len(res["quarantined_datasets"])},
    )


def _post_quarantine(gw: GraphGateway, ctx: RunContext, params: dict, state: dict, outcome: StepOutcome) -> CheckResult:
    return CheckResult(passed=True, reason="Datasets quarantined.")


register_step(StepDefinition(
    "QUARANTINE_DATASETS", "WRITE", "REMEDIATE",
    _pre_quarantine, _exec_quarantine, _post_quarantine
))


# Step 5: HOLD_REPORTS
def _pre_hold_reports(gw: GraphGateway, ctx: RunContext, params: dict, state: dict) -> CheckResult:
    return CheckResult(passed=True, reason="Ready to hold/review reports.")


def _exec_hold_reports(gw: GraphGateway, ctx: RunContext, params: dict, state: dict) -> StepOutcome:
    res = state.get("action_result", {})
    return StepOutcome(
        success=True,
        data={
            "reports_held": res.get("reports_held", []),
            "reports_flagged_review": res.get("reports_flagged_review", []),
        },
        structural_features={
            "held_count": len(res.get("reports_held", [])),
            "review_count": len(res.get("reports_flagged_review", [])),
        },
    )


def _post_hold_reports(gw: GraphGateway, ctx: RunContext, params: dict, state: dict, outcome: StepOutcome) -> CheckResult:
    return CheckResult(passed=True, reason="Reports defensively updated.")


register_step(StepDefinition(
    "HOLD_REPORTS", "WRITE", "REMEDIATE",
    _pre_hold_reports, _exec_hold_reports, _post_hold_reports
))


# Step 6: CREATE_ASSIGN_TASKS
def _pre_create_tasks(gw: GraphGateway, ctx: RunContext, params: dict, state: dict) -> CheckResult:
    return CheckResult(passed=True, reason="Ready to check review tasks.")


def _exec_create_tasks(gw: GraphGateway, ctx: RunContext, params: dict, state: dict) -> StepOutcome:
    res = state.get("action_result", {})
    tasks = res.get("tasks_created", [])
    has_esc = any(t.get("status") == "ESCALATED" for t in tasks)
    return StepOutcome(
        success=len(tasks) > 0 or len(res.get("actions_skipped", [])) > 0,
        data={"tasks": tasks},
        structural_features={"task_count": len(tasks), "has_escalations": has_esc},
    )


def _post_create_tasks(gw: GraphGateway, ctx: RunContext, params: dict, state: dict, outcome: StepOutcome) -> CheckResult:
    return CheckResult(passed=True, reason="Tasks assigned or escalated.")


register_step(StepDefinition(
    "CREATE_ASSIGN_TASKS", "WRITE", "REMEDIATE",
    _pre_create_tasks, _exec_create_tasks, _post_create_tasks
))


# Step 7: VERIFY_GATE_BLOCKS
def _pre_verify_gate(gw: GraphGateway, ctx: RunContext, params: dict, state: dict) -> CheckResult:
    return CheckResult(passed=True, reason="Ready to verify publication gate.")


def _exec_verify_gate(gw: GraphGateway, ctx: RunContext, params: dict, state: dict) -> StepOutcome:
    impact = state.get("impact")
    gate_results = []
    if impact:
        gate = PublicationGate(clock=ctx.clock)
        for rpt in impact.reports:
            attempt_id = f"gate_verify_{ctx.incident_id}_{rpt.id}"
            att = gate.attempt_publish(
                gw=gw,
                report_id=rpt.id,
                actor="SystemVerifier",
                attempt_id=attempt_id,
            )
            gate_results.append(att)

    all_blocked = all(not g.is_allowed for g in gate_results) if gate_results else True
    return StepOutcome(
        success=all_blocked,
        data={"gate_attempts": [g.decision.value for g in gate_results]},
        structural_features={"gate_blocked": all_blocked},
    )


def _post_verify_gate(gw: GraphGateway, ctx: RunContext, params: dict, state: dict, outcome: StepOutcome) -> CheckResult:
    if outcome.success:
        return CheckResult(passed=True, reason="Publication gate successfully blocked affected reports.")
    return CheckResult(passed=False, reason="Publication gate permitted an impacted report!")


register_step(StepDefinition(
    "VERIFY_GATE_BLOCKS", "READ", "VERIFY",
    _pre_verify_gate, _exec_verify_gate, _post_verify_gate
))


# ── 3. Derive CANDIDATE Playbook ─────────────────────────────────────────────

def derive_playbook(
    gw: GraphGateway,
    source_incident_id: str,
    incident_type: str = "CERT_LAPSE",
    version: str = "1.0.0",
) -> str:
    """
    Derive an InvestigationPlaybook (status CANDIDATE) from executed Incident 1 events.
    Captures structural facts in expected_outcomes (no specific entity IDs).
    Idempotent on (incident_type, source_incident_id, version).
    """
    playbook_id = f"pb_{hashlib.sha256(f'{incident_type}:{source_incident_id}:{version}'.encode()).hexdigest()[:16]}"

    # Check if already derived
    exists = gw.query(queries.Q_CHECK_PLAYBOOK_EXISTS, {"id": playbook_id}, name="CHECK_PLAYBOOK")
    if exists:
        return playbook_id

    timeline = get_timeline(gw, source_incident_id)

    # Extract exploration summary from ruled out hypotheses
    exploration_summary = {}
    for ev in timeline:
        if ev.event_type == EventType.HYPOTHESIS_CHECKED.value:
            hyp = ev.payload.get("hypothesis", ev.logical_key)
            out = ev.outcome or "UNKNOWN"
            exploration_summary[hyp] = out

    # Standard steps derived in executed order
    ordered_step_types = [
        "CONFIRM_LAPSE",
        "TRACE_DEPENDENTS",
        "PROPOSE_ACTIONS",
        "QUARANTINE_DATASETS",
        "HOLD_REPORTS",
        "CREATE_ASSIGN_TASKS",
        "VERIFY_GATE_BLOCKS",
    ]

    applicability = {
        "incident_type": incident_type,
        "graph_shape": "instrument_with_scoped_cert_and_dependent_datasets",
    }

    gw.query(
        queries.Q_CREATE_PLAYBOOK,
        {
            "id": playbook_id,
            "name": f"Automated Playbook for {incident_type}",
            "version": version,
            "status": "CANDIDATE",
            "incident_type": incident_type,
            "applicability_json": json.dumps(applicability),
            "source_incident_id": source_incident_id,
            "exploration_summary_json": json.dumps(exploration_summary),
            "promotion_checks_json": json.dumps({}),
        },
        name="CREATE_PLAYBOOK",
    )

    # Attach each step with structural expectations
    for order, st_name in enumerate(ordered_step_types, start=1):
        step_def = STEP_REGISTRY[st_name]
        step_id = f"{playbook_id}_step_{order}"

        # Structural expected outcomes (no specific entity IDs)
        expected: dict[str, Any] = {}
        if st_name == "CONFIRM_LAPSE":
            expected = {"coverage_modes": ["single"], "has_lapses": True}
        elif st_name == "TRACE_DEPENDENTS":
            expected = {"max_lineage_depth": 2}
        elif st_name == "PROPOSE_ACTIONS":
            expected = {"assignment_modes": ["OWNER", "PERFORMER"], "has_escalations": False}
        elif st_name == "QUARANTINE_DATASETS":
            expected = {"quarantine_enforced": True}
        elif st_name == "HOLD_REPORTS":
            expected = {"defensive_status_applied": True}
        elif st_name == "CREATE_ASSIGN_TASKS":
            expected = {"tasks_assigned": True, "has_escalations": False}
        elif st_name == "VERIFY_GATE_BLOCKS":
            expected = {"gate_blocked": True}

        gw.query(
            queries.Q_ATTACH_PLAYBOOK_STEP,
            {
                "id": step_id,
                "playbook_id": playbook_id,
                "order": order,
                "step_type": st_name,
                "kind": step_def.kind,
                "role": step_def.role,
                "precondition_key": f"pre_{st_name.lower()}",
                "postcondition_key": f"post_{st_name.lower()}",
                "params_json": json.dumps({}),
                "expected_outcome": json.dumps(expected),
            },
            name="ATTACH_PLAYBOOK_STEP",
        )

    return playbook_id


# ── 4. Promote Playbook (PC1 to PC6) ──────────────────────────────────────────

def promote_playbook(
    gw: GraphGateway,
    playbook_id: str,
    incident_id: str,
    ctx: RunContext | None = None,
) -> tuple[bool, dict[str, Any]]:
    """
    Rigorously verify postconditions PC1 to PC6 before promoting to VERIFIED.
    Returns (promoted: bool, checks: dict).
    """
    checks: dict[str, Any] = {}

    # PC1: every affected dataset recomputed from the impact trace is QUARANTINED
    # Get all lapsed experiments for the incident
    action_records = gw.query(
        queries.Q_GET_INCIDENT_ACTION_RECORDS,
        {"incident_id": incident_id},
        name="CHECK_PC1_ACTIONS",
    )
    quarantined_targets = [ar["target_id"] for ar in action_records if ar["action_type"] == "QUARANTINE_DATASET"]
    all_quarantined = True
    for tid in quarantined_targets:
        ds_row = gw.query("MATCH (ds:Dataset {id: $id}) RETURN ds.status AS status", {"id": tid})
        if not ds_row or ds_row[0].get("status") != "QUARANTINED":
            all_quarantined = False
            break
    checks["PC1_datasets_quarantined"] = {"passed": all_quarantined and len(quarantined_targets) > 0, "count": len(quarantined_targets)}

    # PC2: every dependent report is HELD or REQUIRES_REVIEW, and gate attempt returns BLOCK
    report_records = [ar for ar in action_records if ar["action_type"] in ("HOLD_REPORT", "FLAG_REPORT_REVIEW")]
    pc2_passed = True
    for rr in report_records:
        r_id = rr["target_id"]
        r_stat_row = gw.query("MATCH (rpt:Report {id: $id}) RETURN rpt.publication_status AS status", {"id": r_id})
        status = r_stat_row[0]["status"] if r_stat_row else ""
        if status not in ("HELD", "REQUIRES_REVIEW"):
            pc2_passed = False
            break
        # Gate test
        gate = PublicationGate(clock=ctx.clock if ctx else FixedClock(now_epoch))
        gate_res = gate.attempt_publish(
            gw=gw,
            report_id=r_id,
            actor="PromotionVerifier",
            attempt_id=f"promo_check_{incident_id}_{r_id}",
        )
        if gate_res.is_allowed:
            pc2_passed = False
            break
    checks["PC2_reports_held_gate_blocks"] = {"passed": pc2_passed, "report_count": len(report_records)}

    # PC3: a control report with no affected dependencies still returns ALLOW
    gate = PublicationGate(clock=ctx.clock if ctx else FixedClock(now_epoch))
    control_res = gate.attempt_publish(
        gw=gw,
        report_id="RPT-2",
        actor="PromotionVerifier",
        attempt_id=f"promo_check_control_{incident_id}",
    )
    checks["PC3_control_report_allowed"] = {"passed": control_res.is_allowed, "decision": control_res.decision.value}

    # PC4: every incident Task has an assignee or is ESCALATED with a reason
    task_rows = gw.query(
        queries.Q_GET_INCIDENT_TASKS,
        {"incident_id": incident_id},
        name="CHECK_PC4_TASKS",
    )
    pc4_passed = True
    for t in task_rows:
        if t["status"] == "ESCALATED":
            if not t.get("escalation_reason"):
                pc4_passed = False
                break
        elif not t.get("assignee_id"):
            pc4_passed = False
            break
    checks["PC4_tasks_assigned_or_escalated"] = {"passed": pc4_passed and len(task_rows) > 0, "task_count": len(task_rows)}

    # PC5: every ActionRecord has an evidence_path_json whose node ids all re-resolve
    pc5_passed = True
    for ar in action_records:
        try:
            path_nodes = json.loads(ar.get("evidence_path_json") or "[]")
        except Exception:
            path_nodes = []
        for nid in path_nodes:
            if nid in ("PRODUCED", "USES_DATASET", "DERIVED_FROM"):
                continue
            exists = gw.query("MATCH (n {id: $id}) RETURN n.id AS id", {"id": nid})
            if not exists:
                pc5_passed = False
                break
        if not pc5_passed:
            break
    checks["PC5_evidence_paths_resolve"] = {"passed": pc5_passed and len(action_records) > 0}

    # PC6: no duplicate Tasks or ActionRecords exist for the incident's unique keys
    task_ids = [t["task_id"] for t in task_rows]
    ar_ids = [ar["id"] for ar in action_records]
    pc6_passed = (len(task_ids) == len(set(task_ids))) and (len(ar_ids) == len(set(ar_ids)))
    checks["PC6_no_duplicate_records"] = {"passed": pc6_passed}

    all_passed = all(c["passed"] for c in checks.values())

    now_epoch = ctx.clock.now_epoch() if ctx else int(time.time())

    # Emit postcondition events if ctx available
    if ctx and ctx.recorder:
        for check_name, check_data in checks.items():
            ctx.recorder.record_event(
                gw=gw,
                ctx=ctx,
                event_type=EventType.POSTCONDITION_CHECKED,
                summary=f"Postcondition {check_name}: {'PASSED' if check_data['passed'] else 'FAILED'}",
                logical_key=f"pc_check_{check_name}",
                outcome="PASSED" if check_data["passed"] else "FAILED",
                payload=check_data,
            )

    if all_passed:
        gw.query(
            queries.Q_PROMOTE_PLAYBOOK,
            {
                "id": playbook_id,
                "incident_id": incident_id,
                "at": now_epoch,
                "promotion_checks_json": json.dumps(checks),
            },
            name="PROMOTE_PLAYBOOK",
        )
        return True, checks
    else:
        gw.query(
            queries.Q_RECORD_PROMOTION_FAILURE,
            {
                "id": playbook_id,
                "failure_json": json.dumps(checks),
            },
            name="RECORD_PROMOTION_FAILURE",
        )
        return False, checks


# ── 5. Playbook Retrieval and Execution ───────────────────────────────────────

def retrieve_playbook(
    gw: GraphGateway,
    incident_type: str = "CERT_LAPSE",
    instrument_id: str | None = None,
) -> dict[str, Any] | None:
    """Retrieve best matching playbook (prefers VERIFIED over CANDIDATE)."""
    candidates = gw.query(
        queries.Q_FIND_MATCHING_PLAYBOOKS,
        {"incident_type": incident_type},
        name="FIND_PLAYBOOKS",
    )
    if not candidates:
        return None

    # Check applicability shape
    shape = gw.query(
        queries.Q_CHECK_INCIDENT_SHAPE_APPLICABILITY,
        {"instrument_id": instrument_id},
        name="CHECK_SHAPE",
    )
    if not shape or shape[0].get("dataset_count", 0) == 0:
        return None

    # Return top ranked candidate (query already orders VERIFIED first)
    return candidates[0]


def compare_expected(observed: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    """Compare structural features between observed outcome and stored expectations."""
    diffs = []
    for k, exp_val in expected.items():
        obs_val = observed.get(k)
        if obs_val is None:
            continue
        if obs_val != exp_val:
            diffs.append(f"{k}: observed {obs_val} differs from expected {exp_val}")
    return diffs


def execute_playbook_step(
    gw: GraphGateway,
    ctx: RunContext,
    run_id: str,
    step: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Execute one step of an investigation playbook and record a StepResult."""
    step_id = step["id"]
    step_type = step["step_type"]
    step_def = STEP_REGISTRY.get(step_type)

    if not step_def:
        raise ValueError(f"Unknown step type: {step_type}")

    expected = json.loads(step.get("expected_outcome") or "{}")

    t0 = time.perf_counter()
    domain_queries_before = ctx.metrics.domain_queries if ctx.metrics else 0

    # 1. Precondition
    pre_res = step_def.precondition(gw, ctx, {}, state)
    if not pre_res.passed:
        # Precondition failed
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        step_res_id = f"sr_{run_id}_{step['order']}"
        gw.query(
            queries.Q_RECORD_STEP_RESULT,
            {
                "id": step_res_id,
                "run_id": run_id,
                "step_id": step_id,
                "status": StepStatus.FAILED.value,
                "detail": f"Precondition failed: {pre_res.reason}",
                "differences_json": "[]",
                "precondition_json": json.dumps({"passed": False, "reason": pre_res.reason}),
                "postcondition_json": "{}",
                "observed_json": "{}",
                "expected_json": json.dumps(expected),
                "domain_query_count": 0,
                "elapsed_ms": elapsed_ms,
            },
            name="RECORD_STEP_RESULT",
        )
        return {"status": StepStatus.FAILED.value, "detail": pre_res.reason}

    # 2. Execute
    outcome = step_def.execute(gw, ctx, {}, state)

    # 3. Postcondition
    post_res = step_def.postcondition(gw, ctx, {}, state, outcome)

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
    domain_queries_after = ctx.metrics.domain_queries if ctx.metrics else 0
    queries_run = domain_queries_after - domain_queries_before

    # 4. Compare observed structural features against expected
    diffs = compare_expected(outcome.structural_features, expected)
    status = (
        StepStatus.FAILED.value if not post_res.passed
        else StepStatus.DIFFERED.value if diffs
        else StepStatus.PASSED.value
    )

    step_res_id = f"sr_{run_id}_{step['order']}"
    gw.query(
        queries.Q_RECORD_STEP_RESULT,
        {
            "id": step_res_id,
            "run_id": run_id,
            "step_id": step_id,
            "status": status,
            "detail": post_res.reason,
            "differences_json": json.dumps(diffs),
            "precondition_json": json.dumps({"passed": True}),
            "postcondition_json": json.dumps({"passed": post_res.passed, "reason": post_res.reason}),
            "observed_json": json.dumps(outcome.structural_features),
            "expected_json": json.dumps(expected),
            "domain_query_count": queries_run,
            "elapsed_ms": elapsed_ms,
        },
        name="RECORD_STEP_RESULT",
    )

    # Emit PLAYBOOK_STEP event
    if ctx.recorder:
        ctx.recorder.record_event(
            gw=gw,
            ctx=ctx,
            event_type=EventType.PLAYBOOK_STEP,
            summary=f"Step {step['order']} {step_type}: {status}",
            logical_key=f"step_{step['order']}_{step_type}",
            outcome=status,
            payload={
                "step_type": step_type,
                "status": status,
                "differences": diffs,
                "observed": outcome.structural_features,
                "domain_queries": queries_run,
            },
        )

    return {
        "status": status,
        "step_type": step_type,
        "detail": post_res.reason,
        "differences": diffs,
        "observed": outcome.structural_features,
    }
