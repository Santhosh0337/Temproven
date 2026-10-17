"""
tests/unit/test_temporal.py
───────────────────────────
Pure unit tests for interval arithmetic, timezone normalization,
and classify_coverage() logic (no database required).
"""

import pytest
from datetime import timezone

from temproven.domain.temporal import (
    CoverageStatus,
    classify_coverage,
    compute_uncovered_gaps,
    epoch_to_iso,
    merge_intervals,
    parse_iso_to_epoch,
)


# ── Timezone normalization ──────────────────────────────────────────────────

def test_timezone_normalization_offset_matches_utc():
    """Offset +05:30 (IST) and UTC Z for the same instant yield identical epoch."""
    # 15:30 IST is 10:00 UTC
    epoch_ist = parse_iso_to_epoch("2026-03-10T15:30:00+05:30")
    epoch_utc = parse_iso_to_epoch("2026-03-10T10:00:00Z")
    assert epoch_ist == epoch_utc
    assert epoch_ist == 1773136800
    assert epoch_to_iso(epoch_ist) == "2026-03-10T10:00:00Z"


def test_timezone_normalization_naive_assumed_utc():
    """Naive string assumed UTC without offset alteration."""
    epoch = parse_iso_to_epoch("2026-01-01T00:00:00")
    epoch_z = parse_iso_to_epoch("2026-01-01T00:00:00Z")
    assert epoch == epoch_z


# ── Interval merging & gap computation ──────────────────────────────────────

def test_merge_intervals_adjacent_and_overlapping():
    """Half-open intervals [10, 20) and [20, 30) touch and merge to [10, 30)."""
    merged = merge_intervals([(10, 20), (20, 30)])
    assert merged == [(10, 30)]

    merged_overlap = merge_intervals([(10, 25), (20, 35)])
    assert merged_overlap == [(10, 35)]


def test_merge_intervals_with_gap():
    """Intervals [10, 20) and [22, 30) have gap [20, 22) and must not merge."""
    merged = merge_intervals([(10, 20), (22, 30)])
    assert merged == [(10, 20), (22, 30)]


def test_compute_uncovered_gaps():
    """Gaps inside and outside coverage are accurately reported."""
    # Usage [15, 28) with validity [10, 20) and [24, 30)
    # Gaps should be [20, 24)
    gaps = compute_uncovered_gaps(15, 28, [(10, 20), (24, 30)])
    assert gaps == [(20, 24)]

    # Usage [5, 35) with validity [10, 20)
    # Gaps should be [5, 10) and [20, 35)
    gaps2 = compute_uncovered_gaps(5, 35, [(10, 20)])
    assert gaps2 == [(5, 10), (20, 35)]


# ── Boundary conditions in classify_coverage() ──────────────────────────────

def test_boundary_usage_ending_exactly_at_valid_until():
    """
    Usage [10, 20) against valid [10, 20).
    Boundary check: used_until <= valid_until (20 <= 20) is TRUE.
    Outcome must be COVERED (single).
    """
    usage = {"usage_id": "U1", "used_from": 10, "used_until": 20}
    candidates = [{
        "usage_id": "U1",
        "cert_id": "C1",
        "claim_id": "K1",
        "valid_from": 10,
        "valid_until": 20,
        "claim_recorded_at": 1,
        "claim_verified_at": 1,
        "num_superseders": 0,
        "revocations": [],
        "contradictions": [],
    }]
    res = classify_coverage(usage, candidates, as_of_recorded=5)
    assert res.status == CoverageStatus.COVERED_SINGLE
    assert res.is_allowed is True
    assert res.uncovered_gaps == []


def test_boundary_usage_starting_exactly_at_valid_until():
    """
    Usage [20, 30) against valid [10, 20).
    Half-open interval [10, 20) does not include 20.
    Outcome must be NOT_COVERED with gap [(20, 30)].
    """
    usage = {"usage_id": "U2", "used_from": 20, "used_until": 30}
    candidates = [{
        "usage_id": "U2",
        "cert_id": "C1",
        "claim_id": "K1",
        "valid_from": 10,
        "valid_until": 20,
        "claim_recorded_at": 1,
        "claim_verified_at": 1,
        "num_superseders": 0,
        "revocations": [],
        "contradictions": [],
    }]
    res = classify_coverage(usage, candidates, as_of_recorded=5)
    assert res.status == CoverageStatus.NOT_COVERED
    assert res.is_allowed is False
    assert res.uncovered_gaps == [(20, 30)]


