"""Operator-only setup/check of the maintainer's production integration gate."""

import argparse
import json
import ssl
from pathlib import Path
from urllib.parse import quote

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    secrets = root / 'runtime/deploy-secrets'
    users = json.loads((secrets / 'forgejo-users.json').read_text())
    rule = json.loads((root / 'infra/devagent/branch-protection.json').read_text())
    endpoint = '/repos/xenon-admin/XeonoLab/branch_protections'
    name = quote(rule['rule_name'], safe='')
    with httpx.Client(
        base_url='https://192.168.0.101:9445/api/v1',
        verify=ssl.create_default_context(cafile=secrets / 'lan-root-ca.crt'),
        trust_env=False, timeout=30, auth=('xenon-admin', users['xenon-admin']),
    ) as client:
        existing = client.get(endpoint + '/' + name)
        if args.apply:
            if existing.status_code == 404:
                response = client.post(endpoint, json=rule)
            else:
                existing.raise_for_status()
                response = client.patch(endpoint + '/' + name, json={k: v for k, v in rule.items() if k != 'rule_name'})
            response.raise_for_status()
            existing = client.get(endpoint + '/' + name)
        existing.raise_for_status()
        actual = existing.json()
        mismatches = []
        for key, expected in rule.items():
            value = actual.get(key)
            if isinstance(expected, list):
                if sorted(value or []) != sorted(expected):
                    mismatches.append(key)
            elif value != expected:
                mismatches.append(key)
        if mismatches:
            raise SystemExit('Branch protection differs: ' + ', '.join(mismatches))
    print('PASS: production branch blocks direct push; approved exact-head CI/preflight and maintainer merge whitelist configured')


if __name__ == '__main__':
    main()
