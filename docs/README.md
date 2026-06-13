# Documentation Register

Official documentation for **Algo Trading Hub**. Use this as the entry point for
course review, engineering handover, and production-readiness checks.

## Primary Reading Path

| Document | Audience | Purpose |
|----------|----------|---------|
| [../README.md](../README.md) | All reviewers | Product overview, local setup, dashboard behaviour, architecture summary |
| [**REPORT_ALIGNMENT.md**](REPORT_ALIGNMENT.md) | Course reviewers / report writers | QF635 report sections mapped to repo evidence and remaining report work |
| [**ARCHITECTURE.md**](ARCHITECTURE.md) | Architects / security | Canonical links to diagrams + component map |
| [../backend/README.md](../backend/README.md) | Engineers / quants | Engine modules, API contract, configuration matrix, troubleshooting |

## Operations and Governance

| Document | Audience | Purpose |
|----------|----------|---------|
| [**OPERATIONS.md**](OPERATIONS.md) | SRE / trading ops | Health semantics, monitoring, incidents, backups, deployment patterns |
| [**SECURITY.md**](SECURITY.md) | Security / platform | Threat model, secrets, network control plane, hardening checklist |
| [**COMPLIANCE_AND_GOVERNANCE.md**](COMPLIANCE_AND_GOVERNANCE.md) | Risk / compliance | Limitations of scope, records, change control, regulatory disclaimer |
| [**deploy/gcp/README.md**](../deploy/gcp/README.md) | Platform / SRE | Google Cloud: Compute Engine, Docker, Artifact Registry, backups |
| [**deploy/vercel/README.md**](../deploy/vercel/README.md) | Platform / frontend | Dashboard deployment and frontend environment variables |

## Supporting and Generated Material

| Document | Audience | Purpose |
|----------|----------|---------|
| [../BRANCH_CHANGES.md](../BRANCH_CHANGES.md) | Reviewers / teammates | Human-readable log of branch changes from `main` and the reason for each change |
| [**STRUCTURE.md**](STRUCTURE.md) | Contributors | Quick code map and current refactor notes |
| [**SPLIT_AUDIT.md**](SPLIT_AUDIT.md) | Contributors | Maintainability audit for large modules |
| [**INVESTOR_DECK.md**](INVESTOR_DECK.md) | Fundraising / LPs | Investor slide plan with archive-backed metrics; not required for QF635 submission |
| [**netting-analysis-report.md**](netting-analysis-report.md) | Quants / ops | Auto-generated netting stats from `data/runs/`; verify freshness before citing |
| [../backend/AGENTS.md](../backend/AGENTS.md) | Contributors | Engineering conventions for the Python backend |

## Versioning Note

These documents describe the repository **as built**. For regulated use, align
them with your firm's SDLC, approval workflow, and independent verification of
behaviour.
