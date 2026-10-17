"""
temproven/domain/actions.py
───────────────────────────
Enforces consequential workflow actions triggered by temporal graph reasoning.

POLICIES:
- Quarantine eligible datasets: ACTIVE -> QUARANTINED.
- Reports: DRAFT -> HELD; PUBLISHED -> REQUIRES_REVIEW.
  Never claims an external publication was recalled or unpublished.
- Tasks: one review Task per affected dataset (and per published report).
  Assignment rule: Owner -> Performer -> Custodian -> ESCALATED with reason.
- ActionRecord: written for every action with target, prior/new state, reason,
  evidence path, timestamp, acting agent, and rule_version.
- Idempotency: deterministic action IDs; reprocessing returns 'already applied'
  without duplicating tasks or state mutations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from temproven.graph import queries

if TYPE_CHECKING:
    from temproven.graph.gateway import GraphGateway


RULE_VERSION = "1.0.0"


@dataclass
class ImpactedNode:
    id: str
    label: str  # 'Dataset' or 'Report'
    current_status: str
    reason: str
    dependency_path: list[str] = field(default_factory=list)


@dataclass
class ImpactResult:
    lapsed_experiment_ids: list[str]
    datasets: list[ImpactedNode] = field(default_factory=list)
    reports: list[ImpactedNode] = field(default_factory=list)


@dataclass
class AssignmentResult:
    dataset_id: str
    assignee_id: str | None
    assignee_name: str | None
    role_matched: str | None  # 'OWNER', 'PERFORMER', 'CUSTODIAN', or None
    is_escalated: bool
    escalation_reason: str | None


class ActionService:
    """Service handling impact traversal, assignment, quarantine, and audit logging."""

    @staticmethod
    def trace_impact(gw: GraphGateway, lapsed_experiment_ids: list[str], ctx: Any | None = None) -> ImpactResult:
        """
        Follow PRODUCED, then DERIVED_FROM*0..6 downward, then USES_DATASET to reports.
        Returns inspectable paths and reason for each affected node.
        """
        impacted_datasets: dict[str, ImpactedNode] = {}
        impacted_reports: dict[str, ImpactedNode] = {}

        for exp_id in lapsed_experiment_ids:
            rows = gw.query(queries.Q_IMPACT_TRAVERSAL, {"exp_id": exp_id}, name="TRACE_IMPACT", ctx=ctx)
            for row in rows:
                ds_id = row.get("dataset_id")
                if ds_id and ds_id not in impacted_datasets:
                    # Retrieve exact lineage path
                    path_rows = gw.query(
                        queries.Q_DATASET_LINEAGE_PATH,
                        {"exp_id": exp_id, "target_ds_id": ds_id},
                        name="LINEAGE_PATH",
                        ctx=ctx,
                    )
                    path = [exp_id, "PRODUCED"]
                    if path_rows and path_rows[0].get("path_nodes"):
                        p_nodes = path_rows[0]["path_nodes"]
                        # reverse since path is from target up to root
                        path.extend(reversed(p_nodes))
                    else:
                        path.append(ds_id)

                    reason = (
                        f"Directly produced by lapsed experiment {exp_id}"
                        if path_rows and len(path_rows[0].get("path_nodes", [])) <= 1
                        else f"Derived from dataset produced by lapsed experiment {exp_id}"
                    )

                    impacted_datasets[ds_id] = ImpactedNode(
                        id=ds_id,
                        label="Dataset",
                        current_status=row.get("dataset_status") or "UNKNOWN",
                        reason=reason,
                        dependency_path=path,
                    )

                rpt_id = row.get("report_id")
                if rpt_id and rpt_id not in impacted_reports:
                    rpt_path = list(impacted_datasets[ds_id].dependency_path) + ["USES_DATASET", rpt_id]
                    impacted_reports[rpt_id] = ImpactedNode(
                        id=rpt_id,
                        label="Report",
                        current_status=row.get("report_status") or "UNKNOWN",
                        reason=f"Depends on affected dataset {ds_id}",
                        dependency_path=rpt_path,
                    )

        return ImpactResult(
            lapsed_experiment_ids=lapsed_experiment_ids,
            datasets=list(impacted_datasets.values()),
            reports=list(impacted_reports.values()),
        )

    @staticmethod
    def resolve_assignment(gw: GraphGateway, dataset_id: str, ctx: Any | None = None) -> AssignmentResult:
        """
        Determine task assignee in order:
        1. Owner of the dataset
        2. Performer of the producing experiment
        3. Custodian of the instrument
        4. Otherwise ESCALATED with explicit reason
        """
        rows = gw.query(queries.Q_RESOLVE_ASSIGNMENT, {"dataset_id": dataset_id}, name="RESOLVE_ASSIGNMENT", ctx=ctx)
        if not rows:
            return AssignmentResult(
                dataset_id=dataset_id,
                assignee_id=None,
                assignee_name=None,
                role_matched=None,
                is_escalated=True,
                escalation_reason="Dataset not found in graph",
            )

        row = rows[0]
        # 1. Owner
        if row.get("owner_id"):
            return AssignmentResult(
                dataset_id=dataset_id,
                assignee_id=row["owner_id"],
                assignee_name=row.get("owner_name"),
                role_matched="OWNER",
                is_escalated=False,
                escalation_reason=None,
            )

        # 2. Performer
        if row.get("performer_id"):
            return AssignmentResult(
                dataset_id=dataset_id,
                assignee_id=row["performer_id"],
                assignee_name=row.get("performer_name"),
                role_matched="PERFORMER",
                is_escalated=False,
                escalation_reason=None,
            )

        # 3. Custodian
        if row.get("custodian_id"):
            return AssignmentResult(
                dataset_id=dataset_id,
                assignee_id=row["custodian_id"],
                assignee_name=row.get("custodian_name"),
                role_matched="CUSTODIAN",
                is_escalated=False,
                escalation_reason=None,
            )

        # 4. Explicit escalation
        return AssignmentResult(
            dataset_id=dataset_id,
            assignee_id=None,
            assignee_name=None,
            role_matched=None,
            is_escalated=True,
            escalation_reason="No owner, performer, or custodian found in graph relationships",
        )

    @classmethod
    def execute_incident_actions(
        cls,
        gw: GraphGateway,
        incident_id: str,
        impact: ImpactResult,
        recorded_at: int,
        actor_agent: str = "ImpactAgent",
        ctx: Any | None = None,
    ) -> dict[str, Any]:
        """
        Atomically and idempotently apply quarantine, report status updates,
        task creation, and ActionRecords for an incident.
        """
        # Ensure Incident node exists
        gw.query(
            """
            MERGE (inc:Incident {id: $incident_id})
            ON CREATE SET
                inc.incident_type = 'CERT_LAPSE',
                inc.status = 'IN_PROGRESS',
                inc.opened_at = $at,
                inc.next_seq = 1
            """,
            {"incident_id": incident_id, "at": recorded_at},
            name="ENSURE_INCIDENT",
            ctx=ctx,
        )

        quarantined: list[str] = []
        reports_held: list[str] = []
        reports_flagged_review: list[str] = []
        tasks_created: list[dict[str, Any]] = []
        actions_skipped: list[str] = []

        # 1. Quarantine Datasets
        for ds in impact.datasets:
            ar_id = f"ar_quarantine_{incident_id}_{ds.id}"
            task_id = f"task_quarantine_{incident_id}_{ds.id}"

            # Idempotency check
            check = gw.query(queries.Q_CHECK_ACTION_RECORD_EXISTS, {"id": ar_id}, name="CHECK_AR", ctx=ctx)
            if check:
                actions_skipped.append(ar_id)
                continue

            # Update dataset status in graph
            gw.query(queries.Q_QUARANTINE_DATASET, {"dataset_id": ds.id}, name="QUARANTINE_DATASET", ctx=ctx)
            quarantined.append(ds.id)

            # Resolve responsible scientist
            assign_res = cls.resolve_assignment(gw, ds.id, ctx=ctx)

            # Create Task
            task_status = "ESCALATED" if assign_res.is_escalated else "OPEN"
            gw.query(
                queries.Q_CREATE_TASK,
                {
                    "task_id": task_id,
                    "incident_id": incident_id,
                    "task_type": "REVIEW_QUARANTINE",
                    "status": task_status,
                    "assignee_id": assign_res.assignee_id,
                    "escalation_reason": assign_res.escalation_reason,
                    "created_at": recorded_at,
                    "target_id": ds.id,
                },
                name="CREATE_TASK_DATASET",
                ctx=ctx,
            )
            tasks_created.append({
                "task_id": task_id,
                "target_id": ds.id,
                "status": task_status,
                "assignee_id": assign_res.assignee_id,
                "role": assign_res.role_matched,
            })

            # Record ActionRecord
            gw.query(
                queries.Q_RECORD_ACTION,
                {
                    "id": ar_id,
                    "incident_id": incident_id,
                    "action_type": "QUARANTINE_DATASET",
                    "target_id": ds.id,
                    "prior_state": ds.current_status,
                    "new_state": "QUARANTINED",
                    "reason": ds.reason,
                    "evidence_path_json": json.dumps(ds.dependency_path),
                    "recorded_at": recorded_at,
                    "actor_agent": actor_agent,
                    "rule_version": RULE_VERSION,
                },
                name="RECORD_ACTION_QUARANTINE",
                ctx=ctx,
            )

        # 2. Update Reports
        for rpt in impact.reports:
            ar_id = f"ar_report_{incident_id}_{rpt.id}"
            check = gw.query(queries.Q_CHECK_ACTION_RECORD_EXISTS, {"id": ar_id}, name="CHECK_AR", ctx=ctx)
            if check:
                actions_skipped.append(ar_id)
                continue

            new_status = ""
            action_type = ""
            if rpt.current_status == "DRAFT":
                new_status = "HELD"
                action_type = "HOLD_REPORT"
                reports_held.append(rpt.id)
            elif rpt.current_status == "PUBLISHED":
                new_status = "REQUIRES_REVIEW"
                action_type = "FLAG_REPORT_REVIEW"
                reports_flagged_review.append(rpt.id)

                # Create Task for reviewing published report
                task_id = f"task_review_report_{incident_id}_{rpt.id}"
                gw.query(
                    queries.Q_CREATE_TASK,
                    {
                        "task_id": task_id,
                        "incident_id": incident_id,
                        "task_type": "REVIEW_PUBLISHED_REPORT",
                        "status": "ESCALATED",
                        "assignee_id": None,
                        "escalation_reason": "Published report impacted by invalid upstream data; requires senior author review",
                        "created_at": recorded_at,
                        "target_id": rpt.id,
                    },
                    name="CREATE_TASK_REPORT",
                    ctx=ctx,
                )
                tasks_created.append({
                    "task_id": task_id,
                    "target_id": rpt.id,
                    "status": "ESCALATED",
                    "assignee_id": None,
                    "role": "REPORT_REVIEW",
                })
            elif rpt.current_status in ("HELD", "REQUIRES_REVIEW"):
                # Already in defensive state
                actions_skipped.append(ar_id)
                continue

            if new_status:
                gw.query(
                    queries.Q_UPDATE_REPORT_STATUS,
                    {"report_id": rpt.id, "new_status": new_status},
                    name="UPDATE_REPORT_STATUS",
                    ctx=ctx,
                )
                gw.query(
                    queries.Q_RECORD_ACTION,
                    {
                        "id": ar_id,
                        "incident_id": incident_id,
                        "action_type": action_type,
                        "target_id": rpt.id,
                        "prior_state": rpt.current_status,
                        "new_state": new_status,
                        "reason": rpt.reason,
                        "evidence_path_json": json.dumps(rpt.dependency_path),
                        "recorded_at": recorded_at,
                        "actor_agent": actor_agent,
                        "rule_version": RULE_VERSION,
                    },
                    name="RECORD_ACTION_REPORT",
                    ctx=ctx,
                )

        return {
            "incident_id": incident_id,
            "quarantined_datasets": quarantined,
            "reports_held": reports_held,
            "reports_flagged_review": reports_flagged_review,
            "tasks_created": tasks_created,
            "actions_skipped": actions_skipped,
        }
