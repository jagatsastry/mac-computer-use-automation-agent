# Documentation Index

Documentation for the macOS automation agent, organized by category.

## Quick Links

- **[QUICKSTART](guides/QUICKSTART.md)** — Runbook and setup
- **[Automation Guide](guides/automation.md)** — Current setup and configuration
- **[Life of a Prompt](guides/LIFE_OF_A_PROMPT.md)** — Architecture walkthrough

## Directory Structure

| Directory | Contents |
|-----------|----------|
| **architecture/** | Design docs, agent flow, competitive landscape |
| **guides/** | User-facing guides (QUICKSTART, automation, prompts) |
| **features/** | Feature specs, PRDs, and supporting docs by initiative |
| **reports/** | Customer test reports, consultant reviews, assessments |
| **benchmarks/** | Grounding specs, model comparisons, benchmark how-tos |
| **plans/** | Planning documents and design proposals |
| **research/** | Research notes and landscape analysis |
| **accessibility/** | AX dumps, browser accessibility research |
| **features-todo/** | Backlog and future feature ideas |

## Features

Feature docs live under `features/<initiative>/` with consistent naming:

- `*-prd.md` — Product requirements
- `*-spec.md` — Technical specification
- `*-sota.md` — State-of-the-art research
- `*-codebase.md` — Codebase analysis
- `*-customer-report.md` — Customer test results

### Initiatives

- **adaptive-skill-system** — Skill learning and promotion pipeline
- **replan-patch** — Replanning and patch injection
- **multi-path-skills** — Multi-path skill execution
- **speed-phase1** — Tier 1 verification and speed optimizations
- **verification-fixes** — Amazon return verification fixes
- **walmart-return-fixes** — Walmart return workflow fixes
- **target-buy-fixes** — Target buy workflow fixes
- **survey-gaps** — Survey gap analysis and fixes
- **type-text-hardening** — Type-text action hardening
- **scroll-action** — Scroll action support
- **skill-librarian** — Skill librarian and promotion
- **vision-arch** — Vision architecture improvements
- **restaurant-skills** — Restaurant ordering skills

## Reports

- **Customer test reports** — `reports/amazon-return/`, `reports/walmart-return/`, `reports/target-buy-bedsheets/`, etc.
- **Consultant reviews** — `reports/consultant-reviews/`
- **Assessments** — `reports/buildml-agent-assessment.md`, `reports/buildml-checklist-assessment.md`
