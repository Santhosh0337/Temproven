"""
temproven/seed/seed.py
──────────────────────
Seeds deterministic synthetic records for Temproven into FalkorDB.

SCENARIO STRUCTURE:
- Instrument INS-A (spectrophotometer, scope SPECTRO_ABSORBANCE)
  - Cert CERT-A1: valid [2026-01-01T00:00Z, 2026-07-01T00:00Z), recorded 2026-01-02
  - EXP-A1 (March), EXP-A2 (May), EXP-A3 (June 15), EXP-A4 (June 28)
  - Datasets DS-A1..DS-A4, plus derived DS-A5 (DERIVED_FROM DS-A3)
  - Reports RPT-1 (uses DS-A1, DS-A2, DS-A5) and RPT-2 (control, uses DS-A1, DS-A2)
  - Scientists Alice (custodian of INS-A, performer of A1-A3, owns A1, A2, A5)
    and Bob (performer of A4, owns A3, A4).

- Instrument INS-B (thermal profiler, scope THERMAL_PROFILE)
  - Chained certs CERT-B1 [2026-02-01, 2026-04-01) and CERT-B2 [2026-04-01, 2026-09-01)
  - EXP-B1 (March), EXP-B2 (spans boundary 2026-04-01, covered only by chain)
  - EXP-B3 (August) produces DS-B3, derived DS-B4.
  - Unassigned/orphan: INS-B has no custodian, EXP-B3 has no performer, DS-B3/B4 have no owner
    (triggers task escalation).
  - Report RPT-3 (uses DS-B2, DS-B4).

All timestamps are integer epoch seconds in UTC.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from temproven.domain.temporal import parse_iso_to_epoch
from temproven.graph.gateway import GraphGateway
from temproven.graph.schema import apply_schema

logger = logging.getLogger(__name__)


def clear_graph(gw: GraphGateway) -> None:
    """Wipe all nodes and edges in the current graph."""
    gw.query("MATCH (n) DETACH DELETE n", {}, name="CLEAR_GRAPH")


def seed_graph(gw: GraphGateway, clear_existing: bool = True) -> dict[str, int]:
    """
    Apply schema and seed base synthetic records for Act 1 and initial INS-B state.
    Returns counts of seeded entities.
    """
    if clear_existing:
        clear_graph(gw)

    apply_schema(gw)

    # ── 1. Scientists ────────────────────────────────────────────────────────
    scientists = [
        {"id": "SCI-1", "name": "Dr. Alice Chen", "role": "Senior Biochemist"},
        {"id": "SCI-2", "name": "Dr. Bob Martinez", "role": "Analytical Chemist"},
    ]
    for s in scientists:
        gw.query(
            "MERGE (s:Scientist {id: $id}) ON CREATE SET s.name = $name, s.role = $role",
            s,
            name="SEED_SCIENTIST",
        )

    # ── 2. Instrument INS-A & Cert CERT-A1 ───────────────────────────────────
    # Valid from 2026-01-01 to 2026-07-01
    t_a1_valid_from = parse_iso_to_epoch("2026-01-01T00:00:00Z")
    t_a1_valid_until = parse_iso_to_epoch("2026-07-01T00:00:00Z")
    t_a1_recorded = parse_iso_to_epoch("2026-01-02T00:00:00Z")

    gw.query(
        """
        MERGE (inst:Instrument {id: 'INS-A'})
        ON CREATE SET inst.name = 'Spectrophotometer Alpha', inst.kind = 'spectrophotometer'
        WITH inst
        MATCH (sci:Scientist {id: 'SCI-1'})
        MERGE (sci)-[:CUSTODIAN_OF]->(inst)
        """,
        {},
        name="SEED_INS_A",
    )

    gw.query(
        """
        MATCH (inst:Instrument {id: 'INS-A'})
        MERGE (cert:CalibrationCertificate {id: 'CERT-A1'})
        ON CREATE SET
            cert.scope = 'SPECTRO_ABSORBANCE',
            cert.issuer = 'National Metrology Institute',
            cert.recorded_at = $t_rec
        MERGE (cert)-[:CERTIFIES]->(inst)
        MERGE (ev:Evidence {id: 'EV-A1-v1'})
        ON CREATE SET
            ev.source_type = 'CALIBRATION_AUTHORITY',
            ev.trust_level = 'AUTHORITATIVE',
            ev.observed_at = $t_vf,
            ev.recorded_at = $t_rec,
            ev.ref = 'CAL-2026-NMI-001',
            ev.description = 'Initial 6-month calibration certificate'
        MERGE (c:Claim {id: 'CLAIM-A1-v1'})
        ON CREATE SET
            c.claim_type = 'CERT_VALIDITY',
            c.subject_id = 'CERT-A1',
            c.valid_from = $t_vf,
            c.valid_until = $t_vu,
            c.recorded_at = $t_rec,
            c.verified_at = $t_rec,
            c.summary = 'Initial verified 6-month validity [Jan-Jul 2026)',
            c.status = 'VERIFIED'
        MERGE (cert)-[:HAS_VALIDITY]->(c)
        MERGE (c)-[:SUPPORTED_BY]->(ev)
        """,
        {
            "t_vf": t_a1_valid_from,
            "t_vu": t_a1_valid_until,
            "t_rec": t_a1_recorded,
        },
        name="SEED_CERT_A1",
    )

    # ── 3. INS-A Experiments, Usages, Datasets ──────────────────────────────
    # A1: March (Covered)
    # A2: May (Covered)
    # A3: June 15 (Covered in Act 1, Not covered after Act 2 correction)
    # A4: June 28 (Covered in Act 1, Not covered after Act 2 correction)
    exp_a_data = [
        {
            "exp_id": "EXP-A1",
            "title": "Protein Binding Assay March",
            "usage_id": "USG-A1",
            "used_from": parse_iso_to_epoch("2026-03-10T10:00:00Z"),
            "used_until": parse_iso_to_epoch("2026-03-10T16:00:00Z"),
            "dataset_id": "DS-A1",
            "dataset_name": "Binding Kinetics March",
            "performer_id": "SCI-1",
            "owner_id": "SCI-1",
        },
        {
            "exp_id": "EXP-A2",
            "title": "Affinity Assay May",
            "usage_id": "USG-A2",
            "used_from": parse_iso_to_epoch("2026-05-15T09:00:00Z"),
            "used_until": parse_iso_to_epoch("2026-05-15T15:00:00Z"),
            "dataset_id": "DS-A2",
            "dataset_name": "Affinity Table May",
            "performer_id": "SCI-1",
            "owner_id": "SCI-1",
        },
        {
            "exp_id": "EXP-A3",
            "title": "Dose Response June 15",
            "usage_id": "USG-A3",
            "used_from": parse_iso_to_epoch("2026-06-15T08:00:00Z"),
            "used_until": parse_iso_to_epoch("2026-06-15T14:00:00Z"),
            "dataset_id": "DS-A3",
            "dataset_name": "Dose Response Raw Data",
            "performer_id": "SCI-1",
            "owner_id": None,  # Has performer but no direct owner (tests fallback)
        },
        {
            "exp_id": "EXP-A4",
            "title": "Selectivity Screen June 28",
            "usage_id": "USG-A4",
            "used_from": parse_iso_to_epoch("2026-06-28T11:00:00Z"),
            "used_until": parse_iso_to_epoch("2026-06-28T17:00:00Z"),
            "dataset_id": "DS-A4",
            "dataset_name": "Selectivity Screen Data",
            "performer_id": "SCI-2",
            "owner_id": "SCI-2",
        },
    ]

    for item in exp_a_data:
        gw.query(
            """
            MATCH (inst:Instrument {id: 'INS-A'})
            MATCH (performer:Scientist {id: $performer_id})
            MERGE (exp:Experiment {id: $exp_id})
            ON CREATE SET exp.title = $title
            MERGE (performer)-[:PERFORMED]->(exp)
            MERGE (u:InstrumentUsage {id: $usage_id})
            ON CREATE SET
                u.used_from = $used_from,
                u.used_until = $used_until,
                u.required_scope = 'SPECTRO_ABSORBANCE'
            MERGE (exp)-[:HAS_USAGE]->(u)
            MERGE (u)-[:OF_INSTRUMENT]->(inst)
            MERGE (ds:Dataset {id: $dataset_id})
            ON CREATE SET ds.name = $dataset_name, ds.status = 'ACTIVE'
            MERGE (exp)-[:PRODUCED]->(ds)
            """,
            item,
            name=f"SEED_{item['exp_id']}",
        )
        if item.get("owner_id"):
            gw.query(
                """
                MATCH (sci:Scientist {id: $owner_id}), (ds:Dataset {id: $dataset_id})
                MERGE (sci)-[:OWNS]->(ds)
                """,
                {"owner_id": item["owner_id"], "dataset_id": item["dataset_id"]},
                name=f"SEED_OWNER_{item['dataset_id']}",
            )

    # Lineage: DS-A5 derived from DS-A3, owned by SCI-1
    gw.query(
        """
        MATCH (ds3:Dataset {id: 'DS-A3'})
        MATCH (sci:Scientist {id: 'SCI-1'})
        MERGE (ds5:Dataset {id: 'DS-A5'})
        ON CREATE SET ds5.name = 'Aggregated Dose Curve DS-A5', ds5.status = 'ACTIVE'
        MERGE (ds5)-[:DERIVED_FROM]->(ds3)
        MERGE (sci)-[:OWNS]->(ds5)
        """,
        {},
        name="SEED_DS_A5_LINEAGE",
    )

    # ── 4. Reports for INS-A ────────────────────────────────────────────────
    # RPT-1: uses DS-A1, DS-A2, DS-A5
    # RPT-2: control, uses DS-A1, DS-A2 only
    gw.query(
        """
        MERGE (r1:Report {id: 'RPT-1'})
        ON CREATE SET r1.title = 'Quarterly Lead Candidate Dossier', r1.publication_status = 'DRAFT'
        WITH r1
        MATCH (d1:Dataset {id: 'DS-A1'}), (d2:Dataset {id: 'DS-A2'}), (d5:Dataset {id: 'DS-A5'})
        MERGE (r1)-[:USES_DATASET]->(d1)
        MERGE (r1)-[:USES_DATASET]->(d2)
        MERGE (r1)-[:USES_DATASET]->(d5)
        """,
        {},
        name="SEED_RPT_1",
    )

    gw.query(
        """
        MERGE (r2:Report {id: 'RPT-2'})
        ON CREATE SET r2.title = 'Early Stage Screening Baseline (Control)', r2.publication_status = 'DRAFT'
        WITH r2
        MATCH (d1:Dataset {id: 'DS-A1'}), (d2:Dataset {id: 'DS-A2'})
        MERGE (r2)-[:USES_DATASET]->(d1)
        MERGE (r2)-[:USES_DATASET]->(d2)
        """,
        {},
        name="SEED_RPT_2",
    )

    # ── 5. Instrument INS-B & Chained Certs (Incident 2 baseline) ───────────
    # INS-B has NO custodian (triggers escalation check)
    gw.query(
        """
        MERGE (inst:Instrument {id: 'INS-B'})
        ON CREATE SET inst.name = 'Thermal Profiler Beta', inst.kind = 'thermal_profiler'
        """,
        {},
        name="SEED_INS_B",
    )

    # CERT-B1: [2026-02-01, 2026-04-01)
    # CERT-B2: [2026-04-01, 2026-09-01)
    t_b1_start = parse_iso_to_epoch("2026-02-01T00:00:00Z")
    t_b1_end = parse_iso_to_epoch("2026-04-01T00:00:00Z")
    t_b2_start = parse_iso_to_epoch("2026-04-01T00:00:00Z")
    t_b2_end = parse_iso_to_epoch("2026-09-01T00:00:00Z")

    gw.query(
        """
        MATCH (inst:Instrument {id: 'INS-B'})
        MERGE (cert1:CalibrationCertificate {id: 'CERT-B1'})
        ON CREATE SET
            cert1.scope = 'THERMAL_PROFILE',
            cert1.issuer = 'Thermal Standards Bureau',
            cert1.recorded_at = $t_b1_start
        MERGE (cert1)-[:CERTIFIES]->(inst)
        MERGE (ev1:Evidence {id: 'EV-B1'})
        ON CREATE SET
            ev1.source_type = 'CALIBRATION_AUTHORITY',
            ev1.trust_level = 'AUTHORITATIVE',
            ev1.observed_at = $t_b1_start,
            ev1.recorded_at = $t_b1_start,
            ev1.ref = 'CAL-2026-TSB-001',
            ev1.description = 'Thermal profile cert Part 1'
        MERGE (c1:Claim {id: 'CLAIM-B1-v1'})
        ON CREATE SET
            c1.claim_type = 'CERT_VALIDITY',
            c1.subject_id = 'CERT-B1',
            c1.valid_from = $t_b1_start,
            c1.valid_until = $t_b1_end,
            c1.recorded_at = $t_b1_start,
            c1.verified_at = $t_b1_start,
            c1.summary = 'Thermal profile validity Part 1 [Feb-Apr 2026)',
            c1.status = 'VERIFIED'
        MERGE (cert1)-[:HAS_VALIDITY]->(c1)
        MERGE (c1)-[:SUPPORTED_BY]->(ev1)

        MERGE (cert2:CalibrationCertificate {id: 'CERT-B2'})
        ON CREATE SET
            cert2.scope = 'THERMAL_PROFILE',
            cert2.issuer = 'Thermal Standards Bureau',
            cert2.recorded_at = $t_b2_start
        MERGE (cert2)-[:CERTIFIES]->(inst)
        MERGE (ev2:Evidence {id: 'EV-B2'})
        ON CREATE SET
            ev2.source_type = 'CALIBRATION_AUTHORITY',
            ev2.trust_level = 'AUTHORITATIVE',
            ev2.observed_at = $t_b2_start,
            ev2.recorded_at = $t_b2_start,
            ev2.ref = 'CAL-2026-TSB-002',
            ev2.description = 'Thermal profile cert Part 2'
        MERGE (c2:Claim {id: 'CLAIM-B2-v1'})
        ON CREATE SET
            c2.claim_type = 'CERT_VALIDITY',
            c2.subject_id = 'CERT-B2',
            c2.valid_from = $t_b2_start,
            c2.valid_until = $t_b2_end,
            c2.recorded_at = $t_b2_start,
            c2.verified_at = $t_b2_start,
            c2.summary = 'Thermal profile validity Part 2 [Apr-Sep 2026)',
            c2.status = 'VERIFIED'
        MERGE (cert2)-[:HAS_VALIDITY]->(c2)
        MERGE (c2)-[:SUPPORTED_BY]->(ev2)
        """,
        {
            "t_b1_start": t_b1_start,
            "t_b1_end": t_b1_end,
            "t_b2_start": t_b2_start,
            "t_b2_end": t_b2_end,
        },
        name="SEED_CERTS_INS_B",
    )

    # ── 6. INS-B Experiments & Datasets ─────────────────────────────────────
    # B1: March (single cert coverage)
    # B2: spans 2026-04-01 boundary (covered only by chain!)
    # B3: August (orphan experiment & dataset: no owner, performer, or custodian)
    t_b1_used_f = parse_iso_to_epoch("2026-03-02T08:00:00Z")
    t_b1_used_u = parse_iso_to_epoch("2026-03-03T18:00:00Z")
    t_b2_used_f = parse_iso_to_epoch("2026-03-31T20:00:00Z")
    t_b2_used_u = parse_iso_to_epoch("2026-04-01T04:00:00Z")  # Spans boundary!
    t_b3_used_f = parse_iso_to_epoch("2026-08-11T00:00:00Z")
    t_b3_used_u = parse_iso_to_epoch("2026-08-12T00:00:00Z")

    # EXP-B1
    gw.query(
        """
        MATCH (inst:Instrument {id: 'INS-B'}), (sci:Scientist {id: 'SCI-2'})
        MERGE (exp:Experiment {id: 'EXP-B1'})
        ON CREATE SET exp.title = 'Thermal Stability Batch 1'
        MERGE (sci)-[:PERFORMED]->(exp)
        MERGE (u:InstrumentUsage {id: 'USG-B1'})
        ON CREATE SET
            u.used_from = $uf, u.used_until = $uu, u.required_scope = 'THERMAL_PROFILE'
        MERGE (exp)-[:HAS_USAGE]->(u)
        MERGE (u)-[:OF_INSTRUMENT]->(inst)
        MERGE (ds:Dataset {id: 'DS-B1'})
        ON CREATE SET ds.name = 'Thermal Curve Batch 1', ds.status = 'ACTIVE'
        MERGE (exp)-[:PRODUCED]->(ds)
        MERGE (sci)-[:OWNS]->(ds)
        """,
        {"uf": t_b1_used_f, "uu": t_b1_used_u},
        name="SEED_EXP_B1",
    )

    # EXP-B2 (chain coverage across April 1 boundary)
    gw.query(
        """
        MATCH (inst:Instrument {id: 'INS-B'}), (sci:Scientist {id: 'SCI-2'})
        MERGE (exp:Experiment {id: 'EXP-B2'})
        ON CREATE SET exp.title = 'Overnight Melt Curve Exp (Spans Boundary)'
        MERGE (sci)-[:PERFORMED]->(exp)
        MERGE (u:InstrumentUsage {id: 'USG-B2'})
        ON CREATE SET
            u.used_from = $uf, u.used_until = $uu, u.required_scope = 'THERMAL_PROFILE'
        MERGE (exp)-[:HAS_USAGE]->(u)
        MERGE (u)-[:OF_INSTRUMENT]->(inst)
        MERGE (ds:Dataset {id: 'DS-B2'})
        ON CREATE SET ds.name = 'Melt Profile April', ds.status = 'ACTIVE'
        MERGE (exp)-[:PRODUCED]->(ds)
        """,
        {"uf": t_b2_used_f, "uu": t_b2_used_u},
        name="SEED_EXP_B2",
    )

    # EXP-B3 & DS-B3 (orphan: no performer, no owner, INS-B has no custodian)
    gw.query(
        """
        MATCH (inst:Instrument {id: 'INS-B'})
        MERGE (exp:Experiment {id: 'EXP-B3'})
        ON CREATE SET exp.title = 'High-Temperature Stress Run August'
        MERGE (u:InstrumentUsage {id: 'USG-B3'})
        ON CREATE SET
            u.used_from = $uf, u.used_until = $uu, u.required_scope = 'THERMAL_PROFILE'
        MERGE (exp)-[:HAS_USAGE]->(u)
        MERGE (u)-[:OF_INSTRUMENT]->(inst)
        MERGE (ds:Dataset {id: 'DS-B3'})
        ON CREATE SET ds.name = 'High Temp Stress Data August', ds.status = 'ACTIVE'
        MERGE (exp)-[:PRODUCED]->(ds)
        MERGE (ds4:Dataset {id: 'DS-B4'})
        ON CREATE SET ds4.name = 'Degradation Rate August', ds4.status = 'ACTIVE'
        MERGE (ds4)-[:DERIVED_FROM]->(ds)
        """,
        {"uf": t_b3_used_f, "uu": t_b3_used_u},
        name="SEED_EXP_B3_ORPHAN",
    )

    # Report RPT-3 uses DS-B2 and DS-B4
    gw.query(
        """
        MERGE (r3:Report {id: 'RPT-3'})
        ON CREATE SET r3.title = 'Thermal Formulations Summary', r3.publication_status = 'DRAFT'
        WITH r3
        MATCH (d2:Dataset {id: 'DS-B2'}), (d4:Dataset {id: 'DS-B4'})
        MERGE (r3)-[:USES_DATASET]->(d2)
        MERGE (r3)-[:USES_DATASET]->(d4)
        """,
        {},
        name="SEED_RPT_3",
    )

    # Summary count
    node_count_rows = gw.query("MATCH (n) RETURN count(n) AS c", {}, name="COUNT_SEEDED")
    count = node_count_rows[0]["c"] if node_count_rows else 0
    return {"total_nodes": count}


if __name__ == "__main__":
    print("Seeding Temproven database with synthetic records...")
    gw = GraphGateway()
    try:
        res = seed_graph(gw, clear_existing=True)
        print(f"Seed complete. Graph has {res['total_nodes']} nodes.")
    finally:
        gw.close()
