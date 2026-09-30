#!/bin/sh
set -eu
# Native PostgreSQL is loopback-only. The container uses host networking to reach it.
docker run -d --name xeonolab-keycloak --restart unless-stopped --network host \
  --env-file /etc/xeonolab/oidc/keycloak.env \
  -e KC_HTTP_PORT=8180 -e KC_HTTP_HOST=192.168.0.109 \
  -v /etc/xeonolab/oidc/xeonolab-realm.json:/opt/keycloak/data/import/xeonolab-realm.json:ro \
  --memory 2g \
  quay.io/keycloak/keycloak@sha256:9d1f1b2b7261ff53c66cb1092dfcdc34a5fb77e81f9e6a6e75b8b6a795de8067 \
  start --import-realm
