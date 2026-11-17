"""Which Solution installs Bifrost may update automatically from Git.

Keys are install ids (preferred) or slugs. A slug only applies when exactly one
install has it; otherwise name the install id. ``"*"`` sets the default.

Modes:
- ``auto``: a push to the install's connected ref, or a newer descriptor
  version found by the platform's update check, queues a sync.
- ``notify``: record the event only. An operator runs
  ``bifrost solution sync <install>``.

Changing this file is itself a GitOps change: merge it and this Solution
syncs itself.
"""

POLICY: dict[str, str] = {
    "*": "notify",
    # This controller (Midtown-Technology-Group/bifrost-solution-gitops @ main).
    "solution-gitops": "auto",
    # Bifrost Docs, provider org (Midtown-Technology-Group/docs @ mtg).
    "44832b44-87db-4837-afab-b22fc47f88db": "auto",
}
