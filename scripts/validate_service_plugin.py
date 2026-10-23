"""Validate explicitly supplied local proposal files without admission or execution."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xeonolab.service_plugin_contract import (  # noqa: E402
    MAX_DECLARATION_BYTES,
    MAX_MANIFEST_BYTES,
    ServicePluginContractError,
    validate_service_plugin,
)


def read_bounded(path, limit):
    with path.open('rb') as stream:
        return stream.read(limit + 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('declaration', type=Path)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    try:
        validate_service_plugin(
            read_bounded(args.declaration, MAX_DECLARATION_BYTES),
            read_bounded(args.manifest, MAX_MANIFEST_BYTES),
        )
    except OSError:
        print('Local input could not be read.', file=sys.stderr)
        return 1
    except ServicePluginContractError as error:
        print(str(error), file=sys.stderr)
        return 1
    print('Contract valid; provenance, policy, admission and runtime are NOT verified.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
