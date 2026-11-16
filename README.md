# Solution GitOps

A Bifrost Solution that makes Git the deploy mechanism for Midtown's Bifrost Solution installs.
**Merging to an install's connected branch deploys it.**

Bifrost already lets an install be *Git-connected*. The repository and ref become its only
writer; `bifrost solution deploy` is refused, and each sync full-replaces the install's managed
definitions from that ref. But the platform never applies a sync by itself. It only checks every
six hours whether `version:` in `bifrost.solution.yaml` went up, and flags the install. This
Solution closes that loop:

| Trigger | What happens |
|---|---|
| GitHub `push` webhook (HMAC-verified) | `gitops_github_push` finds Git-connected installs following the pushed repository and branch or tag, and queues `POST /api/solutions/{id}/sync` for those whose policy is `auto`. For monorepo installs, a push that didn't touch the install's subpath is skipped, but only when the change set is known. |
| Hourly schedule | `gitops_reconcile` syncs `auto` installs that the platform reports as behind (`update_available_version`). This is the backstop for missed webhooks. |
| Operator / agent | `gitops_status` lists every install with its Git connection, versions, pending update and policy. |

Every decision is appended to the admin-only `gitops-events` table.

## Safety model

- **A webhook is only a trigger.** No payload content is deployed. A sync clones the install's
  *own* configured repository and ref, so a forged or replayed request can at most redeploy what's
  already authoritative. Replays are also deduplicated by `X-GitHub-Delivery`.
- **Opt-in per install** in [`modules/policy.py`](modules/policy.py). Installs are keyed by ID, or
  by slug only when exactly one install has it. Everything else defaults to `notify`.
- **Workflows request restricted powers:** `solutions.read`, `solutions.deploy`,
  `tables.readwrite`. They're runnable directly by platform admins only.
- **Protect the deploy branches** of every connected repository: require a PR and green CI.
  Merge rights on those branches are deploy rights.

## Conventions for Git-connected Solutions

- Bump `version:` in `bifrost.solution.yaml` for every deployable change. In forks of upstream
  Solutions, use PEP 440 local labels (`0.1.3+mtg.1`). Upstream `0.1.4` still sorts above them.
- One deploy branch per environment (e.g. `mtg`). Feature branches start from the upstream-tracking
  branch when the change could be proposed upstream.

## Development

```bash
python -m pip install pytest pyyaml packaging "https://bifrost.midtowntg.com/api/cli/download/bifrost-cli.tar.gz"
python -m pytest -q tests
```

Operating, onboarding and emergency procedures: [docs/RUNBOOK.md](docs/RUNBOOK.md).
