# Two-tier testing: unit (mocked) + live (real tenant)

Framework-level tests (mocks at every boundary) prove the code compiles and the wire format is parsed, but they cannot tell us the thing Lenovo actually cares about: whether the reference agent, when deployed to a real Azure tenant, successfully reaches Dataverse and returns the right records. To close that gap, every slice ships two layers of tests — fast unit tests with mocks, and a live-integration layer that exercises real Entra / Dataverse / Foundry / MCP against the author's Azure Global dev tenant. A PR does not merge until both layers are green.

## Considered Options

- **Unit-only (current)** — rejected as insufficient. Every test passes today on `httpx.MockTransport` / `respx`; we have no evidence that the code actually talks to Azure correctly. Invariant 4 (delivered blind) makes this level of uncertainty unacceptable.
- **Live-only, no unit layer** — rejected. Live tests are 10–100× slower and depend on network; fast-feedback loops on edge cases and error branches would suffer. Both layers are needed.
- **Live tests only on nightly / manual trigger** — rejected. Issues that live-only tests catch (mis-wired auth, wrong scopes, OData shape drift) need to fail PRs at review time, not surface a day later when memory has faded.
- **Stand up a WIF environment before testing anything** — rejected for the walking skeleton. Configuring Federated Identity Credentials + Dataverse application user takes calendar time and is blocked on tenant admin availability. We ship a dev-mode auth path that exercises the same MCP server / Dataverse client / agent code paths using the legacy `client_credentials` flow against the same dev tenant. WIF verification is Slice 8's pre-flight script against a customer tenant.

## Consequences

### `AUTH_MODE` switch in `src/auth.py`

- `AUTH_MODE=obo` (default in deployed code, unchanged from ADR 0001) — Managed Identity → Federated Identity Credential → OBO exchange of the inbound user JWT → Dataverse-scoped token. Production path.
- `AUTH_MODE=app_only_secret` (dev + CI integration tests only) — ignores any inbound user JWT, uses `AZURE_CLIENT_ID` + `AZURE_CLIENT_SECRET` + `AZURE_TENANT_ID` to run the OAuth 2.0 `client_credentials` flow and return an app-only Dataverse-scoped token. The authority / scope are still pulled from `CloudConfig` (ADR 0003).
- `function_app.py` in production MUST refuse to boot with `AUTH_MODE=app_only_secret`. The refusal is enforced by a startup assertion plus a preflight check (Slice 8, #10).
- **Known gap:** `AUTH_MODE=app_only_secret` runs every Dataverse call as the service account, so **Dataverse row-level security does not filter per-user** the way it does in production. US 2 ("sales rep sees only their opportunities") therefore cannot be verified by the live layer in dev mode. It is verified by:
  1. Unit tests over `DataverseAuth.get_dataverse_token` that assert the OBO request payload is constructed correctly (existing).
  2. Slice 8 pre-flight + a manual user-identity smoke test against a customer tenant once WIF is configured.

### `tests/unit/` (fast, always run)

- Mocks at every external boundary (`respx` for httpx, `httpx.MockTransport` for in-process stubs, AF test doubles).
- Deterministic, offline, no secrets required.
- Target: whole suite under 5 s on CI.

### `tests/integration/` (live, always run on PR)

- Real HTTPS to Entra ID + Dataverse Web API + Azure AI Foundry.
- Drives the same `src/asgi.py` Starlette app; no in-process shortcuts.
- Secrets sourced from `skills/crm-opportunity/.env` locally or GitHub repo secrets in CI. Test module imports `conftest.py` which reads both files with `python-dotenv` if present and skips the entire integration layer when required vars are absent (so local-unit-only contributors are not blocked).
- Data contract: every test that writes to Dataverse uses a record name prefixed `CRM-Agent-Test-<uuid4>` and deletes it in `finally:`. A nightly `cleanup-stale-test-records` job (defined in Slice 9 #11) deletes any orphans as a safety net.
- The GitHub Actions `integration` job is **required** to merge a PR (branch protection rule referenced in Slice 9's Bicep / repo-settings docs).

### Cost envelope

- Per live run: a handful of Foundry chat completions, at most a dozen Dataverse read/write round-trips. Expected ≤ $0.10 per PR run at current pricing; tests set `max_tokens=50` and use small prompts.
- Cost caps: CI workflow sets a per-day spending alert on the Foundry resource (Slice 9 Bicep). Any single test that exceeds 10 s against Foundry is considered a bug and fails the job.

### Future slices

- **Every slice from #5 onward adds at least one `tests/integration/` test** covering the real-tenant behaviour of whatever it adds (new MCP tool, new LLM provider, new Bicep resource, etc.). The issue's acceptance criteria is updated to include this (actioned in a separate PR after this ADR lands).
- Slice 8 (#10) pre-flight script is the customer-facing analogue of the integration suite; it verifies the same chains against a customer tenant that the authors cannot access. Integration tests and preflight share underlying utilities where it makes sense.
