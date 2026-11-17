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
3. List the repositories in [`scripts/hooked_repos.txt`](../scripts/hooked_repos.txt), then run
   `python scripts/rotate_webhook_secret.py --source-id <source-id>`. It generates a secret,
   rotates it into Bifrost, and creates or updates a push-only hook on every listed repository.
   The secret is never printed. It needs repository admin only, not `admin:org_hook`.

   GitHub's `ping` delivery is accepted and ignored, because only `push` is subscribed.

## Onboard an install

1. **Make the source authoritative.** Sync full-replaces managed definitions, so entities missing
   from the ref are deleted. Confirm the ref matches what's deployed:
   - `bifrost solution export <install> --mode shareable` and diff it against the repository subpath;
   - or redeploy from that ref first.
2. **Private repositories** need a GitHub App *Solution Repositories* source in the install's
   scope. Public repositories need nothing. Create the sources with:

   ```bash
   python scripts/bootstrap_solution_source.py --repo <owner>/<repo> \
       --scope 00000000-0000-0000-0000-000000000002 --scope GLOBAL
   ```

   Everything on the Bifrost side is REST. GitHub insists on a person confirming three steps:
   creating the App, authorizing it, and installing it. The script opens each step in the browser
   and continues by itself. Notes:
   - Each scope gets its own App, because Bifrost reuses an App only within the source's own scope.
   - Rerunning the script for a new repository reuses that scope's App.
   - Sources bind to the repository's `owner/name`, so (re)create them after a transfer.
   - Prod's 2.0.1 CLI has no `--git-source-id`. Use REST
     (`PATCH /api/solutions/{id}` with `git_repo_url`, `repo_subpath`, `git_ref` and `git_source_id`)
     or a newer CLI.
3. **Connect:** `bifrost solution git connect <install-id> <repo-url> --ref <branch-or-tag> [--subpath <dir>]`.
4. **Protect the branch:** require a PR and passing CI, and enforce version bumps for deployable changes.
   Add the repository to `scripts/hooked_repos.txt` and rerun `scripts/rotate_webhook_secret.py`.
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
| GitHub webhooks | Repository hooks on every repository in `scripts/hooked_repos.txt` (push only), managed by `scripts/rotate_webhook_secret.py`. No organization hook, so no `admin:org_hook` token scope. |
| Secret | Stored only in Bifrost and the GitHub hooks. Rotate with `scripts/rotate_webhook_secret.py`; last rotated 2026-11-17. |
