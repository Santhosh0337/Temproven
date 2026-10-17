"""
temproven/graph/queries.py
──────────────────────────
ALL Cypher query strings live here as named constants.

RULES:
1. No string concatenation of user input. All variable data goes in the
   `params` dict passed to GraphGateway.query().
2. Every query constant name starts with Q_ so it is obviously a query.
3. The query is the single source of truth — schema.py, seed.py, tests,
   and the UI all import from here. Never duplicate query text.
4. No LLM-generated Cypher. Every query is written and reviewed by a human.
"""

# ── Connection verification & admin ──────────────────────────────────────────

Q_PING = "RETURN $msg AS pong"
"""
Trivial parameterized query used to verify the DB connection is live.
Parameters: {msg: str}
Returns: [{pong: str}]
"""

Q_COUNT_ALL_NODES = "MATCH (n) RETURN count(n) AS node_count"
"""Count total nodes in the current graph."""


# ── Bitemporal coverage queries ─────────────────────────────────────────────

Q_USAGE_COVERAGE_CANDIDATES = """
MATCH (exp:Experiment)-[:HAS_USAGE]->(u:InstrumentUsage)-[:OF_INSTRUMENT]->(inst:Instrument)
WHERE ($usage_id IS NULL OR u.id = $usage_id)
  AND ($experiment_id IS NULL OR exp.id = $experiment_id)
  AND ($instrument_id IS NULL OR inst.id = $instrument_id)
OPTIONAL MATCH (cert:CalibrationCertificate)-[:CERTIFIES]->(inst)
WHERE cert.recorded_at <= $t AND cert.scope = u.required_scope
OPTIONAL MATCH (cert)-[:HAS_VALIDITY]->(c:Claim)
WHERE c.claim_type = 'CERT_VALIDITY'
  AND c.recorded_at <= $t
  AND c.verified_at IS NOT NULL
  AND c.verified_at <= $t
OPTIONAL MATCH (s:Claim)-[:SUPERSEDES]->(c)
WHERE s.recorded_at <= $t
  AND s.verified_at IS NOT NULL
  AND s.verified_at <= $t
OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(ev:Evidence)
OPTIONAL MATCH (cert)-[:HAS_REVOCATION]->(rev:CertificateRevocation)
WHERE rev.recorded_at <= $t
OPTIONAL MATCH (c)-[:CONTRADICTS]-(contra:Claim)
WHERE contra.recorded_at <= $t
RETURN
    exp.id AS experiment_id,
    u.id AS usage_id,
    u.used_from AS used_from,
    u.used_until AS used_until,
    u.required_scope AS required_scope,
    inst.id AS instrument_id,
    cert.id AS cert_id,
    cert.scope AS cert_scope,
    cert.recorded_at AS cert_recorded_at,
    c.id AS claim_id,
    c.valid_from AS valid_from,
    c.valid_until AS valid_until,
    c.recorded_at AS claim_recorded_at,
    c.verified_at AS claim_verified_at,
    ev.id AS evidence_id,
    count(DISTINCT s) AS num_superseders,
    collect(DISTINCT CASE WHEN rev.id IS NOT NULL THEN {id: rev.id, effective_at: rev.effective_at, recorded_at: rev.recorded_at} ELSE null END) AS revocations,
    collect(DISTINCT CASE WHEN contra.id IS NOT NULL THEN {id: contra.id, recorded_at: contra.recorded_at, verified_at: contra.verified_at} ELSE null END) AS contradictions
"""


# ── Impact traversal ─────────────────────────────────────────────────────────

Q_IMPACT_TRAVERSAL = """
MATCH (exp:Experiment {id: $exp_id})-[:PRODUCED]->(root_ds:Dataset)
OPTIONAL MATCH (child_ds:Dataset)-[:DERIVED_FROM*0..6]->(root_ds)
WITH root_ds, collect(DISTINCT child_ds) + [root_ds] AS all_ds_nodes
UNWIND all_ds_nodes AS ds
WITH DISTINCT ds
OPTIONAL MATCH (rpt:Report)-[:USES_DATASET]->(ds)
RETURN
    ds.id AS dataset_id,
    ds.name AS dataset_name,
    ds.status AS dataset_status,
    rpt.id AS report_id,
    rpt.title AS report_title,
    rpt.publication_status AS report_status
"""

