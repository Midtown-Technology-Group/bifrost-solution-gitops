"""Decision logic for Solution GitOps.

Pure functions only: no Bifrost SDK imports, so every rule here is unit-tested
without an instance. The workflows in ``functions/gitops.py`` fetch installs,
call these functions, and then act on the decisions.

Safety model: a GitHub push is only a *trigger*. Nothing in the payload is
deployed. A sync makes the platform clone the install's own configured
repository and ref, so a forged or replayed webhook can at worst cause a
redundant redeploy of source that is already authoritative.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

AUTO = "auto"
NOTIFY = "notify"
MODES = frozenset({AUTO, NOTIFY})

SYNC = "sync"
SKIP = "skip"

# GitHub caps the commits array in a push webhook; at the cap the file lists
# are not a complete picture of what changed.
_GITHUB_PUSH_COMMIT_CAP = 2048

_SCP_LIKE = re.compile(r"^(?:[\w.-]+@)?(?P<host>[\w.-]+):(?P<path>[^/].*)$")


def normalize_repo_url(url: Any) -> str | None:
    """Reduce any Git remote spelling to ``host/owner/repo`` (lowercase).

    Handles https/http/git/ssh URLs, scp-like ``git@host:owner/repo``,
    credentials, ports, trailing slashes and a ``.git`` suffix.
    """
    if not isinstance(url, str):
        return None
    text = url.strip()
    if not text:
        return None
    if "://" in text:
        rest = text.split("://", 1)[1]
        authority, _, path = rest.partition("/")
        host = authority.rsplit("@", 1)[-1]
    else:
        match = _SCP_LIKE.match(text)
        if match is None:
            return None
        host, path = match.group("host"), match.group("path")
    host = host.split(":", 1)[0].lower()
    path = path.split("?", 1)[0].split("#", 1)[0].strip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if not host or len(parts) < 2:
        return None
    return f"{host}/{'/'.join(parts)}".lower()


@dataclass(frozen=True)
class PushEvent:
    """The parts of a GitHub ``push`` webhook that GitOps decisions use."""

    repo_keys: frozenset[str]
    repo_display: str
    ref_kind: str  # "branch" | "tag"
    ref_name: str
    default_branch: str | None
    deleted: bool
    created: bool
    forced: bool
    after: str | None
    # None means "unknown": treat every path as possibly changed.
    changed_paths: frozenset[str] | None


def _changed_paths(payload: Mapping[str, Any]) -> frozenset[str] | None:
    if payload.get("forced") or payload.get("created") or payload.get("deleted"):
        return None
    commits = payload.get("commits")
    if not isinstance(commits, list) or not commits or len(commits) >= _GITHUB_PUSH_COMMIT_CAP:
        return None
    paths: set[str] = set()
    for commit in commits:
        if not isinstance(commit, Mapping):
            return None
        for key in ("added", "removed", "modified"):
            value = commit.get(key)
            if not isinstance(value, list):
                return None
            paths.update(str(item) for item in value if isinstance(item, str))
    return frozenset(paths)


def parse_github_push(payload: Any) -> PushEvent:
    """Validate and project a GitHub push payload. Raises ``ValueError``."""
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a JSON object")
    ref = payload.get("ref")
    if not isinstance(ref, str):
        raise ValueError("payload has no ref")
    if ref.startswith("refs/heads/"):
        kind, name = "branch", ref[len("refs/heads/"):]
    elif ref.startswith("refs/tags/"):
        kind, name = "tag", ref[len("refs/tags/"):]
    else:
        raise ValueError("ref is neither a branch nor a tag")
    if not name:
        raise ValueError("ref name is empty")
    repo = payload.get("repository")
    if not isinstance(repo, Mapping):
        raise ValueError("payload has no repository")
    keys = {
        normalize_repo_url(repo.get(field))
        for field in ("clone_url", "html_url", "git_url", "ssh_url", "url")
    }
    keys.discard(None)
    if not keys:
        raise ValueError("repository has no usable URL")
    default_branch = repo.get("default_branch")
    after = payload.get("after")
    return PushEvent(
        repo_keys=frozenset(k for k in keys if k),
        repo_display=str(repo.get("full_name") or sorted(k for k in keys if k)[0]),
        ref_kind=kind,
        ref_name=name,
        default_branch=default_branch if isinstance(default_branch, str) and default_branch else None,
        deleted=bool(payload.get("deleted")),
        created=bool(payload.get("created")),
        forced=bool(payload.get("forced")),
        after=after if isinstance(after, str) else None,
        changed_paths=_changed_paths(payload),
    )


@dataclass(frozen=True)
class Decision:
    install_id: str
    slug: str
    action: str  # SYNC | SKIP
    reason: str


def validate_policy(policy: Mapping[str, str]) -> None:
    """Raise ``ValueError`` for an unusable policy (checked in tests and at runtime)."""
    for key, mode in policy.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("policy keys must be install ids, slugs or '*'")
        if mode not in MODES:
            raise ValueError(f"policy mode for {key!r} must be one of {sorted(MODES)}")


def policy_mode(policy: Mapping[str, str], install: Mapping[str, Any], installs: Iterable[Mapping[str, Any]]) -> str:
    """Resolve an install's mode: install id, then a *unique* slug, then '*'.

    A slug shared by several installs never opts any of them in; name the
    install id instead. Unlisted installs default to notify-only.
    """
    install_id = str(install.get("id") or "")
    if install_id and install_id in policy:
        return policy[install_id]
    slug = install.get("slug")
    if isinstance(slug, str) and slug in policy:
        same_slug = [item for item in installs if item.get("slug") == slug]
        return policy[slug] if len(same_slug) == 1 else NOTIFY
    return policy.get("*", NOTIFY)


def _strip_ref(ref: str) -> str:
    for prefix in ("refs/heads/", "refs/tags/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def ref_matches(install: Mapping[str, Any], push: PushEvent) -> bool:
    configured = install.get("git_ref")
    if isinstance(configured, str) and configured.strip():
        return _strip_ref(configured.strip()) == push.ref_name
    # No configured ref: the platform follows the repository default branch.
    return push.ref_kind == "branch" and push.default_branch is not None and push.ref_name == push.default_branch


def subpath_touched(install: Mapping[str, Any], push: PushEvent) -> bool:
    subpath = install.get("repo_subpath")
    subpath = subpath.strip().strip("/") if isinstance(subpath, str) else ""
    if not subpath or push.changed_paths is None:
        return True
    prefix = f"{subpath}/"
    return any(path == subpath or path.startswith(prefix) for path in push.changed_paths)


def _identity(install: Mapping[str, Any]) -> tuple[str, str]:
    return str(install.get("id") or ""), str(install.get("slug") or "")


def plan_push(installs: list[Mapping[str, Any]], push: PushEvent, policy: Mapping[str, str]) -> list[Decision]:
    """Decide which installs a push should sync. Unrelated installs are omitted."""
    validate_policy(policy)
    decisions: list[Decision] = []
    for install in installs:
        if not install.get("git_connected"):
            continue
        if normalize_repo_url(install.get("git_repo_url")) not in push.repo_keys:
            continue
        if not ref_matches(install, push):
            continue
        install_id, slug = _identity(install)
        if push.deleted:
            decisions.append(Decision(install_id, slug, SKIP, "ref was deleted; install stays on its last deployed commit"))
            continue
        status = install.get("status")
        if status not in (None, "active"):
            decisions.append(Decision(install_id, slug, SKIP, f"install status is {status!r}"))
            continue
        mode = policy_mode(policy, install, installs)
        if mode != AUTO:
            decisions.append(Decision(install_id, slug, SKIP, "not opted in to automatic sync (policy: notify)"))
            continue
        if not subpath_touched(install, push):
            decisions.append(Decision(install_id, slug, SKIP, "push did not change the install's repository subpath"))
            continue
        short = (push.after or "")[:12] or "unknown"
        decisions.append(Decision(install_id, slug, SYNC, f"push to {push.ref_kind} {push.ref_name} at {short}"))
    return decisions


def plan_reconcile(installs: list[Mapping[str, Any]], policy: Mapping[str, str]) -> list[Decision]:
    """Backstop for missed webhooks: sync opted-in installs the platform flags as behind.

    The platform's update check (every 6 hours) compares the descriptor
    ``version:`` at the connected ref with the installed version and sets
    ``update_available_version``.
    """
    validate_policy(policy)
    decisions: list[Decision] = []
    for install in installs:
        available = install.get("update_available_version")
        if not install.get("git_connected") or not available:
            continue
        install_id, slug = _identity(install)
        installed = install.get("version") or "unversioned"
        if install.get("status") not in (None, "active"):
            decisions.append(Decision(install_id, slug, SKIP, f"install status is {install.get('status')!r}"))
        elif policy_mode(policy, install, installs) != AUTO:
            decisions.append(Decision(install_id, slug, SKIP, f"update {installed} -> {available} available; policy is notify"))
        else:
            decisions.append(Decision(install_id, slug, SYNC, f"update {installed} -> {available} available"))
    return decisions


def status_rows(installs: list[Mapping[str, Any]], policy: Mapping[str, str]) -> list[dict[str, Any]]:
    """One row per install for operators: Git connection, versions and policy."""
    rows = []
    for install in installs:
        install_id, slug = _identity(install)
        rows.append(
            {
                "install_id": install_id,
                "slug": slug,
                "scope": install.get("scope"),
                "organization_id": install.get("organization_id"),
                "git_connected": bool(install.get("git_connected")),
                "repository": normalize_repo_url(install.get("git_repo_url")),
                "ref": install.get("git_ref"),
                "subpath": install.get("repo_subpath"),
                "version": install.get("version"),
                "update_available_version": install.get("update_available_version"),
                "policy": policy_mode(policy, install, installs),
                "status": install.get("status"),
            }
        )
    rows.sort(key=lambda row: (not row["git_connected"], row["slug"], row["install_id"]))
    return rows
