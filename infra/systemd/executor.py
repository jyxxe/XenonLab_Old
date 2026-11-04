#!/usr/bin/python3
"""Trusted fixed inventory executor. User data never becomes a shell command."""
import json
import re
import subprocess
import sys
from pathlib import Path

# Reject unsupported protocol extensions before reading inventory or attempting
# activation. Service plans need a reviewed new protocol, never ignored hints.
raw = sys.stdin.buffer.read(65537)
try:
    job = json.loads(raw)
except (ValueError, UnicodeError):
    raise SystemExit(2) from None
if len(raw) > 65536 or not isinstance(job, dict) or set(job) != {'manifest', 'deployment_id', 'epoch'}:
    raise SystemExit(2)
manifest = job['manifest']
if not isinstance(manifest, dict) or set(manifest) != {'commit', 'artifacts', 'migration_manifest', 'test_report'}:
    raise SystemExit(2)
if type(job['epoch']) is not int or job['epoch'] < 1:
    raise SystemExit(2)
if not isinstance(manifest['commit'], str) or not re.fullmatch('[a-f0-9]{40}', manifest['commit']):
    raise SystemExit(2)
if not isinstance(manifest['migration_manifest'], str) or not isinstance(manifest['test_report'], str) or not manifest['test_report']:
    raise SystemExit(2)
artifacts = manifest['artifacts']
if not isinstance(artifacts, list) or not artifacts:
    raise SystemExit(2)
for artifact in artifacts:
    if not isinstance(artifact, dict) or set(artifact) != {'name', 'digest'}:
        raise SystemExit(2)
    if not isinstance(artifact['name'], str) or not re.fullmatch('[a-z][a-z0-9-]{0,63}', artifact['name']):
        raise SystemExit(2)
    if not isinstance(artifact['digest'], str) or not re.fullmatch('sha256:[a-f0-9]{64}', artifact['digest']):
        raise SystemExit(2)
if not isinstance(job['deployment_id'], str) or not re.fullmatch('[a-f0-9-]{36}', job['deployment_id']):
    raise SystemExit(2)
config = json.loads(Path('/etc/xeonolab/executor.json').read_text())
if manifest['commit'] != config['commit'] or manifest['artifacts'] != config['artifacts']:
    raise SystemExit(2)
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
        print(json.dumps({'state':'rolled_back' if ok else 'failed'}))
        raise SystemExit(0 if ok else 1)
    completed.append(host)
print(json.dumps({'state':'completed'}))