Q_DATASET_LINEAGE_PATH = """
MATCH (exp:Experiment {id: $exp_id})-[:PRODUCED]->(root_ds:Dataset)
OPTIONAL MATCH path = (leaf_ds:Dataset)-[:DERIVED_FROM*0..6]->(root_ds)
WHERE leaf_ds.id = $target_ds_id
RETURN [node IN nodes(path) | node.id] AS path_nodes
"""


# ── Assignment fallback ──────────────────────────────────────────────────────

Q_RESOLVE_ASSIGNMENT = """
MATCH (ds:Dataset {id: $dataset_id})
OPTIONAL MATCH (owner:Scientist)-[:OWNS]->(ds)
OPTIONAL MATCH (exp:Experiment)-[:PRODUCED]->(root_ds:Dataset)
WHERE root_ds = ds OR (ds)-[:DERIVED_FROM*1..6]->(root_ds)
OPTIONAL MATCH (performer:Scientist)-[:PERFORMED]->(exp)
OPTIONAL MATCH (exp)-[:HAS_USAGE]->(:InstrumentUsage)-[:OF_INSTRUMENT]->(inst:Instrument)
OPTIONAL MATCH (custodian:Scientist)-[:CUSTODIAN_OF]->(inst)
RETURN
    ds.id AS dataset_id,
    owner.id AS owner_id,
    owner.name AS owner_name,
    performer.id AS performer_id,
    performer.name AS performer_name,
    custodian.id AS custodian_id,
    custodian.name AS custodian_name
"""


# ── Action execution & audit records ─────────────────────────────────────────

Q_CHECK_ACTION_RECORD_EXISTS = """
MATCH (ar:ActionRecord {id: $id})
RETURN ar.id AS id
"""

Q_RECORD_ACTION = """
MATCH (inc:Incident {id: $incident_id})
MERGE (ar:ActionRecord {id: $id})
ON CREATE SET
    ar.action_type = $action_type,
    ar.incident_id = $incident_id,
    ar.target_id = $target_id,
    ar.prior_state = $prior_state,
    ar.new_state = $new_state,
    ar.reason = $reason,
    ar.evidence_path_json = $evidence_path_json,
    ar.recorded_at = $recorded_at,
    ar.actor_agent = $actor_agent,
    ar.rule_version = $rule_version
MERGE (ar)-[:PART_OF]->(inc)
RETURN ar.id AS id
"""

Q_QUARANTINE_DATASET = """
MATCH (ds:Dataset {id: $dataset_id})
SET ds.status = 'QUARANTINED'
RETURN ds.id AS id, ds.status AS status
"""

Q_UPDATE_REPORT_STATUS = """
MATCH (rpt:Report {id: $report_id})
SET rpt.publication_status = $new_status
RETURN rpt.id AS id, rpt.publication_status AS status
"""

Q_CREATE_TASK = """
MATCH (inc:Incident {id: $incident_id})
MERGE (t:Task {id: $task_id})
ON CREATE SET
    t.task_type = $task_type,
    t.status = $status,
    t.assignee_id = $assignee_id,
    t.escalation_reason = $escalation_reason,
    t.created_at = $created_at,
    t.incident_id = $incident_id,
    t.target_id = $target_id
MERGE (inc)-[:HAS_TASK]->(t)
WITH t
OPTIONAL MATCH (sci:Scientist {id: $assignee_id})
FOREACH (_ IN CASE WHEN sci IS NOT NULL THEN [1] ELSE [] END |
    MERGE (t)-[:ASSIGNED_TO]->(sci)
)
RETURN t.id AS id, t.status AS status, t.assignee_id AS assignee_id
"""

