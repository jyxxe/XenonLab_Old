"""Read-only local source-build verification; never creates admission or trust roots."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.validate_service_plugin import read_bounded  # noqa: E402
from xeonolab.service_plugin_contract import MAX_DECLARATION_BYTES, MAX_MANIFEST_BYTES  # noqa: E402
from xeonolab.service_plugin_provenance import verify_service_provenance  # noqa: E402
from xeonolab.service_plugin_signatures import MAX_ENVELOPE_BYTES  # noqa: E402
from xeonolab.service_plugin_trust import ServiceSignatureError, TrustPolicyStore  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('envelope', type=Path)
    parser.add_argument('declaration', type=Path)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--trust-db', type=Path, required=True)
    parser.add_argument('--minimum-revision', type=int, default=1)
    args = parser.parse_args()
    try:
        result = verify_service_provenance(read_bounded(args.envelope, MAX_ENVELOPE_BYTES),
                                           read_bounded(args.declaration, MAX_DECLARATION_BYTES),
                                           read_bounded(args.manifest, MAX_MANIFEST_BYTES),
                                           TrustPolicyStore(args.trust_db),
                                           minimum_revision=args.minimum_revision)
    except OSError:
        print('Local verification input unavailable.', file=sys.stderr)
        return 1
    except ServiceSignatureError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps({'provenance_verified': True, 'admission_granted': False,
                      'declaration_digest': result.declaration_digest, 'statement_digest': result.statement_digest,
                      'artifact_digest': result.artifact_digest, 'release_commit': result.release_commit,
                      'capability_manifest_digest': result.capability_manifest_digest,
                      'trust_revision': result.trust_revision, 'trust_policy_digest': result.trust_policy_digest,
                      'signers': list(result.signers), 'valid_until': result.valid_until}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
