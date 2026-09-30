#!/usr/bin/python3
"""Filter only XeonoLab backend ports; leave existing SSH and unrelated services intact."""
import json
import subprocess
import sys
from pathlib import Path

config=json.loads(Path('/etc/xeonolab/backend-firewall.json').read_text())
subprocess.run(['nft','delete','table','inet','xeonolab'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
ports=', '.join(map(str,config['ports']))
allowed=', '.join(config['allowed'])
rules=f'''table inet xeonolab {{
 chain input {{
  type filter hook input priority -5; policy accept;
  iifname "lo" accept
  ip saddr {{ {allowed} }} tcp dport {{ {ports} }} accept
  tcp dport {{ {ports} }} reject
 }}
}}
'''
subprocess.run(['nft','-f','-'],input=rules,text=True,check=True)
