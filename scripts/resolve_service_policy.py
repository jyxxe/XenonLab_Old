"""Resolve explicit local declaration/manifest against an existing core policy DB."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validate_service_plugin import read_bounded  # noqa: E402

from xeonolab.service_execution_policy import (  # noqa: E402
    ExecutionPolicyError,
    ExecutionPolicyStore,
    resolve_execution_policy,
)
from xeonolab.service_plugin_contract import MAX_DECLARATION_BYTES, MAX_MANIFEST_BYTES  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("declaration", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--policy-db", type=Path, required=True)
    parser.add_argument("--minimum-revision", type=int, default=1)
    args = parser.parse_args()
    try:
        result = resolve_execution_policy(
            read_bounded(args.declaration, MAX_DECLARATION_BYTES),
            read_bounded(args.manifest, MAX_MANIFEST_BYTES),
            ExecutionPolicyStore(args.policy_db),
            minimum_revision=args.minimum_revision,
        )
    except (OSError, ExecutionPolicyError):
        print("Service execution policy could not be resolved.", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "policy_resolved": True,
                "admission_granted": False,
                "declaration_digest": result.declaration_digest,
                "capability_manifest_digest": result.capability_manifest_digest,
                "policy_digest": result.policy_digest,
                "policy_revision": result.policy_revision,
                "checked_at": result.checked_at,
                "valid_until": result.valid_until,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
