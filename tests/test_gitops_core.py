import pytest

from modules.gitops_core import (
    AUTO,
    NOTIFY,
    SKIP,
    SYNC,
    normalize_repo_url,
    parse_github_push,
    plan_push,
    plan_reconcile,
    policy_mode,
    status_rows,
    validate_policy,
)
from modules.policy import POLICY

DOCS = "https://github.com/Midtown-Technology-Group/docs"


def push(ref="refs/heads/mtg", commits=None, **extra):
    payload = {
        "ref": ref,
        "after": "abc123def4567890",
        "repository": {
            "full_name": "Midtown-Technology-Group/docs",
            "clone_url": f"{DOCS}.git",
            "html_url": DOCS,
            "ssh_url": "git@github.com:Midtown-Technology-Group/docs.git",
            "default_branch": "main",
        },
        "commits": commits if commits is not None else [{"added": [], "removed": [], "modified": ["functions/catalog.py"]}],
    }
    payload.update(extra)
    return parse_github_push(payload)


def install(**overrides):
    base = {
        "id": "i-docs",
        "slug": "bifrost-docs",
        "git_connected": True,
        "git_repo_url": DOCS,
        "git_ref": "mtg",
        "repo_subpath": None,
        "status": "active",
        "version": "0.1.3",
        "update_available_version": None,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "url",
    [
        DOCS,
        f"{DOCS}.git",
        f"{DOCS}/",
        "git@github.com:Midtown-Technology-Group/docs.git",
        "ssh://git@github.com:22/Midtown-Technology-Group/docs.git",
        "https://user:token@GitHub.com/midtown-technology-group/DOCS",
        "git://github.com/Midtown-Technology-Group/docs.git",
    ],
)
def test_normalize_repo_url_equivalents(url):
    assert normalize_repo_url(url) == "github.com/midtown-technology-group/docs"


@pytest.mark.parametrize("url", [None, "", "docs", "https://github.com/onlyowner", 42])
def test_normalize_repo_url_rejects(url):
    assert normalize_repo_url(url) is None


def test_parse_rejects_non_push_shapes():
    for bad in [None, [], {}, {"ref": "refs/pull/1/head", "repository": {"html_url": DOCS}},
                {"ref": "refs/heads/mtg"}, {"ref": "refs/heads/mtg", "repository": {"html_url": "x"}}]:
        with pytest.raises(ValueError):
            parse_github_push(bad)


def test_push_to_followed_branch_syncs_auto_install():
    decisions = plan_push([install()], push(), {"i-docs": AUTO})
    assert [(d.install_id, d.action) for d in decisions] == [("i-docs", SYNC)]


def test_unrelated_repo_or_ref_is_ignored():
    other_repo = install(git_repo_url="https://github.com/acme/other")
    other_ref = install(id="i-2", git_ref="release")
    not_connected = install(id="i-3", git_connected=False)
    assert plan_push([other_repo, other_ref, not_connected], push(), {"*": AUTO}) == []


def test_notify_policy_skips_with_reason():
    [decision] = plan_push([install()], push(), {"*": NOTIFY})
    assert decision.action == SKIP and "notify" in decision.reason


def test_default_branch_install_without_ref():
    decisions = plan_push([install(git_ref=None)], push(ref="refs/heads/main"), {"*": AUTO})
    assert decisions[0].action == SYNC
    assert plan_push([install(git_ref=None)], push(ref="refs/tags/main"), {"*": AUTO}) == []


def test_tag_pinned_install_matches_tag_push_only():
    pinned = install(git_ref="refs/tags/v0.1.4")
    assert plan_push([pinned], push(ref="refs/tags/v0.1.4"), {"*": AUTO})[0].action == SYNC
    assert plan_push([pinned], push(ref="refs/heads/v0.1.4"), {"*": AUTO})[0].action == SYNC  # same name
    assert plan_push([pinned], push(ref="refs/tags/v0.1.5"), {"*": AUTO}) == []


def test_deleted_ref_never_syncs():
    [decision] = plan_push([install()], push(deleted=True, commits=[]), {"*": AUTO})
    assert decision.action == SKIP and "deleted" in decision.reason


def test_inactive_install_is_skipped():
    [decision] = plan_push([install(status="error")], push(), {"*": AUTO})
    assert decision.action == SKIP


def test_monorepo_subpath_filter_only_when_changes_are_known():
    mono = install(repo_subpath="solutions/docs/")
    touched = push(commits=[{"added": [], "removed": [], "modified": ["solutions/docs/functions/a.py"]}])
    untouched = push(commits=[{"added": ["solutions/other/x.py"], "removed": [], "modified": []}])
    prefix_lookalike = push(commits=[{"added": ["solutions/docs-old/x.py"], "removed": [], "modified": []}])
    assert plan_push([mono], touched, {"*": AUTO})[0].action == SYNC
    assert plan_push([mono], untouched, {"*": AUTO})[0].action == SKIP
    assert plan_push([mono], prefix_lookalike, {"*": AUTO})[0].action == SKIP
    # Unknown change sets (force push, branch creation, missing lists) always sync.
    for unknown in [push(forced=True, commits=untouched_commits()), push(created=True),
                    push(commits=[]), push(commits=[{"modified": ["x"]}])]:
        assert plan_push([mono], unknown, {"*": AUTO})[0].action == SYNC


def untouched_commits():
    return [{"added": ["solutions/other/x.py"], "removed": [], "modified": []}]


def test_policy_resolution_prefers_id_and_refuses_ambiguous_slug():
    a, b = install(id="a", slug="scim-lite"), install(id="b", slug="scim-lite")
    assert policy_mode({"scim-lite": AUTO}, a, [a, b]) == NOTIFY
    assert policy_mode({"scim-lite": AUTO, "b": AUTO}, b, [a, b]) == AUTO
    assert policy_mode({"scim-lite": AUTO}, a, [a]) == AUTO
    assert policy_mode({}, a, [a]) == NOTIFY


def test_reconcile_syncs_only_behind_auto_installs():
    behind_auto = install(id="x", update_available_version="0.1.3+mtg.1")
    behind_notify = install(id="y", slug="other", update_available_version="1.1")
    current = install(id="z", slug="third")
    decisions = plan_reconcile([behind_auto, behind_notify, current], {"x": AUTO})
    assert [(d.install_id, d.action) for d in decisions] == [("x", SYNC), ("y", SKIP)]


def test_validate_policy_and_shipped_policy():
    validate_policy(POLICY)
    with pytest.raises(ValueError):
        validate_policy({"x": "always"})
    with pytest.raises(ValueError):
        validate_policy({"": AUTO})


def test_status_rows_sort_connected_first():
    rows = status_rows([install(id="b", slug="zz", git_connected=False), install(id="a")], {"a": AUTO})
    assert [r["install_id"] for r in rows] == ["a", "b"]
    assert rows[0]["policy"] == AUTO and rows[0]["repository"] == "github.com/midtown-technology-group/docs"
