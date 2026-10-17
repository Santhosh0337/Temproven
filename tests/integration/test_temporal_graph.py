"""
tests/integration/test_temporal_graph.py
────────────────────────────────────────
Integration tests for bitemporal coverage queries and edge cases
against a real local FalkorDB instance using isolated throwaway graphs.
"""

import uuid
import pytest
import falkordb

from temproven import config
from temproven.domain.temporal import (
    CoverageStatus,
    evaluate_usage_coverage,
    parse_iso_to_epoch,
)
from temproven.graph.gateway import GraphGateway
from temproven.graph.schema import apply_schema
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


def test_seed_and_act1_evaluations(test_gw):
    """
    On seeded base data, prior to the correction:
    - EXP-A1 (March) is COVERED (single)
    - EXP-A2 (May) is COVERED (single)
    - EXP-A3 (June 15) is COVERED (single)
    - EXP-A4 (June 28) is COVERED (single)
    - EXP-B1 (March) is COVERED (single)
    - EXP-B2 (Overnight March 31 - April 1) is COVERED (chain)
    """
    seed_graph(test_gw, clear_existing=False)

    # Evaluate at Act 1 clock (before Act 2 supersession is recorded)
    # T = 2026-07-01T00:00:00Z (after experiments, before Oct correction)
    t_act1 = parse_iso_to_epoch("2026-07-01T00:00:00Z")

    results = {
        r.usage_id: r
        for r in evaluate_usage_coverage(test_gw, t=t_act1)
    }

    # All Act 1 usages on INS-A are COVERED by CERT-A1 v1
    assert results["USG-A1"].status == CoverageStatus.COVERED_SINGLE
    assert results["USG-A2"].status == CoverageStatus.COVERED_SINGLE
    assert results["USG-A3"].status == CoverageStatus.COVERED_SINGLE
    assert results["USG-A4"].status == CoverageStatus.COVERED_SINGLE

    # INS-B usages:
    # USG-B1 is covered by CERT-B1
    assert results["USG-B1"].status == CoverageStatus.COVERED_SINGLE
    # USG-B2 spans 2026-04-01 and is covered by the CHAIN [CERT-B1, CERT-B2]
    assert results["USG-B2"].status == CoverageStatus.COVERED_CHAIN
    assert results["USG-B2"].is_allowed is True


