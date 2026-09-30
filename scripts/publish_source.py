"""Push tracked source to Forgejo; credentials stay in private files and askpass."""

import os
import sys
import subprocess
from pathlib import Path

root = Path("runtime/deploy-secrets").resolve()
askpass = root / "forgejo-askpass.py"
askpass.write_text("""#!/usr/bin/python3
import json,os,sys
from pathlib import Path
if 'username' in sys.argv[1].lower():
    print('xenon-admin')
else:
    print(json.loads(Path(os.environ['XEONOLAB_FORGEJO_CREDENTIALS']).read_text())['xenon-admin'])
""")
askpass.chmod(0o700)
environment = {
    **os.environ,
    "GIT_ASKPASS": str(askpass),
    "GIT_TERMINAL_PROMPT": "0",
    "XEONOLAB_FORGEJO_CREDENTIALS": str(root / "forgejo-users.json"),
}
url = "https://xenon.jyxxe.com/dev/xenon-admin/XeonoLab.git"
if "--local" in sys.argv:
    url = "https://192.168.0.101:9445/xenon-admin/XeonoLab.git"
    environment["GIT_SSL_CAINFO"] = str(root / "lan-root-ca.crt")
try:
    subprocess.run(["git", "push", url, "--all"], env=environment, check=True)
finally:
    askpass.unlink(missing_ok=True)
