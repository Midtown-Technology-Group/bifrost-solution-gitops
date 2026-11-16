"""The workflow module must import against the real Bifrost SDK (CI installs the instance's CLI sdist)."""

import pytest


def test_workflow_module_imports():
    pytest.importorskip("bifrost")
    import functions.gitops as gitops

    for name in ("gitops_github_push", "gitops_reconcile", "gitops_status"):
        assert callable(getattr(gitops, name))
