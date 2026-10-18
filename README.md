# Temproven

> **"When evidence changes, every dependent decision must be reconsidered."**

A temporal-graph laboratory integrity and incident-learning system built on **synthetic** records. Temproven is a software workflow-enforcement demo — it does not certify real science or control real equipment.

Built for **Graph Hacks 2026** (WeMakeDevs × FalkorDB).

---

## What It Is and Is Not

**Is:** A demonstration of temporal graph reasoning over connected dependencies that triggers application actions, including quarantine, publication gating, task assignment, and reusable investigation memory, backed by FalkorDB.

**Is not:** A certification system, a real laboratory tool, an authenticated or multi-tenant service, or a general performance benchmark.

---

## Tracks Entered

- **Track 1 — Agents That Act on Connected Data:** Temporal graph reasoning changes application behavior through quarantine, publication gating, and task assignment.
- **Track 2 — Agent Memory and Coordination:** Episodic, semantic, and procedural memory persist in FalkorDB. Three agents coordinate through durable handoff records, and a verified playbook from Incident 1 is reused and validated in Incident 2.

---

## Quick Setup

### Prerequisites

- Python 3.13 or a compatible Python version
- Docker Desktop
- Git

### 1. Start FalkorDB

Ensure Docker Desktop is running, then run:

```powershell
docker run -d --name falkordb `
  -p 6379:6379 `
  -p 3000:3000 `
  -v falkordb_data:/data `
  falkordb/falkordb:latest
```

If a container named `falkordb` already exists, start it instead:

```powershell
docker start falkordb
```

### 2. Clone the Repository

```powershell
git clone https://github.com/Santhosh0337/Temproven.git
cd Temproven
```

### 3. Create the Python Environment

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 4. Configure the Environment

```powershell
Copy-Item .env.example .env
```

The example configuration uses a local FalkorDB instance. Adjust `.env` if your connection settings differ.

### 5. Initialize the Demo Data

```powershell
.\.venv\Scripts\python.exe -m temproven.seed.seed
```

### 6. Run the CLI Demonstration

```powershell
.\.venv\Scripts\python.exe -m temproven.demo --act all
```

### 7. Launch the Dashboard

```powershell
.\.venv\Scripts\python.exe -m streamlit run temproven/app/streamlit_app.py
```

Open the local URL printed by Streamlit in your browser.

### 8. Run the Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/
```

**Note:** These commands describe the intended setup flow. A fresh-clone installation should be verified before claiming the complete setup has been tested.

---

## Streamlit UI Dashboard Architecture

The Temproven dashboard (`temproven/app/streamlit_app.py`) provides four core capability panels.

### 1. Incident Overview and Interactive Event Timeline

- Incident metadata, including ID, type, status, instrument, and UTC/IST timestamps.
- Evidence revision details distinguishing **valid time** — when evidence applies — from **recorded time** — when the system recorded it.
- Summary figures for affected experiments, datasets, reports, and tasks.
- Interactive event timeline positioned by persisted sequence number (`seq`), not timestamp.
- Distinct markers for significant and routine events.
- Hover tooltips showing sequence, category, event type, summary, actor, and recorded time.
- Event selection with a detailed inspection panel.
- Category filters, event counts, and a notice when events are hidden by filtering.
- Horizontal and vertical timeline layouts.
- Previous, next, go-to-sequence, and clear-selection controls.
- Full event details, including actor, timestamps, outcome, references, targets, and JSON payload.
- Collapsed stage summary and complete audit trail ordered by event sequence.

The timeline explicitly states:

> Line = recorded event sequence, not causation.

### 2. Downstream Impact Trace

- A graph visualization showing dependencies from instruments through experiments and datasets to downstream reports.
- Inspection tables for affected datasets, experiment coverage and gaps, and downstream reports marked held or requiring review.

### 3. Investigation, Verified Playbook, and Procedural Memory

- Reusable investigation playbook metadata and verification status.
- Postcondition checklist covering PC1 through PC6.
- Step-by-step execution outcomes marked `PASSED` or `DIFFERED`.
- Semantic-memory belief-change explanations and investigation-query efficiency metrics.

### 4. Backend Publication Gate

- Upstream dependency inspection for reports.
- Publication-attempt control guarded by an explicit user-confirmation checkbox.
- Decision block displaying `ALLOW`, `BLOCK`, or `REVIEW_REQUIRED`, with justification and audit timestamps.

---

## Graph Schema, Cypher Queries, and Time Semantics

FalkorDB is the graph database behind Temproven's connected incident data and investigation workflow.

The graph supports:

- Connected incident and evidence records.
- Dependency tracing from instruments to experiments, datasets, and reports.
- Persisted investigation events and agent handoffs.
- Reusable procedural memory and playbook execution records.
- Downstream actions and publication decisions.

### Temporal Reasoning

Temproven distinguishes two kinds of time:

- **Valid time:** When a fact applies in the modeled real-world timeline.
- **Recorded time:** When the system records or learns that fact.

This distinction enables an investigation to reconsider earlier decisions when newer authoritative evidence changes the interpretation of an earlier state.

**Implementation reference:** The exact node labels, relationship types, and representative Cypher queries should be taken from the source code. Do not treat this summary as a complete graph-schema specification.

---

## AI-Assistance Disclosure

This project was developed with assistance from **Antigravity**, an AI coding assistant.

AI assistance was used to:

- Generate boilerplate project structure and module stubs.
- Draft Cypher query implementations.
- Suggest test cases for temporal edge conditions.

The participant reviewed and tested the implementation and is responsible for understanding the submitted code, graph model, temporal semantics, and architectural decisions. The implementation should be explained and demonstrated accurately.

---

## Limitations

- Synthetic data only; no real laboratory records.
- No authentication or tenant isolation.
- Quarantine release is not implemented.
- Concurrency safety has not been tested; the demo targets a single-threaded workflow.
- LLM integration is absent from this submission; the agents use deterministic, graph-backed workflows.
- Performance measurements apply only to the synthetic demonstration scenario and are not general performance claims.

---

## Demo

Demo video: Add the accessible demo video URL before submission.

---

## Repository

Source code: https://github.com/Santhosh0337/Temproven
