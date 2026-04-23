# Dual-cloud parity discipline

Development happens in Azure Global (author's personal subscription, no access to the customer's environment) while production runs in Azure China (Lenovo landing zone). These are physically separate clouds with different endpoints, authorities, and feature rollout timelines. To avoid surprise failures at handover — which we cannot debug remotely — code and infrastructure are cloud-neutral: every cloud-specific value is a configuration input, and we develop against the intersection of both clouds' capabilities.

## Considered Options

- **Develop against Global, port to China at handover** — rejected. Late-stage porting surfaces bugs during the customer's UAT, which we cannot access to fix. Invariant 4 (delivered blind) makes this unacceptable.
- **Develop directly in Azure China** — rejected. Requires a paid CN subscription and a CN tenant we don't have.
- **Dual-track codebase with cloud-specific branches** — rejected. Doubles maintenance; violates invariant 3 and guarantees drift.

## Consequences

- **No preview features.** MCP extension, Flex Consumption in regions not yet GA in China, Entra preview flows are all off-limits regardless of their availability in Global.
- **Every cloud-specific value is parameterised.** See `src/config.py` for the authoritative list: authority, Dataverse suffix, FIC audience, LLM endpoint, Log Analytics endpoint.
- **CI must render both cloud configurations.** `bicep what-if` runs with `CLOUD_ENV=global` and `CLOUD_ENV=china` as a pre-merge check.
- **Pre-flight script is mandatory.** `scripts/preflight.py` verifies endpoint reachability, AAD / FIC configuration, and Dataverse application-user setup on the target cloud before any real deployment. This is how we compensate for not having access.
- **Error messages explicitly mention both clouds** where the failure mode differs (e.g. FIC audience mismatch, authority mismatch).