def test_bitemporal_supersession_before_and_after_t(test_gw):
    """
    BITEMPORAL RECONSTRUCTION TEST:
    With v1 recorded earlier, and v2 (authoritative, recorded t2) superseding it:
    - At T = t2 - 1 second: evaluation uses v1 -> USG-A3 is COVERED
    - At T = t2: evaluation uses v2 -> USG-A3 is NOT_COVERED
    - This holds even when v1's cached status is marked 'SUPERSEDED'
    """
    seed_graph(test_gw, clear_existing=False)

    # Insert authoritative superseding claim CLAIM-A1-v2 recorded at t2 = 1792227600 (2026-10-17T09:00:00Z)
    t2 = parse_iso_to_epoch("2026-10-17T09:00:00Z")
    t_v2_end = parse_iso_to_epoch("2026-06-01T00:00:00Z")  # Shortened validity

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
            summary: 'Authoritative calibration correction: drift after June 1'
        }),
        (ev2:Evidence {
            id: 'EV-A1-v2',
            source_type: 'CALIBRATION_AUTHORITY',
            trust_level: 'AUTHORITATIVE',
            observed_at: $t2,
            recorded_at: $t2,
            ref: 'CORR-2026-NMI-099',
            description: 'Recalibration audit shows drift began 2026-06-01'
        }),
        (c2)-[:SUPPORTED_BY]->(ev2),
        (cert)-[:HAS_VALIDITY]->(c2),
        (c2)-[:SUPERSEDES {recorded_at: $t2, reason: 'Drift detected'}]->(c1)
        SET c1.status = 'SUPERSEDED'
        """,
        {"t2": t2, "t_v2_end": t_v2_end},
        name="INSERT_SUPERSEDING_CLAIM",
    )

    # 1. As of T = t2 - 1 second (one second BEFORE correction was recorded)
    res_before = evaluate_usage_coverage(test_gw, t=t2 - 1, usage_id="USG-A3")
    assert len(res_before) == 1
    assert res_before[0].status == CoverageStatus.COVERED_SINGLE
    assert res_before[0].is_allowed is True

    # 2. As of T = t2 (the exact instant correction is recorded)
    res_after = evaluate_usage_coverage(test_gw, t=t2, usage_id="USG-A3")
    assert len(res_after) == 1
    assert res_after[0].status == CoverageStatus.NOT_COVERED
    assert res_after[0].is_allowed is False
    assert len(res_after[0].uncovered_gaps) > 0

    # EXP-A1 (March) and EXP-A2 (May) remain COVERED even under v2
    res_a1 = evaluate_usage_coverage(test_gw, t=t2, usage_id="USG-A1")[0]
    res_a2 = evaluate_usage_coverage(test_gw, t=t2, usage_id="USG-A2")[0]
    assert res_a1.status == CoverageStatus.COVERED_SINGLE
    assert res_a2.status == CoverageStatus.COVERED_SINGLE


def test_bitemporal_cache_independence(test_gw):
    """
    Deliberately corrupt the cached `status` property of claims in the database.
    Historical evaluations must NEVER read or filter on `status`, so results
    must remain completely unchanged.
    """
    seed_graph(test_gw, clear_existing=False)

    # Corrupt status property
    test_gw.query("MATCH (c:Claim) SET c.status = 'CORRUPTED_GARBAGE'", {}, name="CORRUPT_STATUS")

    t_eval = parse_iso_to_epoch("2026-05-01T00:00:00Z")
    res = evaluate_usage_coverage(test_gw, t=t_eval, usage_id="USG-A1")
    assert len(res) == 1
    assert res[0].status == CoverageStatus.COVERED_SINGLE


def test_bitemporal_unverified_claim_not_effective_until_verified(test_gw):
    """
    A claim whose verified_at is later than T is not effective at T,
    even if recorded_at <= T.
    """
    test_gw.query(
        """
        CREATE (inst:Instrument {id: 'INS-TEST', name: 'Test', kind: 'test'}),
               (cert:CalibrationCertificate {id: 'CERT-TEST', scope: 'TEST_SCOPE', recorded_at: 100}),
               (cert)-[:CERTIFIES]->(inst),
               (c:Claim {
                   id: 'CLAIM-TEST', claim_type: 'CERT_VALIDITY', subject_id: 'CERT-TEST',
                   valid_from: 1000, valid_until: 5000,
                   recorded_at: 100, verified_at: 500, status: 'VERIFIED'
               }),
               (cert)-[:HAS_VALIDITY]->(c),
               (exp:Experiment {id: 'EXP-TEST'}),
               (u:InstrumentUsage {id: 'USG-TEST', used_from: 2000, used_until: 3000, required_scope: 'TEST_SCOPE'}),
               (exp)-[:HAS_USAGE]->(u),
               (u)-[:OF_INSTRUMENT]->(inst)
        """,
        {},
        name="SEED_UNVERIFIED_GAP",
    )

    # At T = 300: recorded_at (100) <= 300, but verified_at (500) > 300 -> NOT effective!
    res_before_verification = evaluate_usage_coverage(test_gw, t=300, usage_id="USG-TEST")
    assert res_before_verification[0].status == CoverageStatus.NOT_COVERED

    # At T = 500: verified_at <= 500 -> effective!
    res_after_verification = evaluate_usage_coverage(test_gw, t=500, usage_id="USG-TEST")
    assert res_after_verification[0].status == CoverageStatus.COVERED_SINGLE


def test_bitemporal_retroactive_revocation(test_gw):
    """
    Revocation recorded at t3 = 1000 with retroactive effective_at e = 500.
    At T = t3 - 1 (999): certificate covers usage at 600.
    At T = t3 (1000): usage after e=500 is NOT_COVERED, usage before e remains COVERED.
    """
    test_gw.query(
        """
        CREATE (inst:Instrument {id: 'INS-REV', name: 'Test', kind: 'test'}),
               (cert:CalibrationCertificate {id: 'CERT-REV', scope: 'TEST_SCOPE', recorded_at: 100}),
               (cert)-[:CERTIFIES]->(inst),
               (c:Claim {
                   id: 'CLAIM-REV', claim_type: 'CERT_VALIDITY', subject_id: 'CERT-REV',
                   valid_from: 100, valid_until: 2000,
                   recorded_at: 100, verified_at: 100, status: 'VERIFIED'
               }),
               (cert)-[:HAS_VALIDITY]->(c),
               (exp:Experiment {id: 'EXP-REV'}),
               (u_early:InstrumentUsage {id: 'USG-EARLY', used_from: 200, used_until: 400, required_scope: 'TEST_SCOPE'}),
               (u_late:InstrumentUsage {id: 'USG-LATE', used_from: 600, used_until: 800, required_scope: 'TEST_SCOPE'}),
               (exp)-[:HAS_USAGE]->(u_early),
               (exp)-[:HAS_USAGE]->(u_late),
               (u_early)-[:OF_INSTRUMENT]->(inst),
               (u_late)-[:OF_INSTRUMENT]->(inst)
        """,
        {},
        name="SEED_REVOCATION_BASE",
    )

    t3 = 1000
    e = 500

    test_gw.query(
        """
        MATCH (cert:CalibrationCertificate {id: 'CERT-REV'})
        CREATE (rev:CertificateRevocation {
            id: 'REV-RETRO',
            effective_at: $e,
            recorded_at: $t3,
            reason: 'Contamination detected retroactively'
        }),
        (cert)-[:HAS_REVOCATION]->(rev)
        """,
        {"e": e, "t3": t3},
        name="ADD_RETROACTIVE_REVOCATION",
    )

    # 1. At T = t3 - 1 (before revocation is known): both usages covered
    res_early_pre = evaluate_usage_coverage(test_gw, t=t3 - 1, usage_id="USG-EARLY")[0]
    res_late_pre = evaluate_usage_coverage(test_gw, t=t3 - 1, usage_id="USG-LATE")[0]
    assert res_early_pre.status == CoverageStatus.COVERED_SINGLE
    assert res_late_pre.status == CoverageStatus.COVERED_SINGLE

    # 2. At T = t3 (revocation is known): early remains COVERED, late is NOT_COVERED
    res_early_post = evaluate_usage_coverage(test_gw, t=t3, usage_id="USG-EARLY")[0]
    res_late_post = evaluate_usage_coverage(test_gw, t=t3, usage_id="USG-LATE")[0]
    assert res_early_post.status == CoverageStatus.COVERED_SINGLE
    assert res_late_post.status == CoverageStatus.NOT_COVERED


def test_revocation_with_null_effective_at_is_full_revocation(test_gw):
    """
    Revocation with null effective_at:
    - Ignored before recorded_at
    - Full revocation at and after recorded_at
    """
    test_gw.query(
        """
        CREATE (inst:Instrument {id: 'INS-NULLREV', name: 'Test', kind: 'test'}),
               (cert:CalibrationCertificate {id: 'CERT-NULLREV', scope: 'SCOPE_X', recorded_at: 100}),
               (cert)-[:CERTIFIES]->(inst),
               (c:Claim {
                   id: 'CLAIM-NULLREV', claim_type: 'CERT_VALIDITY', subject_id: 'CERT-NULLREV',
                   valid_from: 100, valid_until: 2000,
                   recorded_at: 100, verified_at: 100
               }),
               (cert)-[:HAS_VALIDITY]->(c),
               (exp:Experiment {id: 'EXP-NULLREV'}),
               (u:InstrumentUsage {id: 'USG-NULLREV', used_from: 200, used_until: 400, required_scope: 'SCOPE_X'}),
               (exp)-[:HAS_USAGE]->(u),
               (u)-[:OF_INSTRUMENT]->(inst),
               (rev:CertificateRevocation {id: 'REV-NULL', recorded_at: 1000}),
               (cert)-[:HAS_REVOCATION]->(rev)
        """,
        {},
        name="SEED_NULL_REVOCATION",
    )

    # Before t=1000: covered
    res_pre = evaluate_usage_coverage(test_gw, t=999, usage_id="USG-NULLREV")[0]
    assert res_pre.status == CoverageStatus.COVERED_SINGLE

    # At t=1000: fully revoked -> not covered
    res_post = evaluate_usage_coverage(test_gw, t=1000, usage_id="USG-NULLREV")[0]
    assert res_post.status == CoverageStatus.NOT_COVERED


def test_scope_mismatch_yields_not_covered(test_gw):
    """If certificate scope does not match usage required_scope, it is not applicable."""
    test_gw.query(
        """
        CREATE (inst:Instrument {id: 'INS-SCOPE', name: 'Scope Inst', kind: 'test'}),
               (cert:CalibrationCertificate {id: 'CERT-SCOPE', scope: 'SCOPE_A', recorded_at: 100}),
               (cert)-[:CERTIFIES]->(inst),
               (c:Claim {
                   id: 'CLAIM-SCOPE', claim_type: 'CERT_VALIDITY', subject_id: 'CERT-SCOPE',
                   valid_from: 100, valid_until: 5000,
                   recorded_at: 100, verified_at: 100
               }),
               (cert)-[:HAS_VALIDITY]->(c),
               (exp:Experiment {id: 'EXP-SCOPE'}),
               (u:InstrumentUsage {id: 'USG-SCOPE', used_from: 200, used_until: 400, required_scope: 'SCOPE_B_MISMATCH'}),
               (exp)-[:HAS_USAGE]->(u),
               (u)-[:OF_INSTRUMENT]->(inst)
        """,
        {},
        name="SEED_SCOPE_MISMATCH",
    )
    res = evaluate_usage_coverage(test_gw, t=1000, usage_id="USG-SCOPE")[0]
    assert res.status == CoverageStatus.NOT_COVERED
