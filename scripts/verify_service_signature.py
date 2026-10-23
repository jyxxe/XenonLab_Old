"""Verify local DSSE service-declaration signatures against an operator-selected core trust DB.

Signature evidence does not grant deployment, service admission or execution.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.validate_service_plugin import read_bounded  # noqa: E402
from xeonolab.service_plugin_contract import MAX_MANIFEST_BYTES  # noqa: E402
from xeonolab.service_plugin_signatures import MAX_ENVELOPE_BYTES, verify_service_signature  # noqa: E402
from xeonolab.service_plugin_trust import ServiceSignatureError, TrustPolicyStore  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("envelope", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--trust-db", type=Path, required=True)
    parser.add_argument("--minimum-revision", type=int, default=1)
    args = parser.parse_args()
    try:
        result = verify_service_signature(
            read_bounded(args.envelope, MAX_ENVELOPE_BYTES),
            read_bounded(args.manifest, MAX_MANIFEST_BYTES),
            TrustPolicyStore(args.trust_db),
            minimum_revision=args.minimum_revision,
        )
    except OSError:
        print("Local verification input unavailable.", file=sys.stderr)
        return 1
    except ServiceSignatureError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "signature_verified": True,
                "admission_granted": False,
                "declaration_digest": result.declaration_digest,
                "declared_artifact_digest": result.declared_artifact_digest,
                "trust_revision": result.trust_revision,
                "trust_policy_digest": result.trust_policy_digest,
                "signers": list(result.signers),
                "valid_until": result.valid_until,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
