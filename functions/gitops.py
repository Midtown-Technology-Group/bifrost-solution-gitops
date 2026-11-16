"""Solution GitOps workflows: sync Git-connected installs when their source changes."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from bifrost import UserError, api, tables, tool, workflow
from modules.gitops_core import (
    SYNC,
    Decision,
    PushEvent,
    parse_github_push,
    plan_push,
    plan_reconcile,
    status_rows,
)
from modules.policy import POLICY

logger = logging.getLogger(__name__)

EVENTS_TABLE = "gitops-events"
_MAX_DETAIL = 300


async def _list_installs() -> list[dict[str, Any]]:
    response = await api.get("/api/solutions")
    if response.status_code != 200:
        raise UserError(f"Listing Solution installs failed (HTTP {response.status_code})")
    body = response.json()
    items = body.get("solutions") if isinstance(body, dict) else body
    if not isinstance(items, list):
        raise UserError("Listing Solution installs returned an unexpected shape")
    return [item for item in items if isinstance(item, dict)]


def _detail(response: Any) -> str:
    try:
        body = response.json()
        text = body.get("detail") if isinstance(body, dict) else body
    except Exception:  # noqa: BLE001 - a non-JSON error body is still reportable
        text = response.text
    return str(text)[:_MAX_DETAIL]


async def _queue_sync(install_id: str) -> dict[str, Any]:
    """Queue the platform's own Git sync job for one install."""
    response = await api.post(f"/api/solutions/{install_id}/sync")
    if response.status_code in (200, 202):
        body = response.json()
        return {"outcome": "queued", "job_id": body.get("job_id"), "reused": bool(body.get("reused"))}
    return {"outcome": "failed", "http_status": response.status_code, "detail": _detail(response)}


async def _record(trigger: str, decision: Decision, result: dict[str, Any], push: PushEvent | None = None) -> None:
    """Append to the GitOps event log. Logging never blocks a deploy decision."""
    row = {
        "occurred_at": datetime.now(UTC).isoformat(),
        "trigger": trigger,
        "install_id": decision.install_id,
        "slug": decision.slug,
        "action": decision.action,
        "outcome": result.get("outcome"),
        "reason": decision.reason,
        "repository": push.repo_display if push else None,
        "ref": f"{push.ref_kind}:{push.ref_name}" if push else None,
        "commit": push.after if push else None,
        "job_id": result.get("job_id"),
        "detail": result.get("detail"),
    }
    try:
        await tables.insert(EVENTS_TABLE, row)
    except Exception:  # noqa: BLE001
        logger.warning("Could not record GitOps event for install %s", decision.install_id, exc_info=True)


async def _apply(trigger: str, decisions: list[Decision], push: PushEvent | None = None) -> list[dict[str, Any]]:
    results = []
    for decision in decisions:
        result = await _queue_sync(decision.install_id) if decision.action == SYNC else {"outcome": "skipped"}
        await _record(trigger, decision, result, push)
        results.append({"install_id": decision.install_id, "slug": decision.slug, "action": decision.action,
                        "reason": decision.reason, **result})
    return results


@workflow(
    name="gitops_github_push",
    description="Handle a GitHub push webhook: sync opted-in installs that follow the pushed repository and ref.",
    category="Solution GitOps",
)
async def gitops_github_push(payload: dict) -> dict[str, Any]:
    try:
        push = parse_github_push(payload)
    except ValueError as exc:
        raise UserError(f"Not a usable GitHub push event: {exc}") from exc
    decisions = plan_push(await _list_installs(), push, POLICY)
    results = await _apply("github_push", decisions, push)
    return {
        "repository": push.repo_display,
        "ref": f"{push.ref_kind}:{push.ref_name}",
        "commit": push.after,
        "matched_installs": len(decisions),
        "synced": sum(1 for r in results if r.get("outcome") == "queued"),
        "results": results,
    }


@workflow(
    name="gitops_reconcile",
    description="Backstop for missed webhooks: sync opted-in installs the platform reports as behind their Git ref.",
    category="Solution GitOps",
)
async def gitops_reconcile() -> dict[str, Any]:
    decisions = plan_reconcile(await _list_installs(), POLICY)
    results = await _apply("reconcile", decisions)
    return {
        "behind": len(decisions),
        "synced": sum(1 for r in results if r.get("outcome") == "queued"),
        "results": results,
    }


@tool(
    name="gitops_status",
    description="List Solution installs with their Git connection, versions, pending updates and GitOps policy.",
    category="Solution GitOps",
)
async def gitops_status() -> dict[str, Any]:
    rows = status_rows(await _list_installs(), POLICY)
    return {
        "installs": len(rows),
        "git_connected": sum(1 for row in rows if row["git_connected"]),
        "auto": sum(1 for row in rows if row["git_connected"] and row["policy"] == "auto"),
        "behind": sum(1 for row in rows if row["update_available_version"]),
        "rows": rows,
    }
