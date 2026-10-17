"""
tests/integration/test_semantic_memory.py
─────────────────────────────────────────
Integration tests for Semantic Memory against real FalkorDB:
- Belief change explanation before and after supersession with real coverage effects
- Unverified contradiction policy enforcement (never auto-quarantines)
- Cache-corruption resilience (corrupting cached status has zero effect)
- Provenance enforcement (flagging claims lacking evidence)
"""

import uuid
import pytest
import falkordb

from temproven import config
from temproven.domain.actions import ActionService
from temproven.domain.temporal import parse_iso_to_epoch
from temproven.graph.gateway import GraphGateway
from temproven.graph.schema import apply_schema
from temproven.memory.semantic import explain_belief_change
from temproven.seed.seed import seed_graph


@pytest.fixture
def test_gw():
    """Create a GraphGateway connected to a fresh throwaway graph, deleted upon teardown."""
    unique_graph = f"temproven_test_{uuid.uuid4().hex[:8]}"
    gw = GraphGateway(graph_name=unique_graph)
    apply_schema(gw)
    yield gw
    gw.close()
    client = falkordb.FalkorDB(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT)
    try:
        client.select_graph(unique_graph).delete()
    except Exception:
        pass


def test_belief_change_before_and_after_supersession(test_gw):
    """
    Test explain_belief_change before supersession (Act 1 time) and after
    authoritative correction supersession (Act 2 time).
    Verifies valid vs knowledge times and real coverage effects.
    """
    seed_graph(test_gw, clear_existing=False)

    t_act1 = parse_iso_to_epoch("2026-07-01T00:00:00Z")
    t_act2 = parse_iso_to_epoch("2026-10-17T09:00:00Z")
    t_v2_end = parse_iso_to_epoch("2026-06-01T00:00:00Z")

    # 1. Before supersession: Act 1 time
    exp_before = explain_belief_change(test_gw, "CERT-A1", as_of_recorded=t_act1)
    assert exp_before.change_kind == "NO_CHANGE"
    assert exp_before.current_belief is not None
    assert exp_before.current_belief.claim_id == "CLAIM-A1-v1"
    assert exp_before.current_belief.is_effective is True
    assert exp_before.previous_belief is None
    assert exp_before.coverage_effects == []

    # 2. Ingest authoritative supersession at Act 2 time
    test_gw.query(
        """
        MATCH (cert:CalibrationCertificate {id: 'CERT-A1'}), (c1:Claim {id: 'CLAIM-A1-v1'})
        CREATE (c2:Claim {
            id: 'CLAIM-A1-v2',
            claim_type: 'CERT_VALIDITY',
            subject_id: 'CERT-A1',
            valid_from: c1.valid_from,
            valid_until: $t_v2_end,
            recorded_at: $t2,
            verified_at: $t2,
            status: 'VERIFIED',
            summary: 'Validity shortened to 2026-06-01'
        }),
        (ev2:Evidence {
            id: 'EV-A1-v2',
            source_type: 'CALIBRATION_AUTHORITY',
            trust_level: 'AUTHORITATIVE',
            observed_at: $t2,
            recorded_at: $t2,
            ref: 'CORR-2026-NMI-099',
            description: 'Sensor drift after 2026-06-01'
        }),
        (c2)-[:SUPPORTED_BY]->(ev2),
        (cert)-[:HAS_VALIDITY]->(c2),
        (c2)-[:SUPERSEDES {recorded_at: $t2, reason: 'Drift detected'}]->(c1)
        SET c1.status = 'SUPERSEDED'
        """,
        {"t2": t_act2, "t_v2_end": t_v2_end},
    )

    # 3. After supersession: Act 2 time
    exp_after = explain_belief_change(test_gw, "CERT-A1", as_of_recorded=t_act2)
    assert exp_after.change_kind == "SUPERSEDED"
    assert exp_after.previous_belief is not None
    assert exp_after.previous_belief.claim_id == "CLAIM-A1-v1"
    assert exp_after.previous_belief.is_effective is False
    assert exp_after.current_belief is not None
    assert exp_after.current_belief.claim_id == "CLAIM-A1-v2"
    assert exp_after.current_belief.is_effective is True
    assert exp_after.learned_at == t_act2
    assert exp_after.applies_until == t_v2_end

    # Check real coverage effects: EXP-A3 and EXP-A4 usages were affected
    affected_usages = {eff.usage_id: (eff.status_before, eff.status_after) for eff in exp_after.coverage_effects}
    assert "USG-A3" in affected_usages
    assert affected_usages["USG-A3"] == ("COVERED (single)", "NOT_COVERED")
    assert "USG-A4" in affected_usages
    assert affected_usages["USG-A4"] == ("COVERED (single)", "NOT_COVERED")

    # Render text test
    rendered = exp_after.render_text()
    assert "SUPERSEDED" in rendered
    assert "CLAIM-A1-v2" in rendered
    assert "USG-A3" in rendered


