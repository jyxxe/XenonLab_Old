#!/usr/bin/python3
"""Root-owned forced-command activation receiver. No arbitrary command input."""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

parts = sys.stdin.read(256).strip().split()
if not parts or parts[0] not in ('activate','rollback'):
    raise SystemExit(2)
config = json.loads(Path('/etc/xeonolab/activation.json').read_text())
root = Path('/opt/xeonolab')
current = root/'current'
state_dir = root/'activations'; state_dir.mkdir(exist_ok=True)
if parts[0] == 'activate':
    if len(parts)!=3 or not re.fullmatch('[a-f0-9]{40}',parts[1]) or not re.fullmatch('[a-f0-9-]{36}',parts[2]): raise SystemExit(2)
    commit,did = parts[1:]
    target = root/'releases'/commit
    if not (target/'.venv/bin/uvicorn').exists(): raise SystemExit(3)
    old = str(current.resolve()) if current.is_symlink() else None
    (state_dir/f'{did}.json').write_text(json.dumps({'old':old,'new':str(target)}))
else:
    if len(parts)!=2 or not re.fullmatch('[a-f0-9-]{36}',parts[1]): raise SystemExit(2)
    saved = json.loads((state_dir/f'{parts[1]}.json').read_text())
    target = Path(saved['old']) if saved['old'] else None

def restart(destination):
    if destination is None:
        for service in config['services']: subprocess.run(['systemctl','stop','xeonolab@'+service],check=True)
        current.unlink(missing_ok=True)
        return
    if destination.parent != root/'releases': raise SystemExit(3)
    temporary = root/'next'
    temporary.unlink(missing_ok=True); temporary.symlink_to(destination); os.replace(temporary,current)
    for service in config['services']:
        subprocess.run(['systemctl','enable','xeonolab@'+service],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(['systemctl','restart','xeonolab@'+service],check=True)
    for port in config['ports']:
        for attempt in range(120):
            try:
                with urllib.request.urlopen(f'http://{config["address"]}:{port}/health/ready',timeout=3) as result:
                    if result.status==200: break
            except Exception: pass
            time.sleep(.5)
        else: raise RuntimeError('health check failed')
    if config.get('reload_caddy'):
        subprocess.run(['systemctl','reload','caddy'],check=True)

try:
    restart(target)
except Exception:
    if parts[0]=='activate':
        restart(Path(old) if old else None)
    raise SystemExit(1)
print('activation confirmed')
