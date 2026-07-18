# GitHub Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish OpenEnterprise Twin from a dedicated Documents folder as a public, executive-ready GitHub repository with clear functional CTAs.

**Architecture:** Keep the product and Git history unchanged while relocating the repository root. Strengthen only the README opening and GitHub repository metadata; every CTA resolves to a tracked document section or file, so the public surface never depends on an undeployed service.

**Tech Stack:** Git, GitHub CLI, GitHub Actions, Markdown, Make.

## Global Constraints

- Canonical local path is `/Users/miguelfidalgo/Documents/OpenEnterprise-Twin`.
- Public repository is `mfidalgomartins/OpenEnterprise-Twin` and `main` is pushed directly.
- CTA copy remains evidence-led, contains no unsubstantiated performance claim, and uses only tracked local links.
- Public metadata uses the exact description and topics defined in `docs/superpowers/specs/2026-07-18-github-publication-design.md`.

---

### Task 1: Build the executive GitHub entry surface

**Files:**
- Modify: `README.md:1-18`
- Modify: `docs/superpowers/plans/2026-07-18-github-publication.md`

**Interfaces:**
- Consumes: `docs/assets/decision-room-concept.png`, `docs/architecture.md`, `docs/model.md`, `docs/contributing.md`.
- Produces: four internal Markdown CTAs and a policy-to-brief product flow visible before the product thesis.

- [x] **Step 1: Add the public product header and CTA row**

Insert before `## Product thesis`:

```markdown
<div align="center">

# OpenEnterprise Twin

**Evidence-linked Monte Carlo decision twin for commercial, operational and liquidity policies.**

[![CI](https://github.com/mfidalgomartins/OpenEnterprise-Twin/actions/workflows/ci.yml/badge.svg)](https://github.com/mfidalgomartins/OpenEnterprise-Twin/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](backend/pyproject.toml)
[![React](https://img.shields.io/badge/React-19-149ECA?logo=react&logoColor=white)](frontend/package.json)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](docker-compose.yml)
[![License](https://img.shields.io/badge/License-Apache--2.0-2E7D32)](LICENSE)

[Run the flagship demo](#five-minute-demo) · [Explore architecture](docs/architecture.md) · [Read the model](docs/model.md) · [Contribute](docs/contributing.md)

</div>

> **Policy → paired simulation → evidence-linked recommendation → immutable executive brief.**
```

- [x] **Step 2: Validate the rendered-link targets and markdown hygiene**

Run: `test -f backend/pyproject.toml && test -f frontend/package.json && test -f docker-compose.yml && test -f LICENSE && test -f docs/architecture.md && test -f docs/model.md && test -f docs/contributing.md && git diff --check`

Expected: exit code `0`.

- [x] **Step 3: Mark Task 1 complete in this plan**

Replace the three Task 1 checkboxes with `[x]` after validation.

### Task 2: Establish the public repository and canonical local location

**Files:**
- Modify: `.git/config` through `git remote add origin`.
- Relocate: repository root to `/Users/miguelfidalgo/Documents/OpenEnterprise-Twin`.

**Interfaces:**
- Consumes: authenticated `gh` session and clean Git worktree.
- Produces: GitHub repository metadata, `origin` remote and a tracked public `main` branch.

- [ ] **Step 1: Verify the repository can be relocated without collision**

Run: `test ! -e /Users/miguelfidalgo/Documents/OpenEnterprise-Twin && git status --short`

Expected: exit code `0` and no worktree output after Task 1 is committed.

- [ ] **Step 2: Relocate the checkout and create the public GitHub repository**

Run from `/Users/miguelfidalgo/Documents/Codex/2026-07-16`:

```bash
mv de /Users/miguelfidalgo/Documents/OpenEnterprise-Twin
```

Then run from the new root:

```bash
gh repo create mfidalgomartins/OpenEnterprise-Twin --public --source=. --remote=origin --push
```

- [ ] **Step 3: Set public metadata and push the final presentation commit**

Run:

```bash
gh repo edit mfidalgomartins/OpenEnterprise-Twin \
  --description "Evidence-linked Monte Carlo decision twin for commercial, operational and liquidity policies." \
  --add-topic digital-twin \
  --add-topic monte-carlo \
  --add-topic decision-intelligence \
  --add-topic operations-research \
  --add-topic revenue-analytics \
  --add-topic supply-chain \
  --add-topic fastapi \
  --add-topic react
git push origin main
```

- [ ] **Step 4: Verify the public surface**

Run:

```bash
gh repo view mfidalgomartins/OpenEnterprise-Twin --json url,visibility,description,repositoryTopics,defaultBranchRef
git remote -v
git status -sb
```

Expected: public visibility, the specified description and topics, `origin` pointing to GitHub, and a clean tracking `main` branch.

- [ ] **Step 5: Mark Task 2 complete in this plan**

Replace the five Task 2 checkboxes with `[x]` after verification.
