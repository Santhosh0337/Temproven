"""
temproven/agents/evidence.py
────────────────────────────
EvidenceAgent implementation.

Responsibilities:
- Ingests calibration evidence and claims through the graph.
- Applies bitemporal policy: authoritative evidence supersedes prior claim;
  unverified contradiction links [:CONTRADICTS] without superseding.
- Opens incident and creates initial SELECT_PLAYBOOK handoff to IntegrityAgent.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, TYPE_CHECKING

from temproven.graph import queries
from temproven.memory.episodic import EventType

if TYPE_CHECKING:
    from temproven.clock import Clock
    from temproven.context import RunContext
    from temproven.graph.gateway import GraphGateway


class EvidenceAgent:
    """Thin agent responsible for evidence ingestion and incident initiation."""

    def __init__(self, gw: GraphGateway, clock: Clock | None = None) -> None:
        self.gw = gw
        self.clock = clock

    def ingest_and_open_incident(
        self,
        incident_id: str,
        instrument_id: str,
        cert_id: str,
        old_claim_id: str,
        new_claim_id: str,
        new_valid_until: int,
        evidence_id: str,
        evidence_ref: str,
        description: str,
        trust_level: str = "AUTHORITATIVE",
        incident_type: str = "CERT_LAPSE",
        ctx: RunContext | None = None,
    ) -> dict[str, Any]:
        """
        Record evidence and claim, open the incident, and dispatch SELECT_PLAYBOOK handoff.
        """
        at_epoch = ctx.clock.now_epoch() if ctx else (self.clock.now_epoch() if self.clock else 0)

        # 1. Ensure Incident node exists
        self.gw.query(
            """
            MERGE (inc:Incident {id: $incident_id})
            ON CREATE SET
                inc.incident_type = $incident_type,
                inc.status = 'IN_PROGRESS',
                inc.opened_at = $at,
                inc.next_seq = 1
            """,
            {"incident_id": incident_id, "incident_type": incident_type, "at": at_epoch},
            name="ENSURE_INCIDENT",
            ctx=ctx,
        )

        if ctx and ctx.recorder:
            ctx.recorder.record_event(
                gw=self.gw,
                ctx=ctx,
                event_type=EventType.INCIDENT_OPENED,
                summary=f"Incident {incident_id} opened for {incident_type} on {instrument_id}",
                logical_key="open_incident",
                outcome="OPENED",
                payload={"instrument_id": instrument_id, "cert_id": cert_id},
            )

        # 2. Ingest Evidence and Claim
        if trust_level == "AUTHORITATIVE":
            # Supersession path
            self.gw.query(
                """
                MATCH (cert:CalibrationCertificate {id: $cert_id}), (c1:Claim {id: $old_claim_id})
                MERGE (c2:Claim {id: $new_claim_id})
                ON CREATE SET
                    c2.claim_type = 'CERT_VALIDITY',
                    c2.subject_id = $cert_id,
                    c2.valid_from = c1.valid_from,
                    c2.valid_until = $new_valid_until,
                    c2.recorded_at = $at,
                    c2.verified_at = $at,
                    c2.status = 'VERIFIED',
                    c2.summary = $desc
                MERGE (ev:Evidence {id: $evidence_id})
                ON CREATE SET
                    ev.source_type = 'CALIBRATION_AUTHORITY',
                    ev.trust_level = 'AUTHORITATIVE',
                    ev.observed_at = $at,
                    ev.recorded_at = $at,
                    ev.ref = $evidence_ref,
                    ev.description = $desc
                MERGE (c2)-[:SUPPORTED_BY]->(ev)
                MERGE (cert)-[:HAS_VALIDITY]->(c2)
                MERGE (c2)-[:SUPERSEDES {recorded_at: $at, reason: $desc}]->(c1)
                SET c1.status = 'SUPERSEDED'
                """,
                {
                    "cert_id": cert_id,
                    "old_claim_id": old_claim_id,
                    "new_claim_id": new_claim_id,
                    "new_valid_until": new_valid_until,
                    "evidence_id": evidence_id,
                    "evidence_ref": evidence_ref,
                    "desc": description,
                    "at": at_epoch,
                },
                name="INGEST_AUTHORITATIVE_CORRECTION",
                ctx=ctx,
            )
        else:
            # Unverified contradiction path
            self.gw.query(
                """
                MATCH (cert:CalibrationCertificate {id: $cert_id}), (c1:Claim {id: $old_claim_id})
                MERGE (c_contra:Claim {id: $new_claim_id})
                ON CREATE SET
                    c_contra.claim_type = 'CERT_VALIDITY',
                    c_contra.subject_id = $cert_id,
                    c_contra.valid_from = c1.valid_from,
                    c_contra.valid_until = $new_valid_until,
                    c_contra.recorded_at = $at,
                    c_contra.verified_at = null,
                    c_contra.status = 'UNVERIFIED',
                    c_contra.summary = $desc
                MERGE (ev:Evidence {id: $evidence_id})
                ON CREATE SET
                    ev.source_type = 'UNVERIFIED_TIP',
                    ev.trust_level = 'UNVERIFIED',
                    ev.observed_at = $at,
                    ev.recorded_at = $at,
                    ev.ref = $evidence_ref,
                    ev.description = $desc
                MERGE (c_contra)-[:SUPPORTED_BY]->(ev)
                MERGE (cert)-[:HAS_VALIDITY]->(c_contra)
                MERGE (c_contra)-[:CONTRADICTS {detected_at: $at, reason: $desc, resolution: 'UNRESOLVED'}]->(c1)
                """,
                {
                    "cert_id": cert_id,
                    "old_claim_id": old_claim_id,
                    "new_claim_id": new_claim_id,
                    "new_valid_until": new_valid_until,
                    "evidence_id": evidence_id,
                    "evidence_ref": evidence_ref,
                    "desc": description,
                    "at": at_epoch,
                },
                name="INGEST_UNVERIFIED_CONTRADICTION",
                ctx=ctx,
            )

        if ctx and ctx.recorder:
            ctx.recorder.record_event(
                gw=self.gw,
                ctx=ctx,
                event_type=EventType.EVIDENCE_INGESTED,
                summary=f"Ingested {trust_level} evidence {evidence_id} ({evidence_ref})",
                logical_key=f"ingest_{evidence_id}",
                evidence_ids=[evidence_id],
                payload={"trust_level": trust_level, "claim_id": new_claim_id},
            )

        # 3. Create handoff SELECT_PLAYBOOK to IntegrityAgent
        h_payload = {
            "instrument_id": instrument_id,
            "cert_id": cert_id,
            "incident_type": incident_type,
            "evidence_id": evidence_id,
            "trust_level": trust_level,
        }
        h_key = f"handoff_select_playbook_{incident_id}"
        h_id = f"h_{hashlib.sha256(f'{incident_id}:{h_key}'.encode()).hexdigest()[:16]}"

        self.gw.query(
            queries.Q_CREATE_HANDOFF,
            {
                "id": h_id,
                "incident_id": incident_id,
                "from_agent": "EvidenceAgent",
                "to_agent": "IntegrityAgent",
                "task_type": "SELECT_PLAYBOOK",
                "payload_json": json.dumps(h_payload),
                "idempotency_key": h_key,
                "created_at": at_epoch,
            },
            name="HANDOFF_CREATE",
            ctx=ctx,
        )

        if ctx and ctx.recorder:
            ctx.recorder.record_event(
                gw=self.gw,
                ctx=ctx,
                event_type=EventType.HANDOFF_CREATED,
                summary="Created handoff SELECT_PLAYBOOK to IntegrityAgent",
                logical_key=f"ev_{h_key}",
                payload={"handoff_id": h_id, "to_agent": "IntegrityAgent"},
            )

        return {"incident_id": incident_id, "handoff_id": h_id, "status": "PENDING"}
