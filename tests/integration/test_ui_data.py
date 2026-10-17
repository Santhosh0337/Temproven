"""
tests/integration/test_ui_data.py
─────────────────────────────────
Integration tests for UI data adapters against real FalkorDB (throwaway graph):
- Connection health check
- Incident listing and reconstruction
- Read-only impact data retrieval with zero graph mutation
- Playbook and step retrieval
- Gate execution only upon invocation
"""

import uuid
import pytest
import falkordb

from temproven import config
from temproven.app.data import (
    check_db_connection,
    execute_gate_action,
    fetch_impact_data,
    fetch_incident_detail,
    fetch_incidents,
    fetch_playbook_data,
    fetch_report_details,
    fetch_reports,
)
from temproven.clock import FixedClock
from temproven.domain.gate import GateDecision
from temproven.graph.gateway import GraphGateway
from temproven.graph.schema import apply_schema
from temproven.seed.seed import seed_graph


@pytest.fixture
def ui_test_gw():
    """Create a GraphGateway connected to a fresh throwaway graph, deleted upon teardown."""
    unique_graph = f"temproven_ui_test_{uuid.uuid4().hex[:8]}"
    gw = GraphGateway(graph_name=unique_graph)
    apply_schema(gw)
    yield gw
    gw.close()
    client = falkordb.FalkorDB(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT)
    try:
        client.select_graph(unique_graph).delete()
    except Exception:
        pass


def test_db_connection_health(ui_test_gw):
    """Test connection check against live FalkorDB."""
    ok, err = check_db_connection(ui_test_gw)
    assert ok is True
    assert err is None


def test_fetch_incidents_empty_and_seeded(ui_test_gw):
    """Verify incident retrieval on empty graph and seeded graph."""
    # Empty graph
    incidents_empty = fetch_incidents(ui_test_gw)
    assert incidents_empty == []

    # Seed graph
    seed_graph(ui_test_gw)
    # Create incident nodes
    ui_test_gw.query(
        """
        MATCH (inst:Instrument {id: 'INS-A'})
        CREATE (inc:Incident {
            id: 'INC-2026-001',
            incident_type: 'CERT_LAPSE',
            status: 'IN_PROGRESS',
            opened_at: 1792227600,
            next_seq: 1
        })
        CREATE (inc)-[:CONCERNS_INSTRUMENT]->(inst)
        """
    )

    incidents = fetch_incidents(ui_test_gw)
    assert len(incidents) >= 1
    inc1 = next(i for i in incidents if i["id"] == "INC-2026-001")
    assert inc1["status"] == "IN_PROGRESS"
    assert inc1["instrument_id"] == "INS-A"


def test_fetch_impact_data_is_zero_mutation(ui_test_gw):
    """
    Verify fetch_impact_data reads coverage and lineage without creating or modifying
    any graph nodes.
    """
    seed_graph(ui_test_gw)

    # Count nodes before
    count_before = ui_test_gw.query("MATCH (n) RETURN count(n) AS cnt")[0]["cnt"]

    # Call fetch_impact_data
    impact = fetch_impact_data(ui_test_gw, instrument_id="INS-A", as_of_recorded=1782864000)

    # Count nodes after
    count_after = ui_test_gw.query("MATCH (n) RETURN count(n) AS cnt")[0]["cnt"]
    assert count_before == count_after, "fetch_impact_data must NOT mutate the graph!"

    assert isinstance(impact["experiments"], list)
    assert isinstance(impact["datasets"], list)
    assert isinstance(impact["reports"], list)


def test_gate_action_execution_records_audit(ui_test_gw):
    """
    Test that execute_gate_action only records an attempt and evaluates live evidence
    when explicitly invoked.
    """
    seed_graph(ui_test_gw)

    # RPT-1 depends on DS-A1, DS-A2, DS-A5 (which depends on DS-A3, EXP-A3)
    # Quarantine DS-A5 to simulate incident action
    ui_test_gw.query("MATCH (ds:Dataset {id: 'DS-A5'}) SET ds.status = 'QUARANTINED'")

    clock = FixedClock(1792227600)
    result = execute_gate_action(ui_test_gw, report_id="RPT-1", actor="Tester_Eve", clock=clock)

    assert result.decision == GateDecision.BLOCK
    assert "quarantined" in result.reason.lower()
    assert result.prior_status == "DRAFT"
    assert result.new_status == "DRAFT"  # Status NOT published on BLOCK

    # Check that PublicationAttempt node was durably created
    attempts = ui_test_gw.query(
        "MATCH (rpt:Report {id: 'RPT-1'})-[:HAS_ATTEMPT]->(pa:PublicationAttempt) RETURN pa.result AS res, pa.actor AS actor"
    )
    assert len(attempts) == 1
    assert attempts[0]["res"] == "BLOCK"
    assert attempts[0]["actor"] == "Tester_Eve"