Q_GET_INCIDENT_TASKS = """
MATCH (inc:Incident {id: $incident_id})-[:HAS_TASK]->(t:Task)
OPTIONAL MATCH (t)-[:ASSIGNED_TO]->(sci:Scientist)
RETURN
    t.id AS task_id,
    t.task_type AS task_type,
    t.status AS status,
    t.assignee_id AS assignee_id,
    sci.name AS assignee_name,
    t.escalation_reason AS escalation_reason,
    t.target_id AS target_id,
    t.created_at AS created_at
"""

Q_GET_INCIDENT_ACTION_RECORDS = """
MATCH (ar:ActionRecord)-[:PART_OF]->(inc:Incident {id: $incident_id})
RETURN
    ar.id AS id,
    ar.action_type AS action_type,
    ar.target_id AS target_id,
    ar.prior_state AS prior_state,
    ar.new_state AS new_state,
    ar.reason AS reason,
    ar.actor_agent AS actor_agent,
    ar.recorded_at AS recorded_at,
    ar.evidence_path_json AS evidence_path_json
ORDER BY ar.recorded_at ASC
"""


# ── Publication Gate ─────────────────────────────────────────────────────────

Q_REPORT_UPSTREAM_DEPENDENCIES = """
MATCH (rpt:Report {id: $report_id})
OPTIONAL MATCH (rpt)-[:USES_DATASET]->(direct_ds:Dataset)
OPTIONAL MATCH (direct_ds)-[:DERIVED_FROM*0..6]->(upstream_ds:Dataset)
WITH rpt, collect(DISTINCT direct_ds) + collect(DISTINCT upstream_ds) AS all_ds
UNWIND all_ds AS ds
WITH DISTINCT rpt, ds
WHERE ds IS NOT NULL
OPTIONAL MATCH (exp:Experiment)-[:PRODUCED]->(ds)
OPTIONAL MATCH (exp)-[:HAS_USAGE]->(u:InstrumentUsage)
RETURN
    rpt.id AS report_id,
    rpt.title AS report_title,
    rpt.publication_status AS report_status,
    collect(DISTINCT {id: ds.id, name: ds.name, status: ds.status}) AS used_datasets,
    collect(DISTINCT {exp_id: exp.id, usage_id: u.id}) AS upstream_usages
"""

Q_RECORD_PUBLICATION_ATTEMPT = """
MATCH (rpt:Report {id: $report_id})
CREATE (pa:PublicationAttempt {
    id: $attempt_id,
    report_id: $report_id,
    actor: $actor,
    result: $result,
    reason: $reason,
    evidence_path_json: $evidence_path_json,
    at: $at
})
CREATE (rpt)-[:HAS_ATTEMPT]->(pa)
RETURN pa.id AS id
"""

Q_LIST_REPORTS = """
MATCH (rpt:Report)
RETURN
    rpt.id AS id,
    rpt.title AS title,
    rpt.publication_status AS publication_status
ORDER BY rpt.id ASC
"""


# ── Episodic Memory & Timeline ───────────────────────────────────────────────

Q_CHECK_EVENT_EXISTS = """
MATCH (e:Event {id: $event_id})
RETURN e.id AS id, e.seq AS seq
"""

Q_APPEND_EVENT = """
MATCH (inc:Incident {id: $incident_id})
WITH inc, coalesce(inc.next_seq, 1) AS cur_seq
SET inc.next_seq = cur_seq + 1
CREATE (ev:Event {
    id: $event_id,
    incident_id: $incident_id,
    seq: cur_seq,
    event_type: $event_type,
    at: $at,
    actor_agent: $actor_agent,
    summary: $summary,
    outcome: $outcome,
    logical_key: $logical_key,
    payload_json: $payload_json
})
CREATE (inc)-[:HAS_EVENT]->(ev)
WITH inc, ev, cur_seq
OPTIONAL MATCH (inc)-[:HAS_EVENT]->(prev:Event)
WHERE prev.seq = cur_seq - 1
FOREACH (_ IN CASE WHEN prev IS NOT NULL THEN [1] ELSE [] END |
    CREATE (ev)-[:FOLLOWS]->(prev)
)
RETURN ev.id AS id, ev.seq AS seq
"""