def test_chained_coverage_across_adjacent_certificates():
    """
    Usage [15, 25) spanning boundary between CERT-B1 [10, 20) and CERT-B2 [20, 30).
    Under half-open semantics, intervals touch at 20 and form [10, 30).
    Outcome must be COVERED (chain).
    """
    usage = {"usage_id": "U-CHAIN", "used_from": 15, "used_until": 25}
    candidates = [
        {
            "usage_id": "U-CHAIN",
            "cert_id": "CERT-B1",
            "claim_id": "K-B1",
            "valid_from": 10,
            "valid_until": 20,
            "claim_recorded_at": 1,
            "claim_verified_at": 1,
            "num_superseders": 0,
            "revocations": [],
            "contradictions": [],
        },
        {
            "usage_id": "U-CHAIN",
            "cert_id": "CERT-B2",
            "claim_id": "K-B2",
            "valid_from": 20,
            "valid_until": 30,
            "claim_recorded_at": 1,
            "claim_verified_at": 1,
            "num_superseders": 0,
            "revocations": [],
            "contradictions": [],
        },
    ]
    res = classify_coverage(usage, candidates, as_of_recorded=5)
    assert res.status == CoverageStatus.COVERED_CHAIN
    assert res.is_allowed is True
    assert res.uncovered_gaps == []


def test_chain_with_internal_gap_is_not_covered():
    """
    Cert 1: [10, 20), Cert 2: [22, 30) -> gap is [20, 22).
    Usage [15, 25) spans the gap. Outcome must be NOT_COVERED with gap [(20, 22)].
    """
    usage = {"usage_id": "U-GAP", "used_from": 15, "used_until": 25}
    candidates = [
        {
            "usage_id": "U-GAP",
            "cert_id": "CERT-1",
            "claim_id": "K1",
            "valid_from": 10,
            "valid_until": 20,
            "claim_recorded_at": 1,
            "claim_verified_at": 1,
            "num_superseders": 0,
            "revocations": [],
            "contradictions": [],
        },
        {
            "usage_id": "U-GAP",
            "cert_id": "CERT-2",
            "claim_id": "K2",
            "valid_from": 22,
            "valid_until": 30,
            "claim_recorded_at": 1,
            "claim_verified_at": 1,
            "num_superseders": 0,
            "revocations": [],
            "contradictions": [],
        },
    ]
    res = classify_coverage(usage, candidates, as_of_recorded=5)
    assert res.status == CoverageStatus.NOT_COVERED
    assert res.is_allowed is False
    assert res.uncovered_gaps == [(20, 22)]


# ── Invalid & missing data edge cases ────────────────────────────────────────

def test_null_dates_yield_unknown():
    """Missing usage or validity timestamps yields UNKNOWN, never assumed valid."""
    # 1. Null usage dates
    res1 = classify_coverage({"usage_id": "U-NULL", "used_from": None, "used_until": 20}, [])
    assert res1.status == CoverageStatus.UNKNOWN

    # 2. Null validity dates on claim
    usage = {"usage_id": "U-NULL-VAL", "used_from": 10, "used_until": 20}
    candidates = [{
        "usage_id": "U-NULL-VAL",
        "cert_id": "C1",
        "claim_id": "K1",
        "valid_from": None,
        "valid_until": 20,
        "claim_recorded_at": 1,
        "claim_verified_at": 1,
        "num_superseders": 0,
        "revocations": [],
        "contradictions": [],
    }]
    res2 = classify_coverage(usage, candidates, as_of_recorded=5)
    assert res2.status == CoverageStatus.UNKNOWN


