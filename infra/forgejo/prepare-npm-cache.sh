#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
frontend="$repo_root/apps/desktop-web"
lockfile="$frontend/package-lock.json"
package_json="$frontend/package.json"
if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: infra/forgejo/prepare-npm-cache.sh OUTPUT_DIR [BOOTSTRAP_PROXY_URL]" >&2
  exit 2
fi
out=$1
bootstrap_proxy=${2:-}
case "$out" in
  /*) ;;
  *) out="$PWD/$out" ;;
esac
if [[ -e "$out" ]]; then
  echo "output path already exists: $out" >&2
  exit 2
fi
if [[ -n "$bootstrap_proxy" ]]; then
  node - "$bootstrap_proxy" <<'NODE'
const u = new URL(process.argv[2]);
if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password ||
    u.search || u.hash) {
  throw new Error('bootstrap proxy must be an http(s) URL without credentials, query, or fragment');
}
NODE
fi

# Validate the committed lockfile before allowing npm to fetch anything.
node - "$lockfile" <<'NODE'
const fs = require('node:fs');
const lock = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
if (lock.lockfileVersion !== 3) throw new Error('expected package-lock v3');
let count = 0;
for (const [name, entry] of Object.entries(lock.packages ?? {})) {
  if (!name || !entry.resolved) continue;
  const url = new URL(entry.resolved);
  if (url.protocol !== 'https:' || url.hostname !== 'registry.npmjs.org' ||
      url.username || url.password || url.search || url.hash) {
    throw new Error(`non-public or non-canonical tarball URL in ${name}`);
  }
  if (!/^sha(512|384|256)-[A-Za-z0-9+/]+={0,2}( sha(512|384|256)-[A-Za-z0-9+/]+={0,2})*$/.test(entry.integrity ?? '')) {
    throw new Error(`missing or malformed SRI integrity in ${name}`);
  }
  count++;
}
if (!count) throw new Error('lockfile has no resolved package entries');
console.log(`validated ${count} public registry tarballs with SRI`);
NODE

tmp=$(mktemp -d "${TMPDIR:-/tmp}/xeonolab-npm-cache.XXXXXX")
cleanup() { python3 -c 'import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)' "$tmp"; }
trap cleanup EXIT
mkdir -p "$tmp/project" "$tmp/cache"
cp "$package_json" "$lockfile" "$tmp/project/"
touch "$tmp/user.npmrc" "$tmp/global.npmrc"
npm_bin=$(command -v npm)
node_bin=$(dirname "$(command -v node)")

# Use a clean environment and an empty dedicated cache. No workstation npm config,
# auth environment, default cache, audit endpoints, or private registry. An optional
# unauthenticated bootstrap proxy is used only for this preparation invocation.
npm_args=(ci --ignore-scripts --no-audit --no-fund
  --registry=https://registry.npmjs.org
  --userconfig="$tmp/user.npmrc" --globalconfig="$tmp/global.npmrc"
  --cache="$tmp/cache" --fetch-retries=2 --fetch-timeout=60000 --loglevel=error
  --prefix "$tmp/project")
if [[ -n "$bootstrap_proxy" ]]; then
  npm_args+=("--proxy=$bootstrap_proxy" "--https-proxy=$bootstrap_proxy")
fi
env -i PATH="$node_bin:/usr/bin:/bin" "$npm_bin" "${npm_args[@]}"

# A successful online install is not sufficient: require a fresh offline install
# from the generated cache before emitting the build context.
env -i PATH="$node_bin:/usr/bin:/bin" \
  "$npm_bin" ci --offline --no-audit --no-fund \
    --userconfig="$tmp/user.npmrc" --globalconfig="$tmp/global.npmrc" \
    --cache="$tmp/cache" --loglevel=error --prefix "$tmp/project"

# Verify the exact exported payload, containing only npm's content-addressed
# store, before emitting the final context. npm itself may recreate _logs here;
# only _cacache is copied out below.
mkdir -p "$tmp/export-cache"
cp -a "$tmp/cache/_cacache" "$tmp/export-cache/"
env -i PATH="$node_bin:/usr/bin:/bin" \
  "$npm_bin" ci --offline --no-audit --no-fund \
    --userconfig="$tmp/user.npmrc" --globalconfig="$tmp/global.npmrc" \
    --cache="$tmp/export-cache" --loglevel=error --prefix "$tmp/project"

mkdir -p "$out"
mkdir -p "$out/npm-cache"
cp -a "$tmp/export-cache/_cacache" "$out/npm-cache/"
lock_sha=$(sha256sum "$lockfile" | awk '{print $1}')
node_version=$(node --version)
npm_version=$(npm --version)
package_count=$(node -e 'const p=require(process.argv[1]);console.log(Object.values(p.packages).filter(x=>x.resolved).length)' "$lockfile")
platform=$(node -p '`${process.platform}-${process.arch}`')
proxy_used=false
if [[ -n "$bootstrap_proxy" ]]; then proxy_used=true; fi
cat > "$out/npm-cache-manifest.json" <<EOF
{
  "source": "apps/desktop-web/package-lock.json",
  "lock_sha256": "$lock_sha",
  "resolved_package_count": $package_count,
  "registry": "https://registry.npmjs.org",
  "node_version": "$node_version",
  "npm_version": "$npm_version",
  "preparation_platform": "$platform",
  "bootstrap_proxy_used": $proxy_used,
  "online_install_scripts_executed": false,
  "offline_install_scripts_executed": true,
  "exported_cache_payload": "_cacache only",
  "offline_npm_ci_verified": true,
  "no_logs_npmrc_or_environment_exported": true
}
EOF
echo "Created public lockfile cache context: $out"