Q_LINK_EVENT_EVIDENCE = """
MATCH (ev:Event {id: $event_id})
MATCH (evidence:Evidence {id: $evidence_id})
MERGE (ev)-[:REFERENCES_EVIDENCE]->(evidence)
"""

Q_LINK_EVENT_ACTION = """
MATCH (ev:Event {id: $event_id})
MATCH (ar:ActionRecord {id: $action_id})
MERGE (ev)-[:REFERENCES_ACTION]->(ar)
"""

Q_GET_TIMELINE = """
MATCH (inc:Incident {id: $incident_id})-[:HAS_EVENT]->(ev:Event)
OPTIONAL MATCH (ev)-[:REFERENCES_EVIDENCE]->(ev_ref:Evidence)
OPTIONAL MATCH (ev)-[:REFERENCES_ACTION]->(ar_ref:ActionRecord)
RETURN
    ev.id AS id,
    ev.seq AS seq,
    ev.event_type AS event_type,
    ev.at AS at,
    ev.actor_agent AS actor_agent,
    ev.summary AS summary,
    ev.outcome AS outcome,
    ev.logical_key AS logical_key,
    ev.payload_json AS payload_json,
    collect(DISTINCT ev_ref.id) AS evidence_ids,
    collect(DISTINCT ar_ref.id) AS action_ids
ORDER BY ev.seq ASC
"""

Q_GET_INCIDENT_HEADER = """
MATCH (inc:Incident {id: $incident_id})
OPTIONAL MATCH (inc)-[:CONCERNS_INSTRUMENT]->(inst:Instrument)
RETURN
    inc.id AS id,
    inc.incident_type AS incident_type,
    inc.status AS status,
    inc.opened_at AS opened_at,
    inc.next_seq AS next_seq,
    coalesce(inc.instrument_id, inst.id) AS instrument_id
"""

Q_LIST_INCIDENTS = """
MATCH (inc:Incident)
OPTIONAL MATCH (inc)-[:CONCERNS_INSTRUMENT]->(inst:Instrument)
RETURN
    inc.id AS id,
    inc.incident_type AS incident_type,
    inc.status AS status,
    inc.opened_at AS opened_at,
    coalesce(inc.instrument_id, inst.id) AS instrument_id
ORDER BY inc.opened_at DESC
"""

Q_CLOSE_INCIDENT = """
MATCH (inc:Incident {id: $incident_id})
SET inc.status = 'CLOSED'
RETURN inc.id AS id, inc.status AS status
"""


# ── Semantic Memory: Belief Tracking ─────────────────────────────────────────

Q_GET_SUBJECT_CLAIMS = """
MATCH (cert:CalibrationCertificate {id: $subject_id})-[:HAS_VALIDITY]->(c:Claim)
OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(ev:Evidence)
OPTIONAL MATCH (s:Claim)-[sup:SUPERSEDES]->(c)
OPTIONAL MATCH (c)-[contra:CONTRADICTS]-(contradictor:Claim)
RETURN
    c.id AS claim_id,
    c.valid_from AS valid_from,
    c.valid_until AS valid_until,
    c.recorded_at AS recorded_at,
    c.verified_at AS verified_at,
    c.summary AS summary,
    ev.id AS evidence_id,
    ev.source_type AS evidence_source_type,
    ev.trust_level AS evidence_trust_level,
    ev.observed_at AS evidence_observed_at,
    ev.recorded_at AS evidence_recorded_at,
    collect(DISTINCT {
        superseder_id: s.id,
        recorded_at: s.recorded_at,
        verified_at: s.verified_at,
        reason: sup.reason
    }) AS superseders,
    collect(DISTINCT {
        contradictor_id: contradictor.id,
        recorded_at: contradictor.recorded_at,
        verified_at: contradictor.verified_at,
        detected_at: contra.detected_at,
        reason: contra.reason,
        resolution: contra.resolution
    }) AS contradictions
"""

Q_GET_CERTIFICATE_INSTRUMENT = """
MATCH (cert:CalibrationCertificate {id: $cert_id})-[:CERTIFIES]->(inst:Instrument)
RETURN inst.id AS instrument_id
"""



# ── Agent Handoffs ───────────────────────────────────────────────────────────

