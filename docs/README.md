# Documentation Register

Official documentation for **Algo Trading Hub**. Use this as the entry point for
course review, engineering handover, and production-readiness checks.

## Course Review Reading Path

| Document | Audience | Purpose |
|----------|----------|---------|
| [../README.md](../README.md) | All reviewers | Product overview, installation, local run path, validation checklist |
| [../backend/README.md](../backend/README.md) | Engineers / quants | Backend overview, quick start, folder map, reading path |
| [**ARCHITECTURE.md**](ARCHITECTURE.md) | Architects / reviewers | Canonical architecture diagram index and component map |
| [**REPORT_ALIGNMENT.md**](REPORT_ALIGNMENT.md) | Course reviewers / report writers | QF635 report sections mapped to repo evidence and remaining report work |

## Backend Deep Dives

| Document | Audience | Purpose |
|----------|----------|---------|
| [../backend/docs/backend-architecture.md](../backend/docs/backend-architecture.md) | Engineers / reviewers | Process model, API layer, engine core, gateway, common layer, persistence |
| [../backend/docs/market-data-and-strategies.md](../backend/docs/market-data-and-strategies.md) | Quants / report writers | Market data, analytics, strategies, backtesting context |
| [../backend/docs/risk-execution-and-portfolio.md](../backend/docs/risk-execution-and-portfolio.md) | Engineers / risk reviewers | Execution path, risk stack, breakers, flattening, OMS, position truth |
| [../backend/docs/runtime-reference.md](../backend/docs/runtime-reference.md) | Reviewers / operators | Env, startup modes, API contract, run archive, testing, troubleshooting |

## Operations and Governance

| Document | Audience | Purpose |
|----------|----------|---------|
| [**OPERATIONS.md**](OPERATIONS.md) | SRE / trading ops | Health semantics, monitoring, incidents, backups, deployment patterns |
| [**SECURITY.md**](SECURITY.md) | Security / platform | Threat model, secrets, network control plane, hardening checklist |
| [**COMPLIANCE_AND_GOVERNANCE.md**](COMPLIANCE_AND_GOVERNANCE.md) | Risk / compliance | Limitations of scope, records, change control, regulatory disclaimer |
| [**deploy/gcp/README.md**](../deploy/gcp/README.md) | Platform / SRE | Google Cloud: Compute Engine, Docker, Artifact Registry, backups |
| [**deploy/vercel/README.md**](../deploy/vercel/README.md) | Platform / frontend | Dashboard deployment and frontend environment variables |

## Supporting Material

| Document | Audience | Purpose |
|----------|----------|---------|
| [../BRANCH_CHANGES.md](../BRANCH_CHANGES.md) | Reviewers / teammates | Human-readable log of branch changes from `main` and the reason for each change |
| [**STRUCTURE.md**](STRUCTURE.md) | Contributors / reviewers | Quick code map for frontend, backend, and tests |
| [../backend/AGENTS.md](../backend/AGENTS.md) | Contributors | Engineering conventions for the Python backend |

## Optional / Non-Core Material

These files are useful for context but are not required for a first pass through
the QF635 submission:

| Document | Purpose |
|----------|---------|
| [**SPLIT_AUDIT.md**](SPLIT_AUDIT.md) | Maintainability audit for possible future module splits |
| [**INVESTOR_DECK.md**](INVESTOR_DECK.md) | Investor slide plan; not required for QF635 submission |
| [**netting-analysis-report.md**](netting-analysis-report.md) | Generated netting stats from local run archives; verify freshness before citing |

## Versioning Note

These documents describe the repository **as built**. For regulated use, align
them with your firm's SDLC, approval workflow, and independent verification of
behaviour.
