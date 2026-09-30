#!/usr/bin/python3
import os
import subprocess
raise SystemExit(subprocess.run(['sudo','-n','/usr/local/libexec/xeonolab-activate'],
    input=os.environ.get('SSH_ORIGINAL_COMMAND','').encode()).returncode)
