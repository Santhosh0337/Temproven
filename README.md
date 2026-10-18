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

- **Track 1 — Agents That Act on Connected Data:** Temporal graph reasoning changes real application behavior through quarantine, a backend publication gate, and task assignment.
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
