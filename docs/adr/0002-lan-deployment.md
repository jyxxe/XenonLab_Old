# ADR 0002 — Initial LAN deployment

Use existing VMs without deleting early validation services. New releases use immutable directories
under /opt/xeonolab/releases, independent systemd units and data roots. VM 101 hosts the desktop,
VM 104 the knowledge data on its mirror disk, VM 107 real CPU D100M inference, VM 106 common gateways,
and VM 109 Keycloak with native PostgreSQL. No Cloudflare Tunnel. Caddy terminates local-CA TLS.

Keycloak provides browser Authorization Code + PKCE, independent service-account tokens and
signed role-to-scope mappings. A narrowly scoped deployment-policy identity may approve the
explicitly user-authorized release. Production service tokens refresh server-side and are never
passed to the model. Public signing material is provisioned independently from the private issuer.

Initial management bootstrap installs runtimes, identity and Deploy Gateway. Application activation
then runs through release/approval/lease/deployment. The gateway holds its own restricted SSH key,
accepted only by a forced-command receiver that can activate or roll back pre-staged immutable
release directories. It cannot run arbitrary shell commands or change PVE hardware. The trusted
executor accepts only the commit and artifact digests listed in a root-managed inventory file.
A failed deployment remains frozen unless rollback was confirmed. Full deployment SSE, multi-release
artifact registry, advanced database migrations and zero-downtime updates remain future work.
