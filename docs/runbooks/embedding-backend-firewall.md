# Embedding desktop proxy access

The desktop's `/api/v1/embed/*` routes on VM101 proxy to VM107 port 8104.
The original VM107 firewall allowed VM104, VM106 and VM107 only, so the new
desktop route returned 502 even though the model's local readiness was healthy.
`infra/network/embedding-backend-firewall.json` adds VM101 to that single
backend port's source allowlist. It retains the existing callers and the final
reject rule. The Embedding service still verifies signed identity and scopes.

This JSON is an operator configuration template. Normal protected application
activation does not copy it to `/etc/xeonolab/backend-firewall.json` or update
the live nftables rules. Complete the native configuration step before counting
the desktop Embedding route as accepted.

Before applying it, obtain the exclusive deployment lease for the current
approved release, record the existing configuration's hash and file metadata,
and back up that file plus the complete existing `inet xeonolab` table in a
root-only directory. Confirm that the current table protects only port 8104,
with loopback accepted and the three expected original sources allowed.
Reject unexpected configuration or concurrent changes.

Validate the complete candidate table with `nft --check --file -`. Replace the
existing table using one `nft --file -` transaction containing its deletion and
the candidate table declaration. Do not run separate delete and add commands:
that would temporarily remove the backend boundary. Persist the JSON with an
atomic replacement preserving its ownership and permissions. On failure,
restore both the owned configuration and table using the same guarded process.

Verify VM101 can reach VM107's readiness and the desktop's authenticated
Embedding models/text endpoints, and that a caller outside the four approved
sources cannot connect directly to port 8104. Finally verify normal signed
users still require `embedding:read`/`embedding:write`, and legacy global queue
management remains restricted. Release the lease and retain the proof with
the exact configuration and rules hashes. Application readiness alone does
not prove proxy reachability or authenticated model inference.
