"""Push one explicitly selected local branch to Forgejo.

Credentials stay in private files and are only accessed through Git askpass.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def selected_branch(requested):
    current = subprocess.run(
        ["git", "branch", "--show-current"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if not current:
        raise ValueError("cannot publish from detached HEAD; check out a local branch first")

    branch = current if requested is None else requested
    valid = subprocess.run(
        ["git", "check-ref-format", "--branch", branch], capture_output=True, text=True
    )
    if valid.returncode != 0 or valid.stdout.strip() != branch:
        raise ValueError(f"invalid branch name: {branch!r}")
    exists = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]
    )
    if exists.returncode != 0:
        raise ValueError(f"local branch does not exist: {branch!r}")
    return branch


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", help="local branch to publish (defaults to the current branch)")
    parser.add_argument("--local", action="store_true", help="publish to the LAN Forgejo endpoint")
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    try:
        branch = selected_branch(args.branch)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"publish_source: {error}", file=sys.stderr)
        return 2

    root = Path("runtime/deploy-secrets").resolve()
    askpass = root / "forgejo-askpass.py"
    environment = {
        **os.environ,
        "GIT_ASKPASS": str(askpass),
        "GIT_TERMINAL_PROMPT": "0",
        "XEONOLAB_FORGEJO_CREDENTIALS": str(root / "forgejo-users.json"),
    }
    url = "https://xenon.jyxxe.com/dev/xenon-admin/XeonoLab.git"
    if args.local:
        url = "https://192.168.0.101:9445/xenon-admin/XeonoLab.git"
        environment["GIT_SSL_CAINFO"] = str(root / "lan-root-ca.crt")

    try:
        askpass.write_text(
            """#!/usr/bin/python3
import json,os,sys
from pathlib import Path
if 'username' in sys.argv[1].lower():
    print('xenon-admin')
else:
    print(json.loads(Path(os.environ['XEONOLAB_FORGEJO_CREDENTIALS']).read_text())['xenon-admin'])
"""
        )
        askpass.chmod(0o700)
        refspec = f"refs/heads/{branch}:refs/heads/{branch}"
        subprocess.run(["git", "push", url, refspec], env=environment, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"publish_source: failed to publish branch {branch!r}: {error}", file=sys.stderr)
        return 1
    finally:
        askpass.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
