"""
tests/integration/test_metrics.py
─────────────────────────────────
Integration tests for Metrics & Learning Validation against real FalkorDB:
- Gateway counters equal persisted QUERY_EXECUTED events
- Adding an extra query changes the measured count by exactly 1
- compare_metrics returns zero deltas for identical inputs and exact deltas for known inputs
- Static check: metrics code contains no hardcoded improvement percentages
- DB unreachable raises DatabaseUnavailableError without fallback
"""

import inspect
import uuid
import pytest
import falkordb

from temproven import config
from temproven.clock import FixedClock
from temproven.context import RunContext, MetricsScope, QueryCategory
from temproven.graph import queries
from temproven.graph.gateway import GraphGateway, DatabaseUnavailableError
from temproven.graph.schema import apply_schema
from temproven.memory.episodic import EpisodicRecorder
from temproven.metrics import (
    IncidentMetricsData,
    compare_metrics,
    load_incident_metrics,
)


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


def test_gateway_counter_equals_persisted_query_executed_events(test_gw):
    """
    Test that the gateway domain_query_count matches the count of persisted
    QUERY_EXECUTED event nodes in the graph for that incident scope.
    """
    incident_id = "INC-METRICS-PROOF-1"
    clock = FixedClock(1000)
    metrics = MetricsScope()
    recorder = EpisodicRecorder()
    ctx = RunContext(incident_id=incident_id, actor_agent="ImpactAgent", clock=clock, metrics=metrics, recorder=recorder)

    test_gw.query("CREATE (i:Incident {id: $id, incident_type: 'CERT_LAPSE', status: 'IN_PROGRESS', opened_at: 1000, next_seq: 1})", {"id": incident_id})

    # Run 3 DOMAIN queries with ctx
    test_gw.query("MATCH (n) RETURN count(n) AS cnt", {}, name="COUNT_ALL_NODES_CUSTOM", category=QueryCategory.DOMAIN, ctx=ctx)
    test_gw.query("MATCH (n) RETURN count(n) AS cnt", {}, name="COUNT_ALL_NODES_CUSTOM_2", category=QueryCategory.DOMAIN, ctx=ctx)
    test_gw.query("MATCH (n) RETURN count(n) AS cnt", {}, name="COUNT_ALL_NODES_CUSTOM_3", category=QueryCategory.DOMAIN, ctx=ctx)

    assert metrics.domain_queries == 3

    # Query event count in graph
    event_rows = test_gw.query(
        "MATCH (e:Event {incident_id: $id, event_type: 'QUERY_EXECUTED'}) RETURN count(e) AS cnt",
        {"id": incident_id},
    )
    assert event_rows[0]["cnt"] == 3


def test_extra_query_increments_measured_count_by_exactly_one(test_gw):
    """
    Proves counts come strictly from real execution: adding 1 extra query
    increases the measured count by exactly 1.
    """
    incident_id = "INC-INCREMENT-1"
    clock = FixedClock(1000)
    metrics = MetricsScope()
    recorder = EpisodicRecorder()
    ctx = RunContext(incident_id=incident_id, actor_agent="ImpactAgent", clock=clock, metrics=metrics, recorder=recorder)

    test_gw.query("CREATE (i:Incident {id: $id, incident_type: 'CERT_LAPSE', status: 'IN_PROGRESS', opened_at: 1000, next_seq: 1})", {"id": incident_id})

    # Query 1
    test_gw.query("MATCH (n) RETURN count(n)", {}, name="Q1", category=QueryCategory.DOMAIN, ctx=ctx)
    assert metrics.domain_queries == 1

    # Deliberate extra query
    test_gw.query("MATCH (n) RETURN count(n)", {}, name="Q2_EXTRA", category=QueryCategory.DOMAIN, ctx=ctx)
    assert metrics.domain_queries == 2


def test_compare_metrics_zero_deltas_and_known_deltas():
    """compare_metrics returns zero deltas for identical inputs and exact deltas for known inputs."""
    m1 = IncidentMetricsData(
        incident_id="INC-1",
        mode="EXPLORATORY",
        domain_queries=15,
        bookkeeping_queries=20,
        steps_executed=7,
        steps_skipped=0,
        handoffs=5,
        elapsed_ms=100.0,
    )

    # Identical
    comp_same = compare_metrics(m1, m1)
    assert comp_same.domain_query_delta == 0
    assert comp_same.bookkeeping_delta == 0
    assert comp_same.steps_executed_delta == 0
    assert comp_same.steps_skipped_delta == 0
    assert comp_same.handoffs_delta == 0
    assert comp_same.elapsed_ms_delta == 0.0

    # Known difference
    m2 = IncidentMetricsData(
        incident_id="INC-2",
        mode="VERIFIED_REUSE",
        domain_queries=10,
        bookkeeping_queries=25,
        steps_executed=7,
        steps_skipped=4,
        handoffs=5,
        elapsed_ms=80.0,
    )
    comp_diff = compare_metrics(m1, m2)
    assert comp_diff.domain_query_delta == -5
    assert comp_diff.bookkeeping_delta == 5
    assert comp_diff.steps_skipped_delta == 4
    assert comp_diff.elapsed_ms_delta == -20.0
    assert comp_diff.domain_query_ratio == 0.67
    assert "honesty_note" in dir(comp_diff)


def test_no_hardcoded_improvement_numbers_in_metrics_module():
    """Static check: inspect source code of temproven.metrics to confirm no hardcoded percentage."""
    import temproven.metrics as met_mod
    src = inspect.getsource(met_mod)
    assert "30%" not in src
    assert "40%" not in src
    assert "50%" not in src
    assert "2x" not in src
    assert "3x" not in src


def test_database_unavailable_error_on_bad_connection(monkeypatch):
    """Unreachable DB raises DatabaseUnavailableError immediately with no fallback store."""
    monkeypatch.setattr(config, "FALKORDB_PORT", 65432)
    with pytest.raises(DatabaseUnavailableError):
        GraphGateway(graph_name="test_should_fail")

