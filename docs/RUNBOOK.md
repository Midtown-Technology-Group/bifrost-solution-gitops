# Solution GitOps runbook

## Install the controller (once per instance)

The controller is itself Git-connected, so it updates itself on merge to `main`.

```bash
bifrost solution install-repo https://github.com/Midtown-Technology-Group/bifrost-solution-gitops --ref main
```

1. Approve the requested workflow permissions (`solutions.read`, `solutions.deploy`,
   `tables.readwrite`) in the install's settings.
2. Find the installed webhook source ID (`bifrost api GET /api/events/sources`; name
   `Solution GitOps: GitHub push`). The callback URL is `https://<instance>/api/hooks/<source-id>`.
3. Generate the signing secret: `POST /api/events/sources/<source-id>/rotate-secret` with `{}`.
   The response shows `raw_secret` once. Don't print it, paste it into chat, or commit it.
4. Add the GitHub webhook. Use one organization webhook, or one per connected repository:
   - payload URL: the callback URL;
   - content type: `application/json`;
   - secret: the value from step 3;
   - events: **Just the push event**.

   GitHub's `ping` delivery is accepted and ignored, because only `push` is subscribed.

## Onboard an install

1. **Make the source authoritative.** Sync full-replaces managed definitions, so entities missing
   from the ref are deleted. Confirm the ref matches what's deployed:
   - `bifrost solution export <install> --mode shareable` and diff it against the repository subpath;
   - or redeploy from that ref first.
2. **Private repositories** need a GitHub App *Solution Repositories* source (platform runbook
   `docs/runbooks/private-solution-repositories.md`). Public repositories need nothing.
3. **Connect:** `bifrost solution git connect <install-id> <repo-url> --ref <branch-or-tag> [--subpath <dir>]`.
4. **Protect the branch:** require a PR and passing CI, and enforce version bumps for deployable changes.
5. **Opt in:** add the install ID to `modules/policy.py` with `auto` and merge. The controller
   then syncs itself, so the new policy is live after that sync.

## Pin an install to releases instead of a branch

`bifrost solution git connect <install-id> <repo-url> --ref v1.2.3`, then sync. Pushing a new tag
doesn't move the install. To upgrade, reconnect it with the new tag; the reconnect is the reviewed,
deliberate step.

## Emergency stop

- **One install:** set it to `notify` in `modules/policy.py` and merge. If the controller itself is
  broken, use the next step.
- **Everything:** deactivate the `Solution GitOps: GitHub push` and `hourly reconcile` event sources
  in the UI. That's instance state, so it survives until a deploy resets it; record why in an issue.
- **Roll back a bad deploy:** revert the merge on the deploy branch. Sync applies older descriptor
  versions too; it has no downgrade gate.
- **Take an install off GitOps entirely:** `bifrost solution git disconnect <install-id>`.
  `bifrost solution deploy` works again afterwards.

## Platform behaviour this design relies on (checked against gobifrost/bifrost main, 2026-11-16)

- **Update detection:** the update check reads the descriptor `version:` at the ref's latest
  commit, every 6 hours. Git tags and GitHub Releases aren't consulted. It sets
  `update_available_version` and emits `solution.update_available`.
- **No auto-pull:** nothing in the platform calls sync automatically, despite a stale docstring in
  `git_sync.py`. Hence this controller.
- **Built-in topics can't reach Solution sources:** built-in platform topics are delivered only to a
  topic source that no Solution owns, at most one per topic. A Solution manifest therefore can't
  subscribe to `solution.update_available`; the hourly reconcile replaces that.
- **Sync semantics:** sync is a durable platform job, serialized with deploys and App SDK updates,
  and deduplicated per install. A newer commit arriving mid-sync is picked up by a rerun.

## Production (bifrost.midtowntg.com)

| Item | Value |
|---|---|
| Install | `595787c4-ee7c-4ca1-8817-5f8c19789a40` (provider org), Git-connected to this repo at `main` |
| Webhook source | `77ca0fcd-d0fa-542f-9030-c3bfb5072d10`, callback `https://bifrost.midtowntg.com/api/hooks/77ca0fcd-d0fa-542f-9030-c3bfb5072d10` |
| GitHub webhooks | Repository hooks on `Midtown-Technology-Group/docs` and `Midtown-Technology-Group/bifrost-solution-gitops` (push only). An organization hook needs a token with `admin:org_hook`; until then, add a repository hook when onboarding. |
| Secret | Rotated 2026-11-16 and stored only in Bifrost and the GitHub hooks. Rotate by generating a new value, `POST …/rotate-secret {"secret": …}`, and updating every hook in the same change window. |
