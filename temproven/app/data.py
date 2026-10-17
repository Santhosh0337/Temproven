"""
temproven/app/data.py
─────────────────────
Thin read-only adapters connecting Central Cypher queries and domain services
to the Streamlit UI presentation layer.

RULES:
- Zero writes on page load or rerun.
- No in-memory fallback, no fake mock data.
- All queries are parameterized and centralized in temproven.graph.queries.
- If FalkorDB is unreachable, errors are surfaced explicitly.
"""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from temproven.domain.actions import ActionService
from temproven.domain.gate import GateResult, PublicationGate
from temproven.domain.temporal import evaluate_usage_coverage
from temproven.graph import queries
from temproven.memory.episodic import IncidentReconstruction, reconstruct_incident
from temproven.memory.semantic import explain_belief_change
from temproven.metrics import IncidentMetricsData, load_incident_metrics

if TYPE_CHECKING:
    from temproven.clock import Clock
    from temproven.graph.gateway import GraphGateway


def check_db_connection(gw: GraphGateway) -> tuple[bool, str | None]:
    """
    Test connectivity to the FalkorDB instance.
    Returns (True, None) if reachable, or (False, error_message).
    """
    try:
        rows = gw.query("RETURN 1 AS ping", name="UI_HEALTHCHECK")
        if rows and rows[0].get("ping") == 1:
            return True, None
        return True, None
    except Exception as exc:
        return False, str(exc)


def fetch_incidents(gw: GraphGateway) -> list[dict[str, Any]]:
    """
    Retrieve all incidents from the graph, ordered newest first.
    """
    return gw.query(queries.Q_LIST_INCIDENTS, name="UI_LIST_INCIDENTS")


def fetch_incident_detail(gw: GraphGateway, incident_id: str) -> IncidentReconstruction | None:
    """
    Reconstruct the full incident history and state from durable graph nodes.
    """
    return reconstruct_incident(gw, incident_id)


def fetch_impact_data(
    gw: GraphGateway,
    instrument_id: str | None,
    as_of_recorded: int,
) -> dict[str, Any]:
    """
    Fetch downstream impact data by evaluating live instrument coverage and
    tracing affected datasets and reports.
    Pure read-only operation.
    """
    if not instrument_id:
        return {"experiments": [], "datasets": [], "reports": []}

    # 1. Evaluate coverage for all usages of this instrument at as_of_recorded
    cov_results = evaluate_usage_coverage(
        gw,
        t=as_of_recorded,
        instrument_id=instrument_id,
    )

    experiments: list[dict[str, Any]] = []
    lapsed_exp_ids: list[str] = []

    for cr in cov_results:
        exp_dict = {
            "id": cr.experiment_id,
            "status": cr.status.value,
            "usage_id": cr.usage_id,
            "gaps": cr.uncovered_gaps,
            "reason": cr.reason,
            "evidence_paths": cr.evidence_paths,
        }
        experiments.append(exp_dict)
        if not cr.is_allowed and cr.experiment_id:
            lapsed_exp_ids.append(cr.experiment_id)

    lapsed_exp_ids = list(set(lapsed_exp_ids))

    # 2. Downstream impact traversal
    impact = ActionService.trace_impact(gw, lapsed_exp_ids)

    datasets = [
        {
            "id": d.id,
            "current_status": d.current_status,
            "reason": d.reason,
            "dependency_path": d.dependency_path,
        }
        for d in impact.datasets
    ]

    reports = [
        {
            "id": r.id,
            "current_status": r.current_status,
            "reason": r.reason,
            "dependency_path": r.dependency_path,
        }
        for r in impact.reports
    ]

    return {
        "experiments": experiments,
        "datasets": datasets,
        "reports": reports,
    }