Q_CREATE_HANDOFF = """
MATCH (inc:Incident {id: $incident_id})
MERGE (h:Handoff {id: $id})
ON CREATE SET
    h.incident_id = $incident_id,
    h.from_agent = $from_agent,
    h.to_agent = $to_agent,
    h.task_type = $task_type,
    h.status = 'PENDING',
    h.payload_json = $payload_json,
    h.idempotency_key = $idempotency_key,
    h.created_at = $created_at,
    h.attempt_count = 0
MERGE (inc)-[:HAS_HANDOFF]->(h)
RETURN h.id AS id, h.status AS status
"""

Q_CLAIM_HANDOFF = """
MATCH (h:Handoff {id: $id, status: 'PENDING'})
SET
    h.status = 'CLAIMED',
    h.claimed_by = $claimed_by,
    h.claimed_at = $claimed_at,
    h.lease_expires_at = $lease_expires_at,
    h.attempt_count = h.attempt_count + 1
RETURN h.id AS id, h.status AS status, h.attempt_count AS attempt_count
"""

Q_COMPLETE_HANDOFF = """
MATCH (h:Handoff {id: $id})
SET
    h.status = 'COMPLETED',
    h.completed_at = $completed_at,
    h.result_json = $result_json
RETURN h.id AS id, h.status AS status
"""

Q_FAIL_HANDOFF = """
MATCH (h:Handoff {id: $id})
SET
    h.status = 'FAILED',
    h.failed_reason = $failed_reason,
    h.completed_at = $completed_at
RETURN h.id AS id, h.status AS status
"""

Q_GET_PENDING_HANDOFFS = """
MATCH (h:Handoff {incident_id: $incident_id, status: 'PENDING'})
RETURN
    h.id AS id,
    h.incident_id AS incident_id,
    h.from_agent AS from_agent,
    h.to_agent AS to_agent,
    h.task_type AS task_type,
    h.payload_json AS payload_json,
    h.idempotency_key AS idempotency_key,
    h.created_at AS created_at,
    h.attempt_count AS attempt_count
ORDER BY h.created_at ASC
"""

Q_GET_ALL_INCIDENT_HANDOFFS = """
MATCH (h:Handoff {incident_id: $incident_id})
RETURN
    h.id AS id,
    h.from_agent AS from_agent,
    h.to_agent AS to_agent,
    h.task_type AS task_type,
    h.status AS status,
    h.payload_json AS payload_json,
    h.result_json AS result_json,
    h.created_at AS created_at,
    h.claimed_at AS claimed_at,
    h.completed_at AS completed_at
ORDER BY h.created_at ASC
"""

Q_RECOVER_EXPIRED_HANDOFFS = """
MATCH (h:Handoff)
WHERE h.status = 'CLAIMED' AND h.lease_expires_at < $now
WITH h
SET h.status = CASE WHEN h.attempt_count >= $max_attempts THEN 'FAILED' ELSE 'PENDING' END,
    h.failed_reason = CASE WHEN h.attempt_count >= $max_attempts THEN 'Max claim attempts exceeded' ELSE h.failed_reason END
RETURN h.id AS id, h.status AS status, h.attempt_count AS attempt_count
"""


# ── Procedural Memory: Playbooks & Runs ──────────────────────────────────────

Q_CHECK_PLAYBOOK_EXISTS = """
MATCH (p:InvestigationPlaybook {id: $id})
RETURN p.id AS id, p.status AS status
"""

Q_CREATE_PLAYBOOK = """
MATCH (src:Incident {id: $source_incident_id})
MERGE (p:InvestigationPlaybook {id: $id})
ON CREATE SET
    p.name = $name,
    p.version = $version,
    p.status = $status,
    p.incident_type = $incident_type,
    p.applicability_json = $applicability_json,
    p.source_incident_id = $source_incident_id,
    p.exploration_summary_json = $exploration_summary_json,
    p.promotion_checks_json = $promotion_checks_json
MERGE (p)-[:DERIVED_FROM_INCIDENT]->(src)
RETURN p.id AS id, p.status AS status
"""

