#!/usr/bin/env python3
"""Export and atomically stage only the OIDC origin/token maps in service env files."""

import argparse
import json
import os
import shlex
import stat
import tempfile
from pathlib import Path

MAP_KEYS = ("OIDC_ISSUERS_BY_ORIGIN_JSON", "OIDC_TOKEN_URLS_JSON")
NODE_SERVICES = {
    "101": ("desktop-web",),
    "104": ("xelibrary",),
    "106": ("tool-gateway", "llm-gateway"),
    "107": ("embedding",),
    "111": ("dev-agent",),
}
EXPECTED_ISSUERS = {
    "https://xenon.jyxxe.com": "https://xenon.jyxxe.com/auth/realms/xeonolab",
    "https://xenon.lan": "https://auth.xenon.lan/realms/xeonolab",
    "https://192.168.0.101:9443": "https://192.168.0.101:9444/realms/xeonolab",
}


def _parse_map_value(line, key):
    try:
        words = shlex.split(line, comments=False, posix=True)
    except ValueError as exc:
        raise ValueError(f"invalid {key} assignment") from exc
    if len(words) != 1 or not words[0].startswith(key + "="):
        raise ValueError(f"invalid {key} assignment")
    value = words[0][len(key) + 1 :]
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {key} JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"invalid {key} object")
    return value


def validate_payload(payload):
    if not isinstance(payload, dict) or set(payload) != set(MAP_KEYS):
        raise ValueError("OIDC map payload has an unexpected schema")
    issuer_raw = payload["OIDC_ISSUERS_BY_ORIGIN_JSON"]
    token_raw = payload["OIDC_TOKEN_URLS_JSON"]
    if not isinstance(issuer_raw, str) or not isinstance(token_raw, str):
        raise ValueError("OIDC map payload values must be strings")
    try:
        issuers = json.loads(issuer_raw)
        token_urls = json.loads(token_raw)
    except json.JSONDecodeError as exc:
        raise ValueError("OIDC map payload contains invalid JSON") from exc
    if issuers != EXPECTED_ISSUERS:
        raise ValueError("OIDC origin map does not match the exact expected issuers")
    expected_urls = {
        issuer: issuer + "/protocol/openid-connect/token" for issuer in EXPECTED_ISSUERS.values()
    }
    if token_urls != expected_urls:
        raise ValueError("OIDC token URL map does not match the issuer map")
    return payload


def export_payload(env_file):
    found = {key: [] for key in MAP_KEYS}
    for line in Path(env_file).read_text(encoding="utf-8").splitlines():
        for key in MAP_KEYS:
            if line.startswith(key + "="):
                found[key].append(_parse_map_value(line, key))
    if any(len(values) != 1 for values in found.values()):
        raise ValueError("source env file must contain exactly one of each OIDC map")
    return validate_payload({key: found[key][0] for key in MAP_KEYS})


def _atomic_write(path, content):
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("service env target must be a regular file")
    fd, temporary_name = tempfile.mkstemp(prefix=".oidc-maps-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def update_service_env(env_file, payload):
    payload = validate_payload(payload)
    path = Path(env_file)
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    replacements = {key: key + "=" + shlex.quote(payload[key]) + "\n" for key in MAP_KEYS}
    seen = set()
    updated = []
    for line in lines:
        key = next((candidate for candidate in MAP_KEYS if line.startswith(candidate + "=")), None)
        if key is None:
            updated.append(line)
        elif key not in seen:
            updated.append(replacements[key])
            seen.add(key)
    for key in MAP_KEYS:
        if key not in seen:
            if updated and not updated[-1].endswith("\n"):
                updated[-1] += "\n"
            updated.append(replacements[key])
    content = "".join(updated)
    if content != original:
        _atomic_write(path, content)


def stage_node(node, payload, config_dir="/etc/xeonolab"):
    if node not in NODE_SERVICES:
        raise ValueError("unsupported deployment node")
    payload = validate_payload(payload)
    paths = [Path(config_dir) / (service + ".env") for service in NODE_SERVICES[node]]
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("service env target must be a regular file")
    for path in paths:
        update_service_env(path, payload)
    return NODE_SERVICES[node]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-env", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--node", choices=NODE_SERVICES)
    parser.add_argument("--payload", type=Path)
    args = parser.parse_args()
    if args.export_env:
        if not args.output or args.node or args.payload:
            parser.error("--export-env requires --output and cannot be combined with --node")
        args.output.write_text(json.dumps(export_payload(args.export_env), separators=(",", ":")) + "\n")
        return
    if not args.node or not args.payload or args.export_env or args.output:
        parser.error("staging requires --node and --payload")
    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    services = stage_node(args.node, payload)
    print("Updated OIDC maps for " + ", ".join(services))


if __name__ == "__main__":
    main()
