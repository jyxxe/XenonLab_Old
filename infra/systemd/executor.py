#!/usr/bin/python3
"""Trusted fixed inventory executor. User data never becomes a shell command."""
import json
import re
import subprocess
import sys
from pathlib import Path

job = json.loads(sys.stdin.read(65536))
config = json.loads(Path('/etc/xeonolab/executor.json').read_text())
manifest = job['manifest']
if manifest['commit'] != config['commit'] or manifest['artifacts'] != config['artifacts']:
    raise SystemExit(2)
if not re.fullmatch('[a-f0-9-]{36}',job['deployment_id']): raise SystemExit(2)
completed=[]

def execute(host, command):
    return subprocess.run(['ssh','-i','/etc/xeonolab/deploy-key','-o','BatchMode=yes',
        '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=/etc/xeonolab/deploy-known-hosts',
        '-o','ConnectTimeout=10','xeonolab-deploy@'+host,command],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=180).returncode

for host in config['targets']:
    if execute(host,'activate '+manifest['commit']+' '+job['deployment_id']):
        # Include failed target: its receiver may already have restored itself.
        ok=all(execute(target,'rollback '+job['deployment_id'])==0 for target in [host,*reversed(completed)])
        print(json.dumps({'state':'rolled_back' if ok else 'failed'}));raise SystemExit(0 if ok else 1)
    completed.append(host)
print(json.dumps({'state':'completed'}))