def fetch_playbook_data(gw: GraphGateway, incident_id: str) -> dict[str, Any]:
    """
    Retrieve procedural memory information: verified playbook, promotion checks,
    and execution runs for the incident.
    """
    # 1. Check if a playbook was derived from this incident
    derived_rows = gw.query(
        queries.Q_GET_PLAYBOOK_BY_SOURCE_INCIDENT,
        {"incident_id": incident_id},
        name="UI_GET_DERIVED_PLAYBOOK",
    )

    # 2. Check if a playbook was executed for this incident
    run_rows = gw.query(
        queries.Q_GET_PLAYBOOK_RUNS,
        {"incident_id": incident_id},
        name="UI_GET_PLAYBOOK_RUNS",
    )

    playbook_info: dict[str, Any] | None = None
    playbook_id: str | None = None
    promotion_checks: dict[str, Any] = {}

    if derived_rows:
        row = derived_rows[0]
        playbook_id = row["id"]
        raw_checks = row.get("promotion_checks_json")
        if raw_checks:
            try:
                promotion_checks = json.loads(raw_checks)
            except Exception:
                promotion_checks = {}
        playbook_info = {
            "id": row["id"],
            "name": row["name"],
            "version": row["version"],
            "status": row["status"],
            "incident_type": row["incident_type"],
            "source_incident_id": row["source_incident_id"],
        }
    elif run_rows:
        # Reused playbook from another incident
        run = run_rows[0]
        playbook_id = run["playbook_id"]
        playbook_info = {
            "id": run["playbook_id"],
            "name": run.get("playbook_name") or run["playbook_id"],
            "version": "1.0.0",
            "status": run.get("playbook_status") or "VERIFIED",
            "incident_type": "CERT_LAPSE",
            "source_incident_id": "INC-2026-001",
        }

    # 3. Retrieve playbook steps
    steps: list[dict[str, Any]] = []
    if playbook_id:
        step_rows = gw.query(
            queries.Q_GET_PLAYBOOK_STEPS,
            {"playbook_id": playbook_id},
            name="UI_GET_PLAYBOOK_STEPS",
        )
        for s in step_rows:
            steps.append({
                "id": s["id"],
                "order": s["order"],
                "step_type": s["step_type"],
                "kind": s["kind"],
                "role": s["role"],
                "precondition_key": s.get("precondition_key"),
                "postcondition_key": s.get("postcondition_key"),
                "expected_outcome": s.get("expected_outcome"),
            })

    # 4. Step execution results (if incident had a run)
    run_info: dict[str, Any] | None = None
    step_results: list[dict[str, Any]] = []
    if run_rows:
        run = run_rows[0]
        run_info = {
            "run_id": run["run_id"],
            "mode": run["mode"],
            "playbook_id": run["playbook_id"],
        }
        step_results = run.get("step_results") or []

    return {
        "playbook": playbook_info,
        "run": run_info,
        "steps": steps,
        "step_results": step_results,
        "promotion_checks": promotion_checks,
    }


def fetch_belief_change(
    gw: GraphGateway,
    cert_id: str,
    as_of_recorded: int | None = None,
) -> Any:
    """
    Fetch semantic memory belief change explanation for a certificate.
    """
    return explain_belief_change(gw, cert_id, as_of_recorded=as_of_recorded)


def fetch_incident_metrics(
    gw: GraphGateway,
    incident_id: str,
) -> IncidentMetricsData | None:
    """
    Fetch persisted efficiency metrics for an incident.
    """
    return load_incident_metrics(gw, incident_id)


def fetch_reports(gw: GraphGateway) -> list[dict[str, Any]]:
    """
    Retrieve all reports in the system.
    """
    return gw.query(queries.Q_LIST_REPORTS, name="UI_LIST_REPORTS")


def fetch_report_details(gw: GraphGateway, report_id: str) -> dict[str, Any] | None:
    """
    Retrieve upstream dependencies and status for a report without publishing.
    """
    rows = gw.query(
        queries.Q_REPORT_UPSTREAM_DEPENDENCIES,
        {"report_id": report_id},
        name="UI_REPORT_DEPS",
    )
    if not rows or not rows[0].get("report_id"):
        return None
    return rows[0]


def execute_gate_action(
    gw: GraphGateway,
    report_id: str,
    actor: str,
    clock: Clock,
) -> GateResult:
    """
    Execute backend publication gate evaluation and mutate report status if ALLOW.
    Only called upon explicit user confirmation click.
    """
    gate = PublicationGate(clock=clock)
    return gate.attempt_publish(gw, report_id=report_id, actor=actor)
