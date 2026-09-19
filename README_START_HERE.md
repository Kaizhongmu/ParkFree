# Parking Intelligence System — Codex Starter Pack

This starter pack is designed to be dropped into a new Git repository and opened with Codex.

## Files

### `AGENTS.md`
Repository-level instructions for Codex and other coding agents.

Place at:

```text
./AGENTS.md
```

### `IMPLEMENTATION_SPEC.md`
The long-term engineering and product specification.

Place at:

```text
./docs/IMPLEMENTATION_SPEC.md
```

### `PHASE_01_PROMPT.md`
The first implementation task for Codex.

It intentionally limits Codex to Phase 0 + Phase 1:

- repository foundation;
- FastAPI;
- PostgreSQL/PostGIS;
- SQLAlchemy;
- Alembic;
- domain schemas;
- persistence models;
- tests;
- documentation.

## Recommended setup

```bash
mkdir parking-intelligence
cd parking-intelligence
git init
mkdir -p docs
```

Copy the files into this structure:

```text
parking-intelligence/
├── AGENTS.md
├── PHASE_01_PROMPT.md
└── docs/
    └── IMPLEMENTATION_SPEC.md
```

Then open the `parking-intelligence` repository in Codex.

## First instruction to Codex

You can paste this directly:

```text
Read AGENTS.md, docs/IMPLEMENTATION_SPEC.md, and PHASE_01_PROMPT.md completely.

Execute PHASE_01_PROMPT.md exactly.
Implement Phase 0 and Phase 1 only.
Do not proceed to Phase 2.
Run all relevant tests and migrations before reporting completion.
```

## Recommended workflow after Phase 1

1. Review Codex's changed files.
2. Check the tests it actually ran.
3. Run the project locally.
4. Commit the Phase 0/1 foundation.
5. Only then create the Phase 2 task for the SMU GIS candidate-segment vertical slice.

Do not ask Codex to build the entire parking product in one prompt.
