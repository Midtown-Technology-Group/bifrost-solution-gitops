"""Pure helpers of the operator scripts (no network)."""

import html
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


boot = load("bootstrap_solution_source")
rotate = load("rotate_webhook_secret")


def test_manifest_form_round_trips_and_escapes():
    manifest = {"name": 'x"><script>alert(1)</script>', "default_permissions": {"contents": "read"}}
    page = boot.manifest_form_html("https://github.com/organizations/O/settings/apps/new?state=a&b=c", manifest)
    assert "<script>alert(1)" not in page
    assert 'action="https://github.com/organizations/O/settings/apps/new?state=a&amp;b=c"' in page
    value = page.split('name=manifest value="', 1)[1].split('"', 1)[0]
    assert json.loads(html.unescape(value)) == manifest


def test_scope_body_global_is_explicit_null():
    assert boot.scope_body("GLOBAL") == {"organization_id": None}
    assert boot.scope_body("abc") == {"organization_id": "abc"}


def test_app_names_unique_and_within_github_limit():
    org = "00000000-0000-0000-0000-000000000002"
    assert boot.app_name("MTG Bifrost Solutions", org, org) == "MTG Bifrost Solutions"
    assert boot.app_name("MTG Bifrost Solutions", "GLOBAL", org) == "MTG Bifrost Solutions (Global)"
    assert boot.app_name("MTG Bifrost Solutions", "1a16daa0-4cc3", org) == "MTG Bifrost Solutions (1a16daa0)"
    with pytest.raises(SystemExit):
        boot.app_name("A" * 30, "GLOBAL", None)


def test_read_repos_ignores_comments_and_rejects_bad_lines(tmp_path):
    f = tmp_path / "r.txt"
    f.write_text("# c\nA/b  # trailing\n\nC/d\n")
    assert rotate.read_repos(f) == ["A/b", "C/d"]
    f.write_text("just-a-name\n")
    with pytest.raises(SystemExit):
        rotate.read_repos(f)


def test_committed_repo_list_is_valid():
    assert rotate.read_repos(rotate.REPO_LIST)


def test_hook_body_and_lookup():
    cb = "https://i/api/hooks/s"
    body = rotate.hook_body(cb, "s3cr3t")
    assert body["events"] == ["push"] and body["config"]["content_type"] == "json"
    assert body["config"]["insecure_ssl"] == "0"
    hooks = [{"id": 1, "config": {"url": "https://other"}}, {"id": 2, "config": {"url": cb}}]
    assert rotate.find_hook(hooks, cb)["id"] == 2
    assert rotate.find_hook(hooks[:1], cb) is None
