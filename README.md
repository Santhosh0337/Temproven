# Temproven

> **"When evidence changes, every dependent decision must be reconsidered."**

A temporal-graph laboratory integrity and incident-learning system built on **synthetic** records. Temproven is a software workflow-enforcement demo — it does not certify real science or control real equipment.

Built for **Graph Hacks 2026** (WeMakeDevs × FalkorDB).

---

## What it is and is not

**Is:** A demonstration of temporal graph reasoning over a connected dependency graph that triggers enforced application actions (quarantine, publication gate, task assignment) and reusable investigation memory, all backed by FalkorDB.

**Is not:** A certification system, a real laboratory tool, a multi-tenant or authenticated service, or a general performance benchmark.

---

## Tracks entered

- **Track 1 — Agents That Act on Connected Data:** temporal graph reasoning changes real application behavior (quarantine, backend publication gate, assigned tasks).
- **Track 2 — Agent Memory and Coordination:** episodic, semantic and procedural memory persist in FalkorDB; three agents coordinate through durable handoff records; a verified playbook from Incident 1 is reused and validated in Incident 2.

---

## Quick setup (local Docker)

> Full instructions in progress — this section will be completed in Phase 7.

```bash
# 1. FalkorDB must be running
docker run -p 6379:6379 -p 3000:3000 -it --rm falkordb/falkordb:latest

# 2. Clone and set up Python environment
cd Temproven
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# 3. Configure
copy .env.example .env          # then edit if needed (defaults work for local Docker)

# 4. Seed the demo graph
python -m temproven.seed.seed

# 5. Run the CLI demo
python -m temproven.demo --act all

# 6. Launch the UI
streamlit run temproven/app/streamlit_app.py

# 7. Run tests
pytest tests/
```

---

## Streamlit UI Dashboard Architecture

The Temproven UI (`temproven/app/streamlit_app.py`) provides an editorial, technical laboratory integrity dashboard structured across four core capability panels:

1. **`01 / INCIDENT` — Incident Overview:**
   - Incident metadata (ID, Type, Status badge, Instrument, UTC/IST timestamps).
   - High-level summary metrics (events, actions, tasks, agent handoffs).
   - Linear episodic event stepper reconstructed from `:FOLLOWS` graph chains.
   - Associated review tasks with role-based assignment and escalation reasons.

2. **`02 / IMPACT` — Downstream Impact Trace:**
   - Graphviz directed acyclic graph (DAG) visualizing propagation from instrument through experiments and datasets to published reports.
   - Inspectable breakdown tables for affected datasets (quarantined), experiments (coverage status & gaps), and downstream reports (held/review).

3. **`03 / INVESTIGATION` — Verified Playbook & Procedural Memory:**
   - Reusable investigation playbook metadata (`VERIFIED`, execution mode).
   - Postcondition checklist (PC1 through PC6) confirming diagnostic integrity.
   - Step-by-step playbook execution results (`PASSED` / `DIFFERED`).
   - Semantic memory belief change explanations and investigation query efficiency metrics.

4. **`04 / PUBLICATION` — Backend Publication Gate:**
   - Live upstream dependency inspector for reports.
   - Honest publication attempt CTA guarded by explicit user confirmation checkbox.
   - Hero Decision Block displaying real-time `ALLOW`, `BLOCK`, or `REVIEW_REQUIRED` verdicts, justification, and audit timestamps.

---

## Graph schema, Cypher queries, and time semantics

> To be filled in Phase 7 with the complete schema diagram, query listing, and bitemporal semantics explanation.

---

## AI-assistance disclosure

This project was built with the assistance of **Antigravity (Google DeepMind)**, an AI coding assistant. The AI was used to:
- Generate boilerplate project structure and module stubs
- Draft Cypher query implementations
- Suggest test cases for temporal edge conditions

All code was reviewed, understood, and verified by the participant. The graph model, query design, temporal semantics, and architectural decisions were directed by the participant with the AI acting as a pair-programming assistant. The participant can explain the graph model, every key query, and the reasoning behind each design choice.

---

## Limitations

- Synthetic data only — no real laboratory records
- No authentication or tenant isolation
- Quarantine release not implemented (out of scope)
- Concurrency safety is untested (single-threaded demo only)
- LLM integration is optional and absent in the P0 submission
- Performance metric applies to this synthetic scenario only; not a general claim