def test_zero_length_and_inverted_usage_yield_unknown():
    """Zero-length usage [10, 10) or inverted [20, 10) is invalid data -> UNKNOWN."""
    res_zero = classify_coverage({"usage_id": "UZ", "used_from": 10, "used_until": 10}, [])
    assert res_zero.status == CoverageStatus.UNKNOWN

    res_inv = classify_coverage({"usage_id": "UI", "used_from": 20, "used_until": 10}, [])
    assert res_inv.status == CoverageStatus.UNKNOWN


# ── Conflicts and Revocations ────────────────────────────────────────────────

def test_multiple_effective_claims_on_same_cert_is_conflicting():
    """Two un-superseded claims on the same certificate is a data error -> CONFLICTING."""
    usage = {"usage_id": "U-CONF", "used_from": 15, "used_until": 25}
    candidates = [
        {
            "usage_id": "U-CONF",
            "cert_id": "C1",
            "claim_id": "K1-v1",
            "valid_from": 10,
            "valid_until": 30,
            "claim_recorded_at": 1,
            "claim_verified_at": 1,
            "num_superseders": 0,
            "revocations": [],
            "contradictions": [],
        },
        {
            "usage_id": "U-CONF",
            "cert_id": "C1",
            "claim_id": "K1-v2",
            "valid_from": 10,
            "valid_until": 30,
            "claim_recorded_at": 2,
            "claim_verified_at": 2,
            "num_superseders": 0,
            "revocations": [],
            "contradictions": [],
        },
    ]
    res = classify_coverage(usage, candidates, as_of_recorded=5)
    assert res.status == CoverageStatus.CONFLICTING


def test_unverified_contradiction_yields_conflicting():
    """Unverified contradicting evidence produces CONFLICTING, not silent override."""
    usage = {"usage_id": "U-CONTRA", "used_from": 15, "used_until": 25}
    candidates = [{
        "usage_id": "U-CONTRA",
        "cert_id": "C1",
        "claim_id": "K1",
        "valid_from": 10,
        "valid_until": 30,
        "claim_recorded_at": 1,
        "claim_verified_at": 1,
        "num_superseders": 0,
        "revocations": [],
        "contradictions": [{"id": "CONTRA-1", "recorded_at": 2, "verified_at": None}],
    }]
    res = classify_coverage(usage, candidates, as_of_recorded=5)
    assert res.status == CoverageStatus.CONFLICTING


def test_revocation_truncation_and_full_revocation():
    """Revocation with effective_at truncates end date; null effective_at is full revocation."""
    usage = {"usage_id": "U-REV", "used_from": 15, "used_until": 25}
    # 1. Truncating revocation at 20 makes validity [10, 20), so usage [15, 25) is NOT_COVERED [20, 25)
    candidates_trunc = [{
        "usage_id": "U-REV",
        "cert_id": "C1",
        "claim_id": "K1",
        "valid_from": 10,
        "valid_until": 30,
        "claim_recorded_at": 1,
        "claim_verified_at": 1,
        "num_superseders": 0,
        "revocations": [{"id": "REV-1", "effective_at": 20, "recorded_at": 2}],
        "contradictions": [],
    }]
    res_trunc = classify_coverage(usage, candidates_trunc, as_of_recorded=5)
    assert res_trunc.status == CoverageStatus.NOT_COVERED
    assert res_trunc.uncovered_gaps == [(20, 25)]

    # 2. Revocation with null effective_at -> full revocation -> contributes nothing
    candidates_full = [{
        "usage_id": "U-REV",
        "cert_id": "C1",
        "claim_id": "K1",
        "valid_from": 10,
        "valid_until": 30,
        "claim_recorded_at": 1,
        "claim_verified_at": 1,
        "num_superseders": 0,
        "revocations": [{"id": "REV-FULL", "effective_at": None, "recorded_at": 2}],
        "contradictions": [],
    }]
    res_full = classify_coverage(usage, candidates_full, as_of_recorded=5)
    assert res_full.status == CoverageStatus.NOT_COVERED
    assert res_full.uncovered_gaps == [(15, 25)]
