#!/usr/bin/python3 -I
"""Install the fixed LAN-only Web listener filter as one atomic nft transaction."""
import subprocess
from pathlib import Path

present = subprocess.run(
    ["/usr/sbin/nft", "list", "table", "inet", "devagent_web"], capture_output=True
).returncode == 0
rules = ("delete table inet devagent_web\n" if present else "") + Path("/etc/devagent/web-firewall.nft").read_text()
subprocess.run(["/usr/sbin/nft", "-f", "-"], input=rules, text=True, check=True)
