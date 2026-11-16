"""Validate .bifrost manifests with the instance SDK's own parser (the deploy-time check)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_manifests_parse_and_cross_reference_with_instance_sdk():
    manifest_mod = pytest.importorskip("bifrost.manifest")
    from bifrost.definition_validation import read_workspace_manifest

    files = {p.name: p.read_text() for p in sorted((ROOT / ".bifrost").glob("*.yaml"))}
    for name, content in files.items():
        read_workspace_manifest(name, content)  # raises on schema errors, e.g. unknown fields
    manifest = manifest_mod.parse_manifest_dir(files)
    assert manifest_mod.validate_manifest(manifest) == []
