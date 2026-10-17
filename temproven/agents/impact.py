"""
temproven/agents/impact.py
──────────────────────────
ImpactAgent implementation.

Responsibilities:
- Assesses downstream impact using DiagnosticLadder (exploratory) or Playbook steps (reuse).
- Forms action proposals with inspectable lineage paths.
- Applies approved mutations (quarantine, report hold/review, task creation) idempotently.
- Emits ACTION_APPLIED events and hands off for proposal validation and closing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, TYPE_CHECKING

from temproven.domain.actions import ActionService
from temproven.graph import queries
from temproven.memory.episodic import EventType
from temproven.memory.procedural import (
    DiagnosticLadder,
    STEP_REGISTRY,
    execute_playbook_step,
)

if TYPE_CHECKING:
    from temproven.clock import Clock
    from temproven.context import RunContext
    from temproven.graph.gateway import GraphGateway


class ImpactAgent:
    """Agent executing impact analysis, proposal generation, and approved action writes."""

    def __init__(self, gw: GraphGateway, clock: Clock | None = None) -> None:
        self.gw = gw
        self.clock = clock

    def handle_assess_impact(self, ctx: RunContext, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Run READ segment (diagnostic ladder or playbook steps) and dispatch VALIDATE_PROPOSALS.
        """
        mode = payload.get("mode", "EXPLORATORY")
        instrument_id = payload.get("instrument_id")
        playbook_id = payload.get("playbook_id")

        state: dict[str, Any] = {"params": {"instrument_id": instrument_id}}

        if mode == "EXPLORATORY":
            # 1. Run full 5-step diagnostic ladder
            ladder_res = DiagnosticLadder.run_ladder(self.gw, ctx, instrument_id=instrument_id)

            # 2. Run READ steps directly
            for st_name in ("CONFIRM_LAPSE", "TRACE_DEPENDENTS", "PROPOSE_ACTIONS"):
                step_def = STEP_REGISTRY[st_name]
                pre = step_def.precondition(self.gw, ctx, {}, state)
                if pre.passed:
                    outcome = step_def.execute(self.gw, ctx, {}, state)
                    step_def.postcondition(self.gw, ctx, {}, state, outcome)
        else:
            # Reusing playbook: execute READ steps
            run_id = f"run_{ctx.incident_id}_{playbook_id}"
            at_epoch = ctx.clock.now_epoch()

            # Record PlaybookRun if not existing
            self.gw.query(
                queries.Q_RECORD_PLAYBOOK_RUN,
                {
                    "id": run_id,
                    "incident_id": ctx.incident_id,
                    "playbook_id": playbook_id,
                    "mode": mode,
                    "started_at": at_epoch,
                    "finished_at": None,
                },
                name="RECORD_PLAYBOOK_RUN",
                ctx=ctx,
            )

            # Check H1 first
            ladder_res = DiagnosticLadder.run_ladder(self.gw, ctx, instrument_id=instrument_id)
            if ladder_res["hypothesis_results"].get("H1") == "CONFIRMED":
                # Ladder H2-H5 skipped because H1 confirmed by authoritative evidence
                if ctx.metrics:
                    ctx.metrics.steps_skipped += 4

            # Fetch and execute READ steps
            steps = self.gw.query(queries.Q_GET_PLAYBOOK_STEPS, {"playbook_id": playbook_id}, name="GET_PLAYBOOK_STEPS", ctx=ctx)
            for s in steps:
                if s["kind"] == "READ" and s["role"] in ("CONFIRM", "REMEDIATE"):
                    execute_playbook_step(self.gw, ctx, run_id, s, state)
                    if ctx.metrics:
                        ctx.metrics.steps_executed += 1

        proposals = state.get("proposals", [])

        # Dispatch VALIDATE_PROPOSALS to IntegrityAgent
        h_payload = {
            "proposals": proposals,
            "playbook_id": playbook_id,
            "mode": mode,
            "instrument_id": instrument_id,
        }
        h_key = f"handoff_validate_proposals_{ctx.incident_id}"
        h_id = f"h_{hashlib.sha256(f'{ctx.incident_id}:{h_key}'.encode()).hexdigest()[:16]}"
        at_epoch = ctx.clock.now_epoch()

        self.gw.query(
            queries.Q_CREATE_HANDOFF,
            {
                "id": h_id,
                "incident_id": ctx.incident_id,
                "from_agent": "ImpactAgent",
                "to_agent": "IntegrityAgent",
                "task_type": "VALIDATE_PROPOSALS",
                "payload_json": json.dumps(h_payload),
                "idempotency_key": h_key,
                "created_at": at_epoch,
            },
            name="HANDOFF_CREATE",
            ctx=ctx,
        )

        if ctx.recorder:
            ctx.recorder.record_event(
                gw=self.gw,
                ctx=ctx,
                event_type=EventType.HANDOFF_CREATED,
                summary=f"Created handoff VALIDATE_PROPOSALS to IntegrityAgent ({len(proposals)} proposals)",
                logical_key=f"ev_{h_key}",
                payload={"handoff_id": h_id, "to_agent": "IntegrityAgent", "proposal_count": len(proposals)},
            )

        return {"proposals_count": len(proposals), "handoff_id": h_id}

    def handle_apply_approved(self, ctx: RunContext, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Execute approved WRITE actions (quarantine, report hold, tasks, gate verify) and dispatch VERIFY_AND_CLOSE.
        """
        approved_proposals = payload.get("approved_proposals", [])
        mode = payload.get("mode", "EXPLORATORY")
        playbook_id = payload.get("playbook_id")
        instrument_id = payload.get("instrument_id")

        state: dict[str, Any] = {
            "params": {"instrument_id": instrument_id},
            "proposals": approved_proposals,
        }

        # Re-derive impact object for ActionService execution
        quarantine_ds_ids = [p["target_id"] for p in approved_proposals if p["action_type"] == "QUARANTINE_DATASET"]
        # Find producing experiments from graph
        exp_rows = self.gw.query(
            "MATCH (exp:Experiment)-[:PRODUCED]->(ds:Dataset) WHERE ds.id IN $ds_ids RETURN DISTINCT exp.id AS eid",
            {"ds_ids": quarantine_ds_ids},
            name="GET_PRODUCING_EXPS",
            ctx=ctx,
        )
        lapsed_exp_ids = [r["eid"] for r in exp_rows]
        impact = ActionService.trace_impact(self.gw, lapsed_exp_ids)
        state["impact"] = impact
        state["lapsed_exp_ids"] = lapsed_exp_ids

        if mode == "EXPLORATORY":
            # Run WRITE steps directly
            for st_name in ("QUARANTINE_DATASETS", "HOLD_REPORTS", "CREATE_ASSIGN_TASKS", "VERIFY_GATE_BLOCKS"):
                step_def = STEP_REGISTRY[st_name]
                pre = step_def.precondition(self.gw, ctx, {}, state)
                if pre.passed:
                    outcome = step_def.execute(self.gw, ctx, {}, state)
                    step_def.postcondition(self.gw, ctx, {}, state, outcome)
        else:
            # Reusing playbook: execute WRITE steps with StepResult tracking
            run_id = f"run_{ctx.incident_id}_{playbook_id}"
            steps = self.gw.query(queries.Q_GET_PLAYBOOK_STEPS, {"playbook_id": playbook_id}, name="GET_PLAYBOOK_STEPS", ctx=ctx)
            for s in steps:
                if s["kind"] == "WRITE" or s["step_type"] == "VERIFY_GATE_BLOCKS":
                    execute_playbook_step(self.gw, ctx, run_id, s, state)
                    if ctx.metrics:
                        ctx.metrics.steps_executed += 1

        action_res = state.get("action_result", {})

        if ctx.recorder:
            ctx.recorder.record_event(
                gw=self.gw,
                ctx=ctx,
                event_type=EventType.ACTION_APPLIED,
                summary=f"Applied actions: {len(action_res.get('quarantined_datasets', []))} quarantined, {len(action_res.get('tasks_created', []))} tasks created",
                logical_key="apply_approved_actions",
                outcome="APPLIED",
                action_ids=[t["task_id"] for t in action_res.get("tasks_created", [])],
                payload=action_res,
            )

        # Dispatch VERIFY_AND_CLOSE to IntegrityAgent
        h_payload = {
            "playbook_id": playbook_id,
            "mode": mode,
            "instrument_id": instrument_id,
        }
        h_key = f"handoff_verify_close_{ctx.incident_id}"
        h_id = f"h_{hashlib.sha256(f'{ctx.incident_id}:{h_key}'.encode()).hexdigest()[:16]}"
        at_epoch = ctx.clock.now_epoch()

        self.gw.query(
            queries.Q_CREATE_HANDOFF,
            {
                "id": h_id,
                "incident_id": ctx.incident_id,
                "from_agent": "ImpactAgent",
                "to_agent": "IntegrityAgent",
                "task_type": "VERIFY_AND_CLOSE",
                "payload_json": json.dumps(h_payload),
                "idempotency_key": h_key,
                "created_at": at_epoch,
            },
            name="HANDOFF_CREATE",
            ctx=ctx,
        )

        if ctx.recorder:
            ctx.recorder.record_event(
                gw=self.gw,
                ctx=ctx,
                event_type=EventType.HANDOFF_CREATED,
                summary="Created handoff VERIFY_AND_CLOSE to IntegrityAgent",
                logical_key=f"ev_{h_key}",
                payload={"handoff_id": h_id, "to_agent": "IntegrityAgent"},
            )

        return {"actions_applied": action_res, "handoff_id": h_id}