Q_ATTACH_PLAYBOOK_STEP = """
MATCH (p:InvestigationPlaybook {id: $playbook_id})
CREATE (ps:PlaybookStep {
    id: $id,
    order: $order,
    step_type: $step_type,
    kind: $kind,
    role: $role,
    precondition_key: $precondition_key,
    postcondition_key: $postcondition_key,
    params_json: $params_json,
    expected_outcome: $expected_outcome
})
CREATE (p)-[:HAS_STEP]->(ps)
WITH p, ps
OPTIONAL MATCH (p)-[:HAS_STEP]->(prev:PlaybookStep {order: $order - 1})
FOREACH (_ IN CASE WHEN prev IS NOT NULL THEN [1] ELSE [] END |
    CREATE (prev)-[:NEXT]->(ps)
)
RETURN ps.id AS id
"""

Q_PROMOTE_PLAYBOOK = """
MATCH (p:InvestigationPlaybook {id: $id})
MATCH (inc:Incident {id: $incident_id})
SET
    p.status = 'VERIFIED',
    p.verified_at = $at,
    p.promotion_checks_json = $promotion_checks_json
MERGE (p)-[:VALIDATED_IN {at: $at, outcome: 'PASSED'}]->(inc)
RETURN p.id AS id, p.status AS status
"""

Q_RECORD_PROMOTION_FAILURE = """
MATCH (p:InvestigationPlaybook {id: $id})
SET
    p.last_promotion_failure_json = $failure_json
RETURN p.id AS id, p.status AS status
"""

Q_FIND_MATCHING_PLAYBOOKS = """
MATCH (p:InvestigationPlaybook {incident_type: $incident_type})
RETURN
    p.id AS id,
    p.name AS name,
    p.version AS version,
    p.status AS status,
    p.applicability_json AS applicability_json,
    p.exploration_summary_json AS exploration_summary_json
ORDER BY (CASE WHEN p.status = 'VERIFIED' THEN 0 ELSE 1 END) ASC
"""

Q_CHECK_INCIDENT_SHAPE_APPLICABILITY = """
MATCH (exp:Experiment)-[:HAS_USAGE]->(u:InstrumentUsage)-[:OF_INSTRUMENT]->(inst:Instrument)
WHERE ($instrument_id IS NULL OR inst.id = $instrument_id)
MATCH (exp)-[:PRODUCED]->(ds:Dataset)
OPTIONAL MATCH (cert:CalibrationCertificate)-[:CERTIFIES]->(inst)
WHERE cert.scope = u.required_scope
RETURN count(DISTINCT inst) AS inst_count, count(DISTINCT ds) AS dataset_count
"""


Q_GET_PLAYBOOK_STEPS = """
MATCH (p:InvestigationPlaybook {id: $playbook_id})-[:HAS_STEP]->(ps:PlaybookStep)
RETURN
    ps.id AS id,
    ps.order AS order,
    ps.step_type AS step_type,
    ps.kind AS kind,
    ps.role AS role,
    ps.precondition_key AS precondition_key,
    ps.postcondition_key AS postcondition_key,
    ps.params_json AS params_json,
    ps.expected_outcome AS expected_outcome
ORDER BY ps.order ASC
"""

Q_GET_PLAYBOOK_BY_SOURCE_INCIDENT = """
MATCH (p:InvestigationPlaybook {source_incident_id: $incident_id})
RETURN
    p.id AS id,
    p.name AS name,
    p.version AS version,
    p.status AS status,
    p.incident_type AS incident_type,
    p.source_incident_id AS source_incident_id,
    p.applicability_json AS applicability_json,
    p.exploration_summary_json AS exploration_summary_json,
    p.promotion_checks_json AS promotion_checks_json
"""

Q_RECORD_PLAYBOOK_RUN = """
MATCH (inc:Incident {id: $incident_id})
MATCH (p:InvestigationPlaybook {id: $playbook_id})
CREATE (pr:PlaybookRun {
    id: $id,
    mode: $mode,
    started_at: $started_at,
    finished_at: $finished_at
})
CREATE (pr)-[:FOR_INCIDENT]->(inc)
CREATE (pr)-[:USED_PLAYBOOK]->(p)
RETURN pr.id AS id
"""

