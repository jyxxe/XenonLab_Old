#!/usr/bin/python3
"""Git HTTPS authentication only; never use credentials embedded in a remote URL."""

import sys
from pathlib import Path

if "Username" in sys.argv[1]:
    print("devagent-maintainer")
elif "Password" in sys.argv[1]:
    print(Path("/var/lib/devagent/.config/devagent/forgejo-token").read_text().strip())
else:
    raise SystemExit(1)
