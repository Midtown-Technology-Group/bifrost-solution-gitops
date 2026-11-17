#!/usr/bin/env python3
"""Create Bifrost private Solution Repository sources through the REST API.

Bifrost's side of the GitHub App setup is plain REST (``/api/github/app/*``).
GitHub only allows a person to confirm three steps, and this script opens each
one in the browser and waits for it:

1. creating the App from the manifest (one-time, org-owned, Contents: read);
2. authorizing the App for your GitHub user (later scopes reuse the App,
   and GitHub skips the prompt once you've authorized);
3. installing the App on the repository (or adding the repository to the
   existing installation).

Each confirmation redirects back to Bifrost's ``/settings/github`` page, which
finishes that step with your Bifrost browser session. Log in to Bifrost in the
browser as the same user the ``bifrost`` CLI uses.

Each scope gets its own App. Bifrost reuses an App only inside the scope its
source lives in (``/api/github/app/reuse`` loads the source by target scope),
so a Global source can't borrow the Provider org's App. A scope that already
has a source reuses its App and just gains the repository.

Sources bind to the repository's ``owner/name``. Run this after any repository
transfer, not before.

    python scripts/bootstrap_solution_source.py \
        --repo Midtown-Technology-Group/bifrost-workspace-next \
        --scope 00000000-0000-0000-0000-000000000002 --scope GLOBAL

No secret ever passes through this script. The App key and client secret go
from GitHub straight to Bifrost, and the OAuth codes pass through the
browser. It prints only non-secret IDs.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import tempfile
import time
import webbrowser
from typing import Any, Callable

GLOBAL = "GLOBAL"
POLL_SECONDS = 4


class BifrostError(RuntimeError):
    pass


def api(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """Call Bifrost through the authenticated ``bifrost api`` CLI."""
    args = ["bifrost", "api", method, path]
    if body is not None:
        args.append(json.dumps(body))
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    text = result.stdout.strip()
    try:
        data = json.loads(text) if text else None
    except json.JSONDecodeError:
        raise BifrostError(f"{method} {path}: unexpected response (exit {result.returncode})") from None
    if result.returncode != 0 or (isinstance(data, dict) and "detail" in data and len(data) == 1):
        detail = data.get("detail") if isinstance(data, dict) else text[:200]
        raise BifrostError(f"{method} {path}: {detail}")
    return data


def scope_body(scope: str) -> dict[str, Any]:
    """Request field for a scope. Global must be an explicit null."""
    return {"organization_id": None if scope == GLOBAL else scope}


def manifest_form_html(registration_url: str, manifest: dict[str, Any]) -> str:
    """A page that POSTs the App manifest to GitHub. GitHub accepts manifests only by form POST."""
    action = html.escape(registration_url, quote=True)
    value = html.escape(json.dumps(manifest), quote=True)
    return (
        "<!doctype html><meta charset=utf-8><title>Create GitHub App</title>"
        f'<form id=f method=post action="{action}">'
        f'<input type=hidden name=manifest value="{value}">'
        "<p>Opening GitHub to create the App&hellip;</p>"
        "<button type=submit>Continue to GitHub</button></form>"
        "<script>document.getElementById('f').submit()</script>"
    )


def open_for_user(label: str, url: str) -> None:
    print(f"\n>>> {label}\n    {url}", flush=True)
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - printing the link is the fallback
        pass


def wait_for(description: str, probe: Callable[[], Any], timeout: float) -> Any:
    print(f"    waiting: {description}", flush=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            value = probe()
        except BifrostError as exc:
            value = None
            last = str(exc)
        else:
            last = ""
        if value:
            return value
        if time.monotonic() > deadline:
            raise SystemExit(f"Timed out waiting for: {description}. {last}".strip())
        time.sleep(POLL_SECONDS)


def current_setup(setup_id: str) -> dict[str, Any] | None:
    setup = api("GET", "/api/github/app/setup?purpose=solution")
    return setup if setup and setup.get("setup_id") == setup_id else None


def sources_in(scope: str) -> list[dict[str, Any]]:
    return api("GET", f"/api/github/app/solution-sources?organization_id={scope}")["sources"]


def source_for(repo: str, scope: str) -> dict[str, Any] | None:
    for source in sources_in(scope):
        if source.get("enabled") and any(r["full_name"].casefold() == repo.casefold() for r in source["repositories"]):
            return source
    return None


def any_source(scope: str) -> dict[str, Any] | None:
    return next((s for s in sources_in(scope) if s.get("enabled")), None)


def app_name(base: str, scope: str, first_org: str | None) -> str:
    """GitHub App names are global and at most 34 characters."""
    if scope == GLOBAL:
        name = f"{base} (Global)"
    elif scope == first_org:
        name = base
    else:
        name = f"{base} ({scope[:8]})"
    if len(name) > 34:
        raise SystemExit(f"GitHub App name too long (max 34): {name!r}")
    return name


def start_setup(args: argparse.Namespace, scope: str, reuse_from: dict[str, Any] | None, name: str) -> str:
    if reuse_from:
        setup = api("POST", "/api/github/app/reuse", {"source_id": reuse_from["id"], **scope_body(scope)})
        print(f"    reusing App '{reuse_from['app_name']}' (source {reuse_from['id']})")
        return setup["setup_id"]
    setup = api("POST", "/api/github/app/manifest", {
        "purpose": "solution", "owner": args.owner, "owner_type": args.owner_type,
        "name": name, "public": False, **scope_body(scope),
    })
    setup_id = setup["setup_id"]
    folder = tempfile.mkdtemp(prefix="bifrost-app-")
    page = os.path.join(folder, "create-github-app.html")
    with open(os.open(page, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as fh:
        fh.write(manifest_form_html(setup["registration_url"], setup["manifest"]))
    open_for_user("Confirm 'Create GitHub App' on GitHub (one-time)", "file://" + page)
    try:
        wait_for("App registration returned to Bifrost",
                 lambda: (s := current_setup(setup_id)) and s["stage"] not in ("registration", "registration_needs_review"),
                 args.timeout)
    finally:
        os.remove(page)
        os.rmdir(folder)
    return setup_id


def authorize(args: argparse.Namespace, setup_id: str) -> None:
    url = api("POST", "/api/github/app/authorize", {"setup_id": setup_id})["authorization_url"]
    open_for_user("Authorize the App for your GitHub user", url)
    wait_for("GitHub authorization returned to Bifrost",
             lambda: (s := current_setup(setup_id)) and s["stage"] != "authorization", args.timeout)


def installation_id(args: argparse.Namespace, setup_id: str) -> int:
    def probe() -> int | None:
        setup = api("POST", "/api/github/app/installations", {"setup_id": setup_id})
        match = [i for i in setup["installations"] if i["account"].casefold() == args.owner.casefold()]
        return match[0]["id"] if match else None

    found = probe()
    if found:
        return found
    setup = current_setup(setup_id) or {}
    open_for_user(f"Install the App on {args.owner} with access to {args.repo} only",
                  setup.get("install_url") or "https://github.com/settings/installations")
    return wait_for(f"App installation on {args.owner}", probe, args.timeout)


def repository_id(args: argparse.Namespace, setup_id: str, inst: int) -> int:
    def probe() -> int | None:
        setup = api("POST", "/api/github/app/repositories", {"setup_id": setup_id, "installation_id": inst})
        match = [r for r in setup["repositories"] if r["full_name"].casefold() == args.repo.casefold()]
        return match[0]["id"] if match else None

    found = probe()
    if found:
        return found
    settings = ("https://github.com/settings/installations" if args.owner_type == "user"
                else f"https://github.com/organizations/{args.owner}/settings/installations")
    open_for_user(f"Add {args.repo} to the App installation ('Only select repositories')", f"{settings}/{inst}")
    return wait_for(f"{args.repo} visible to the installation", probe, args.timeout)


def bootstrap_scope(args: argparse.Namespace, scope: str, name: str) -> dict[str, Any]:
    print(f"\n== Scope {scope}")
    existing = source_for(args.repo, scope)
    if existing:
        print(f"    already authorized: source {existing['id']}")
        return existing
    setup_id = start_setup(args, scope, any_source(scope), name)
    authorize(args, setup_id)
    inst = installation_id(args, setup_id)
    repo = repository_id(args, setup_id, inst)
    setup = api("POST", "/api/github/app/select",
                {"setup_id": setup_id, "installation_id": inst, "repository_id": repo})
    if setup.get("stage") != "connected" or not setup.get("source_id"):
        raise SystemExit(f"Selection did not save a source (stage {setup.get('stage')}).")
    source = source_for(args.repo, scope)
    if not source:
        raise SystemExit("Bifrost reported a saved source, but it isn't listed in this scope.")
    print(f"    source {source['id']} (installation {source['installation_id']})")
    return source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="owner/name of the private Solution repository")
    parser.add_argument("--scope", action="append", required=True,
                        help="Bifrost organization UUID, or GLOBAL. Repeat for several; each gets its own App")
    parser.add_argument("--owner", help="GitHub organization that owns the App (default: the repository owner)")
    parser.add_argument("--owner-type", choices=("organization", "user"), default="organization",
                        help="use 'user' while the repository still lives in a personal account")
    parser.add_argument("--app-name", default="MTG Bifrost Solutions",
                        help="base App name; Global appends ' (Global)'")
    parser.add_argument("--timeout", type=float, default=900, help="seconds to wait for each browser step")
    args = parser.parse_args(argv)
    args.owner = args.owner or args.repo.split("/", 1)[0]

    first_org = next((s for s in args.scope if s != GLOBAL), None)
    names = {scope: app_name(args.app_name, scope, first_org) for scope in args.scope}
    results: dict[str, str] = {}
    for scope in args.scope:
        results[scope] = bootstrap_scope(args, scope, names[scope])["id"]
    print("\nSolution Repository sources (pass as git_source_id):")
    for scope, source_id in results.items():
        print(f"  {scope}: {source_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