Q_RECORD_STEP_RESULT = """
MATCH (pr:PlaybookRun {id: $run_id})
CREATE (sr:StepResult {
    id: $id,
    step_id: $step_id,
    status: $status,
    detail: $detail,
    differences_json: $differences_json,
    precondition_json: $precondition_json,
    postcondition_json: $postcondition_json,
    observed_json: $observed_json,
    expected_json: $expected_json,
    domain_query_count: $domain_query_count,
    elapsed_ms: $elapsed_ms
})
CREATE (pr)-[:HAS_RESULT]->(sr)
RETURN sr.id AS id
"""

Q_GET_PLAYBOOK_RUNS = """
MATCH (pr:PlaybookRun)-[:FOR_INCIDENT]->(inc:Incident {id: $incident_id})
MATCH (pr)-[:USED_PLAYBOOK]->(p:InvestigationPlaybook)
OPTIONAL MATCH (pr)-[:HAS_RESULT]->(sr:StepResult)
RETURN
    pr.id AS run_id,
    pr.mode AS mode,
    p.id AS playbook_id,
    p.name AS playbook_name,
    p.status AS playbook_status,
    collect(DISTINCT {
        id: sr.id,
        step_id: sr.step_id,
        status: sr.status,
        detail: sr.detail,
        differences_json: sr.differences_json,
        domain_query_count: sr.domain_query_count,
        elapsed_ms: sr.elapsed_ms
    }) AS step_results
"""


# ── Incident Metrics ─────────────────────────────────────────────────────────

Q_PERSIST_INCIDENT_METRICS = """
MATCH (inc:Incident {id: $incident_id})
MERGE (im:IncidentMetrics {id: $id})
ON CREATE SET
    im.incident_id = $incident_id,
    im.mode = $mode,
    im.domain_queries = $domain_queries,
    im.bookkeeping_queries = $bookkeeping_queries,
    im.steps_executed = $steps_executed,
    im.steps_skipped = $steps_skipped,
    im.handoffs = $handoffs,
    im.elapsed_ms = $elapsed_ms,
    im.started_at = $started_at,
    im.finished_at = $finished_at,
    im.query_counts_json = $query_counts_json
MERGE (inc)-[:HAS_METRICS]->(im)
RETURN im.id AS id
"""

Q_GET_INCIDENT_METRICS = """
MATCH (inc:Incident {id: $incident_id})-[:HAS_METRICS]->(im:IncidentMetrics)
RETURN
    im.id AS id,
    im.incident_id AS incident_id,
    im.mode AS mode,
    im.domain_queries AS domain_queries,
    im.bookkeeping_queries AS bookkeeping_queries,
    im.steps_executed AS steps_executed,
    im.steps_skipped AS steps_skipped,
    im.handoffs AS handoffs,
    im.elapsed_ms AS elapsed_ms,
    im.started_at AS started_at,
    im.finished_at AS finished_at,
    im.query_counts_json AS query_counts_json
"""

Q_RECONSTRUCT_EVIDENCE_CLAIMS = """
MATCH (inc:Incident {id: $incident_id})-[:HAS_EVENT]->(ev:Event)-[:REFERENCES_EVIDENCE]->(e:Evidence)
OPTIONAL MATCH (c:Claim)-[:SUPPORTED_BY]->(e)
OPTIONAL MATCH (s:Claim)-[:SUPERSEDES]->(c)
OPTIONAL MATCH (c)-[:CONTRADICTS]-(contra:Claim)
RETURN DISTINCT
    e.id AS evidence_id,
    e.source_type AS source_type,
    e.trust_level AS trust_level,
    e.observed_at AS observed_at,
    e.recorded_at AS recorded_at,
    c.id AS claim_id,
    c.claim_type AS claim_type,
    c.valid_from AS valid_from,
    c.valid_until AS valid_until,
    c.verified_at AS verified_at,
    collect(DISTINCT s.id) AS superseder_ids,
    collect(DISTINCT contra.id) AS contradiction_ids
"""

