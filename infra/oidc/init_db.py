import os,subprocess
from pathlib import Path
values=dict(line.split('=',1) for line in Path('/etc/xeonolab/oidc/postgres.env').read_text().splitlines())
password=values['POSTGRES_PASSWORD']
query="SELECT 1 FROM pg_roles WHERE rolname='keycloak'"
result=subprocess.run(['sudo','-u','postgres','psql','-tAc',query],capture_output=True,text=True,check=True)
if not result.stdout.strip():
 subprocess.run(['sudo','-u','postgres','psql','-v','ON_ERROR_STOP=1'],input=f"CREATE ROLE keycloak LOGIN PASSWORD '{password}';\nCREATE DATABASE keycloak OWNER keycloak;\n",text=True,check=True)
