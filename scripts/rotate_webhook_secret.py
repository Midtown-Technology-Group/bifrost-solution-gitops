#!/usr/bin/env python3
"""Rotate the GitOps webhook secret and point every listed repository hook at it.

GitHub repository webhooks only need repository admin, so this needs no
``admin:org_hook`` token scope. ``scripts/hooked_repos.txt`` lists the
repositories (one ``owner/name`` per line) and is the source of truth. To
onboard a repository, add it there and run this script. Bifrost stores the
secret write-only and nothing else keeps it, so every hook gets the new value in
the same run. The script recreates a hook if it's missing.

    python scripts/rotate_webhook_secret.py --source-id <webhook-source-uuid>

The secret is generated here and goes only to Bifrost (``rotate-secret``) and
GitHub (hook config) over stdin or a 0600 file that is removed straight
afterwards. It's never printed, logged, or put on a command line.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_INSTANCE = "https://bifrost.midtowntg.com"
REPO_LIST = Path(__file__).with_name("hooked_repos.txt")


def read_repos(path: Path) -> list[str]:
    repos = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            if line.count("/") != 1:
                raise SystemExit(f"{path}: expected owner/name, got {line!r}")
            repos.append(line)
    return repos


def hook_body(callback: str, secret: str) -> dict[str, Any]:
    return {"active": True, "events": ["push"],
            "config": {"url": callback, "content_type": "json", "secret": secret, "insecure_ssl": "0"}}


def find_hook(hooks: list[dict[str, Any]], callback: str) -> dict[str, Any] | None:
    return next((h for h in hooks if h.get("config", {}).get("url") == callback), None)


def gh(args: list[str], stdin: str | None = None) -> Any:
    result = subprocess.run(["gh", "api", *args], input=stdin, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # gh error text never contains the request body.
        raise SystemExit(f"gh api {args[0]} {args[-1] if len(args) > 1 else ''}: {result.stderr.strip()[:300]}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def rotate_bifrost(source_id: str, secret: str) -> None:
    folder = tempfile.mkdtemp(prefix="gitops-rotate-")
    body = os.path.join(folder, "body.json")
    try:
        with open(os.open(body, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as fh:
            json.dump({"secret": secret}, fh)
        result = subprocess.run(["bifrost", "api", "POST", f"/api/events/sources/{source_id}/rotate-secret", "@" + body],
                                capture_output=True, text=True, check=False)
    finally:
        if os.path.exists(body):
            os.remove(body)
        os.rmdir(folder)
    try:
        data = json.loads(result.stdout or "null")
    except json.JSONDecodeError:
        data = None
    if result.returncode != 0 or not isinstance(data, dict) or "detail" in data:
        detail = data.get("detail") if isinstance(data, dict) else "unexpected response"
        raise SystemExit(f"Bifrost rotate-secret failed: {detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-id", required=True, help="Bifrost webhook event source UUID")
    parser.add_argument("--instance", default=DEFAULT_INSTANCE)
    parser.add_argument("--repos-file", type=Path, default=REPO_LIST)
    parser.add_argument("--dry-run", action="store_true", help="show the planned hook changes; rotate nothing")
    args = parser.parse_args(argv)

    callback = f"{args.instance.rstrip('/')}/api/hooks/{args.source_id}"
    repos = read_repos(args.repos_file)
    plan = []
    for repo in repos:  # Fail on access problems before touching the secret.
        hook = find_hook(gh([f"repos/{repo}/hooks"]), callback)
        plan.append((repo, hook))
        print(f"{repo}: {'update hook ' + str(hook['id']) if hook else 'create hook'}")
    if args.dry_run:
        return 0

    secret = secrets.token_hex(32)
    rotate_bifrost(args.source_id, secret)
    print("Bifrost webhook secret rotated.")
    for repo, hook in plan:
        payload = json.dumps(hook_body(callback, secret))
        if hook:
            gh(["-X", "PATCH", f"repos/{repo}/hooks/{hook['id']}", "--input", "-"], stdin=payload)
        else:
            hook = gh(["-X", "POST", f"repos/{repo}/hooks", "--input", "-"], stdin=payload)
        gh(["-X", "POST", f"repos/{repo}/hooks/{hook['id']}/pings"])
        print(f"{repo}: hook {hook['id']} set; ping sent")
    del secret
    print("Check deliveries: gh api repos/<repo>/hooks/<id>/deliveries --jq '.[0]|{event,status_code}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
