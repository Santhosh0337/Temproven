"""
temproven/graph/schema.py
─────────────────────────
Defines and applies indexes and constraints for FalkorDB.

DESIGN DECISIONS:
- Indexes on `id` are created for every node label to guarantee fast O(1)
  lookups during traversals and bitemporal queries.
- FalkorDB returns an error if `CREATE INDEX FOR (n:Label) ON (n.id)` is
  executed when the index already exists. apply_schema() catches this
  specifically ("already indexed") so schema application is strictly idempotent.
- Parameterized execution goes through GraphGateway.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from temproven.graph.gateway import GraphGateway

logger = logging.getLogger(__name__)

# Primary labels that require fast indexing on `id`
INDEXED_LABELS: list[str] = [
    "Instrument",
    "CalibrationCertificate",
    "Claim",
    "CertificateRevocation",
    "Evidence",
    "Experiment",
    "InstrumentUsage",
    "Dataset",
    "Report",
    "Scientist",
    "Incident",
    "Observation",
    "Event",
    "InvestigationPlaybook",
    "PlaybookStep",
    "PlaybookRun",
    "StepResult",
    "Task",
    "ActionRecord",
    "PublicationAttempt",
    "Handoff",
    "IncidentMetrics",
]

# Additional property indexes for fast relation lookups
SECONDARY_INDEXES: list[tuple[str, str]] = [
    ("Claim", "subject_id"),
    ("Claim", "claim_type"),
    ("InstrumentUsage", "required_scope"),
    ("CalibrationCertificate", "scope"),
    ("Event", "incident_id"),
    ("Handoff", "incident_id"),
    ("Task", "incident_id"),
]


def apply_schema(gw: GraphGateway) -> list[str]:
    """
    Apply indexes on the target FalkorDB graph.
    Returns the list of index names/labels created or confirmed existing.
    Idempotent: safe to run multiple times.
    """
    applied = []

    # 1. Primary id indexes
    for label in INDEXED_LABELS:
        cypher = f"CREATE INDEX FOR (n:{label}) ON (n.id)"
        try:
            gw.query(cypher, {}, name=f"SCHEMA_INDEX_{label}_id")
            applied.append(f"{label}(id) [created]")
        except Exception as exc:
            err_msg = str(exc).lower()
            if "already indexed" in err_msg:
                applied.append(f"{label}(id) [existing]")
            else:
                logger.error(f"Failed to create index on {label}(id): {exc}")
                raise

    # 2. Secondary property indexes
    for label, prop in SECONDARY_INDEXES:
        cypher = f"CREATE INDEX FOR (n:{label}) ON (n.{prop})"
        try:
            gw.query(cypher, {}, name=f"SCHEMA_INDEX_{label}_{prop}")
            applied.append(f"{label}({prop}) [created]")
        except Exception as exc:
            err_msg = str(exc).lower()
            if "already indexed" in err_msg:
                applied.append(f"{label}({prop}) [existing]")
            else:
                logger.error(f"Failed to create secondary index on {label}({prop}): {exc}")
                raise

    return applied
