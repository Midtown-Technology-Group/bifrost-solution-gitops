"""Manifest consistency: every reference resolves and powers stay least-privilege."""

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return yaml.safe_load((ROOT / ".bifrost" / name).read_text())


def test_descriptor():
    descriptor = yaml.safe_load((ROOT / "bifrost.solution.yaml").read_text())
    assert descriptor["slug"] == "solution-gitops"
    assert descriptor["allow_outbound_access"] is False
    from packaging.version import Version

    Version(descriptor["version"])


def test_workflows_point_at_real_functions_with_restricted_powers():
    workflows = load("workflows.yaml")["workflows"]
    for key, wf in workflows.items():
        assert key == wf["id"]
        source = (ROOT / wf["path"]).read_text()
        functions = {n.name for n in ast.walk(ast.parse(source)) if isinstance(n, ast.AsyncFunctionDef)}
        assert wf["function_name"] in functions
        assert wf["access_level"] == "role_based" and wf["role_names"] == []  # platform admins only
        perms = wf["requested_permissions"]
        assert perms["mode"] == "restricted"
        granted = {g["permission"] for g in perms["grants"]}
        assert granted <= {"solutions.read", "solutions.deploy", "tables.readwrite"}


def test_event_subscriptions_target_declared_workflows():
    workflow_ids = set(load("workflows.yaml")["workflows"])
    events = load("events.yaml")["events"]
    for key, source in events.items():
        assert key == source["id"]
        assert source["subscriptions"], key
        for sub in source["subscriptions"]:
            assert sub["workflow_id"] in workflow_ids
    webhook = next(s for s in events.values() if s["source_type"] == "webhook")
    config = webhook["webhook_config"]
    assert config["signature_header"] == "X-Hub-Signature-256"  # GitHub HMAC
    assert "secret" not in config  # write-only instance state, never in Git
    assert [s["event_type"] for s in webhook["subscriptions"]] == ["push"]


def test_event_log_table_is_admin_only():
    [table] = load("tables.yaml")["tables"].values()
    assert table["name"] == "gitops-events"
    assert [p["name"] for p in table["policies"]] == ["platform_admin_bypass"]