def test_unverified_contradiction_policy_enforcement(test_gw):
    """
    Unverified contradiction creates CONTRADICTS with UNRESOLVED resolution.
    Belief explanation reports UNRESOLVED_CONTRADICTION.
    Zero datasets quarantined, zero report holds, datasets remain ACTIVE.
    """
    seed_graph(test_gw, clear_existing=False)

    t_contra = parse_iso_to_epoch("2026-08-01T00:00:00Z")
    # Ingest unverified contradictory claim
    test_gw.query(
        """
        MATCH (cert:CalibrationCertificate {id: 'CERT-A1'}), (c1:Claim {id: 'CLAIM-A1-v1'})
        CREATE (c_contra:Claim {
            id: 'CLAIM-A1-unverified',
            claim_type: 'CERT_VALIDITY',
            subject_id: 'CERT-A1',
            valid_from: 1767225600,
            valid_until: 1772323200,
            recorded_at: $t_contra,
            verified_at: null,
            status: 'UNVERIFIED',
            summary: 'Anonymous tip claiming laser failed early'
        }),
        (ev_unv:Evidence {
            id: 'EV-UNVERIFIED-1',
            source_type: 'ANONYMOUS_TIP',
            trust_level: 'UNVERIFIED',
            observed_at: $t_contra,
            recorded_at: $t_contra
        }),
        (c_contra)-[:SUPPORTED_BY]->(ev_unv),
        (cert)-[:HAS_VALIDITY]->(c_contra),
        (c_contra)-[:CONTRADICTS {detected_at: $t_contra, reason: 'Anonymous contradictory tip', resolution: 'UNRESOLVED'}]->(c1)
        """,
        {"t_contra": t_contra},
    )

    exp = explain_belief_change(test_gw, "CERT-A1", as_of_recorded=t_contra)
    assert exp.change_kind == "UNRESOLVED_CONTRADICTION"
    assert exp.rule_id == "unverified-contradiction-never-overrides"
    # Current belief remains the verified claim
    assert exp.current_belief is not None
    assert exp.current_belief.claim_id == "CLAIM-A1-v1"
    assert exp.current_belief.is_effective is True

    # Assert no datasets were quarantined
    ds_rows = test_gw.query("MATCH (ds:Dataset) WHERE ds.status = 'QUARANTINED' RETURN count(ds) AS cnt")
    assert ds_rows[0]["cnt"] == 0

    # Datasets remain ACTIVE
    active_rows = test_gw.query("MATCH (ds:Dataset) WHERE ds.status = 'ACTIVE' RETURN count(ds) AS cnt")
    assert active_rows[0]["cnt"] > 0


def test_cache_corruption_resilience(test_gw):
    """
    Corrupting cached Claim.status property values does not break or alter
    bitemporal belief change explanations.
    """
    seed_graph(test_gw, clear_existing=False)

    t_act2 = parse_iso_to_epoch("2026-10-17T09:00:00Z")
    t_v2_end = parse_iso_to_epoch("2026-06-01T00:00:00Z")

    # Ingest superseding claim
    test_gw.query(
        """
        MATCH (cert:CalibrationCertificate {id: 'CERT-A1'}), (c1:Claim {id: 'CLAIM-A1-v1'})
        CREATE (c2:Claim {
            id: 'CLAIM-A1-v2',
            claim_type: 'CERT_VALIDITY',
            subject_id: 'CERT-A1',
            valid_from: c1.valid_from,
            valid_until: $t_v2_end,
            recorded_at: $t2,
            verified_at: $t2,
            status: 'VERIFIED',
            summary: 'Validity shortened'
        }),
        (ev2:Evidence {
            id: 'EV-A1-v2',
            source_type: 'CALIBRATION_AUTHORITY',
            trust_level: 'AUTHORITATIVE',
            observed_at: $t2,
            recorded_at: $t2
        }),
        (c2)-[:SUPPORTED_BY]->(ev2),
        (cert)-[:HAS_VALIDITY]->(c2),
        (c2)-[:SUPERSEDES {recorded_at: $t2, reason: 'Drift'}]->(c1)
        SET c1.status = 'SUPERSEDED'
        """,
        {"t2": t_act2, "t_v2_end": t_v2_end},
    )

    # Corrupt cached status values intentionally
    test_gw.query("MATCH (c:Claim) SET c.status = 'COMPLETELY_CORRUPTED_GARBAGE'")

    # Explanation must still correctly determine SUPERSEDED
    exp = explain_belief_change(test_gw, "CERT-A1", as_of_recorded=t_act2)
    assert exp.change_kind == "SUPERSEDED"
    assert exp.previous_belief.claim_id == "CLAIM-A1-v1"
    assert exp.current_belief.claim_id == "CLAIM-A1-v2"
    assert exp.current_belief.is_effective is True


def test_claim_without_provenance_flagged(test_gw):
    """A claim lacking supporting Evidence relationship is flagged NO_PROVENANCE."""
    test_gw.query(
        """
        CREATE (cert:CalibrationCertificate {id: 'CERT-UNPROVEN'}),
               (c:Claim {
                   id: 'CLAIM-NO-PROV',
                   claim_type: 'CERT_VALIDITY',
                   subject_id: 'CERT-UNPROVEN',
                   valid_from: 1000,
                   valid_until: 2000,
                   recorded_at: 1000,
                   verified_at: 1000
               }),
               (cert)-[:HAS_VALIDITY]->(c)
        """
    )

    exp = explain_belief_change(test_gw, "CERT-UNPROVEN", as_of_recorded=1500)
    assert exp.change_kind == "NO_PROVENANCE"
    assert exp.rule_id == "provenance-check-failed"
