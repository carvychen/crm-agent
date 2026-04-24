# Monorepo integration — design decisions (session 2026-04-24)

**Status**: design interview complete. PRD filed as [carvychen/agent-platform#1](https://github.com/carvychen/agent-platform/issues/1). Ready for implementation (three sequenced PRs, see bottom).

**Context**: merging two repos — `carvychen/crm-agent` (this one, a reference MCP + agent runtime for Lenovo Dynamics 365) and `carvychen/agent-platform` (a FastAPI + React skill hub, Phase C.2 roadmap already anticipates MCP/Prompt/Agent "Coming Soon" nav). End state: `agent-platform` becomes the canonical monorepo; `crm-agent` is absorbed as a runtime subtree.

## Decisions (14 total, resolved in order)

| # | Decision | Why |
|---|---|---|
| 1 | **One monorepo**: `agent-platform` is the canonical repo; `crm-agent` absorbs into it | Platform + runtime share Azure tenancy, auth patterns, coordinated versioning; splitting across repos creates artificial seams |
| 2 | **Platform backend = admin plane only** (CRUD for Skill / MCP / Prompt / Agent artifacts). No `/mcp`, no `/api/chat` in `backend/app/`. | Skill Hub's existing shape (pure CRUD over Blob) is the right template for the other hubs; keeps platform lightweight and runtime dependencies out of its graph |
| 3 | **Runtime in same monorepo, different deploy**: one git repo, different Azure Function Apps | Monorepo = coordinated versioning + unified docs + one PR flow. Deploy-time separation keeps admin failures from taking out runtime and vice versa. |
| 4 | **Vertical-sliced backend** — `backend/app/{core, skills, mcps, prompts, agents}/`, each self-contained (router + service + models inside) | Matches the original ask ("each module at the same level in its own folder"); current horizontal slicing (routers/, services/, models/) spreads each hub across three files |
| 5 | **Runtime is self-contained; platform manages OTHER artifacts** — `runtimes/crm-agent/` ships its own skill bundle, prompts, MCP server, agent wiring. Platform hubs start empty; populated only when users build new verticals. | No bootstrap problem; no dual source-of-truth; reference runtime has zero platform dependencies. Matches the CONTEXT.md invariant "layers communicate only through documented contracts". |
| 6 | **Scope of this merge: structural only** — migrate + reorganize + stub `mcps/ prompts/ agents/` returning 501. Actually implementing CRUD for the three new hubs is future slices. | Keeps the merge focused and reviewable. Each future hub scoped as its own PR. |
| 7 | **Naming convention**: platform side = "Skill Hub / MCP Hub / Prompt Hub / Agent Hub". Runtime content = "skill bundle / MCP server / prompt set / agent definition". Never overload "module". | Resolves the Hub-vs-Bundle ambiguity before it infects architecture conversations. Matches `agent-platform`'s existing `SkillAdmin` / `SkillUser` role names. |
| 8 | **Runtime subtree name**: `runtimes/crm-agent/` (not `reference/`, not `apps/`) | Architecturally accurate; accommodates future non-reference runtimes without renaming. |
| 9 | **Python 3.11 unified** across admin and runtime | Simpler dev experience; nothing in agent-platform's current deps looks 3.11-incompatible; Azure Functions Python 3.11 is GA per Slice 12. |
| 10 | **No shared Python package**. Each deployable owns its own auth/config code; contract documented in an ADR. | Deployable-independence > DRY for this team size. Matches invariant 1. Overlap is (best guess) small anyway — crm-agent's auth is outbound OBO, admin plane's auth is inbound JWT validation; different operations. |
| 11 | **No orchestrator stub**. Skip until a concrete use case appears. | Stubbing an empty folder with no semantic definition invites name-squatting design debates. If "orchestrator = agent that calls other agents", it fits under Agent Hub. |
| 12 | **All eight existing ADRs (0001–0008) stay under `runtimes/crm-agent/docs/adr/`**. Root `docs/adr/` starts empty. | Re-scoping runtime ADRs post-hoc risks losing nuance. Admin plane will produce its own ADRs when relevant. |
| 13 | **Two contexts with a root `CONTEXT-MAP.md`**: `backend/app/CONTEXT.md` (admin) + `runtimes/crm-agent/docs/CONTEXT.md` (runtime, moved intact from today's file) | Disjoint vocabularies — gluing them into a unified CONTEXT.md would read as two concatenated dictionaries. |
| 14 | **Three sequenced PRs** on `agent-platform`: (1) vertical-slice reorg + stubs; (2) `git filter-repo` migration of crm-agent into `runtimes/`; (3) CONTEXT-MAP + README + frontend Coming Soon wiring | Each PR independently reviewable. PR1 is pure admin refactor; PR2 is pure history rewrite + move; PR3 is pure docs/UX. |

## End-state top-level layout

```
agent-platform/
├── backend/
│   ├── README.md
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   ├── .env.example
│   └── app/
│       ├── core/             # main.py, config.py, auth/ — shared across hubs
│       ├── skills/           # existing Skill Hub reorganized here
│       │   ├── router.py
│       │   ├── service.py    # blob storage operations
│       │   ├── install_token.py
│       │   ├── validator.py
│       │   └── models.py
│       ├── mcps/             # stub → 501 Coming Soon
│       │   ├── router.py
│       │   └── models.py
│       ├── prompts/          # stub
│       │   ├── router.py
│       │   └── models.py
│       └── agents/           # stub
│           ├── router.py
│           └── models.py
├── frontend/                 # unchanged structure; Coming Soon placeholders link to new stub endpoints
├── runtimes/
│   └── crm-agent/            # migrated from carvychen/crm-agent, history preserved
│       ├── function_app.py
│       ├── host.json
│       ├── pyproject.toml
│       ├── requirements.txt
│       ├── requirements-dev.txt
│       ├── src/              # mcp_server.py, agent/, auth.py, config.py, dataverse_client.py, preflight/, flex_asgi.py
│       ├── skills/crm-opportunity/
│       ├── infra/            # Bicep
│       ├── tests/
│       ├── docs/             # CONTEXT.md, adr/ (0001–0008), deployment/, operations/, planning/
│       └── scripts/
├── docs/                     # cross-cutting only (empty at start)
│   └── adr/                  # cross-cutting ADRs (empty at start)
├── wiki/                     # unchanged
├── CONTEXT-MAP.md            # NEW — points at backend and runtime as distinct contexts
└── README.md                 # rewritten to describe the monorepo's two parts
```

## PR plan

### PR1 — `backend/app/` vertical-slice reorg + stub hubs

In `agent-platform` repo, branch `feat/backend-vertical-slices`.

- Move existing skill-hub code:
  - `backend/app/routers/skills.py` → `backend/app/skills/router.py`
  - `backend/app/services/blob_storage.py` → `backend/app/skills/service.py`
  - `backend/app/services/install_token.py` → `backend/app/skills/install_token.py`
  - `backend/app/services/skill_validator.py` → `backend/app/skills/validator.py`
  - `backend/app/models/skill.py` → `backend/app/skills/models.py`
- Create `backend/app/core/` for `config.py` and `auth/dependencies.py` (unchanged content).
- Update `backend/app/main.py` imports to match.
- Create `backend/app/mcps/`, `backend/app/prompts/`, `backend/app/agents/` each with:
  - `__init__.py`
  - `router.py` — one or two GET/POST endpoints returning `HTTPException(status_code=501, detail="Hub not yet implemented")` with a stable contract shape (empty list, Coming Soon message)
  - `models.py` — minimal Pydantic response model
- Register new routers in `backend/app/main.py`.
- All existing tests green (pytest in `backend/tests/`).
- Add smoke tests for the three new stub hubs (GET returns 501 / the Coming Soon contract).

### PR2 — `git filter-repo` migration of crm-agent into `runtimes/crm-agent/`

One-shot migration, no other changes. Branch `feat/migrate-crm-agent`.

- Clone `carvychen/crm-agent` fresh.
- Run `git filter-repo --to-subdirectory-filter runtimes/crm-agent` (on a copy; filter-repo is destructive on the source).
- Add rewritten repo as a remote on `agent-platform`.
- `git merge --allow-unrelated-histories` onto `main`.
- Verify `git log -- runtimes/crm-agent/src/auth.py` shows the full crm-agent PR history.
- Open PR; no code diffs, just a directory tree of files appearing at `runtimes/crm-agent/*` with authentic commit history.

### PR3 — Contexts, READMEs, frontend Coming Soon wiring

Branch `feat/integration-docs`.

- Create `CONTEXT-MAP.md` at repo root.
- Create `backend/app/CONTEXT.md` with admin-plane glossary (Hub / bundle / tenant / install-token / RBAC role / tid / oid).
- Verify `runtimes/crm-agent/docs/CONTEXT.md` (moved in PR2) is intact; cross-reference from CONTEXT-MAP.
- Rewrite root `README.md` to reflect monorepo structure; rewrite `backend/README.md` to point at new module layout; keep `runtimes/crm-agent/README.md` as-is from crm-agent's migrated history.
- Update `frontend/src/` nav: wire the Coming Soon nav items ("Agents", "Prompts", "MCP") to `/agents`, `/prompts`, `/mcps` routes that render a simple placeholder page, each calling its backend stub endpoint and displaying the 501 Coming Soon contract.
- Document the auth-contract ADR at root `docs/adr/0001-auth-contract-admin-vs-runtime.md` (title TBD) — captures decision #10 ("no shared auth package; each side owns its JWT validation; contract is 'validate per Entra JWKS and extract oid/tid/upn/roles'").
- Update `carvychen/crm-agent` repo README with a one-liner pointing to the new monorepo home and archive the repo after PR2 merges (optional; keeps the history accessible even without the redirect).

### Post-merge

- `carvychen/crm-agent` repo: mark archived on GitHub, update README with a pointer to `agent-platform/runtimes/crm-agent/`. History remains browsable; no further PRs land there.
- Close any open issues on crm-agent with a reference to where they'd land in the new monorepo (none are currently open).
- Update Lenovo handover / demo material to reference the monorepo URL and structure.

## Open items (non-blocking; decide during implementation)

- **Module contract enforcement** — what prevents `backend/app/skills/` from importing `backend/app/mcps/` internals? Options: none (trust convention); a pytest import-audit test; import-linter rules. Defer until we have more than one hub implemented — nothing to enforce today.
- **Multi-tenancy for runtime** — crm-agent is single-tenant (one Dataverse environment per deploy). If the platform admin is multi-tenant (`tid`-prefixed blob), and different Lenovo teams want different CRM agents, each team gets its own `runtimes/crm-agent/` deploy pointed at its own Dataverse. Nothing in the migration blocks this; the model just needs to be documented when the second deploy appears.
- **Frontend placeholder page** — minimum viable is a `<ComingSoon module="MCP" />` component rendering the 501 response. Can be iterated on during PR3 without blocking.
- **Orchestrator** — not stubbed. Revisit when a concrete composition use case appears.
