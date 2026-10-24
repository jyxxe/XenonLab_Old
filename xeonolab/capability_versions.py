"""The bounded version subset shared by capability metadata and admission.

Exact, caret, tilde and '*' only; no build metadata, partials or compound ranges.
Precedence follows SemVer 2.0.0, with node-semver's explicit prerelease opt-in
and leftmost-nonzero caret bounds. This is not a package/dependency downloader.
"""

import re

MAX_VERSION_LENGTH = 256
_NUMBER = r"(?:0|[1-9][0-9]*)"
CORE_VERSION = rf"{_NUMBER}\.{_NUMBER}\.{_NUMBER}"
_IDENTIFIER = rf"(?:{_NUMBER}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
SEMVER = re.compile(rf"^{CORE_VERSION}(?:-{_IDENTIFIER}(?:\.{_IDENTIFIER})*)?$")


def parse_version(value):
    if not isinstance(value, str) or len(value) > MAX_VERSION_LENGTH or not SEMVER.fullmatch(value):
        raise ValueError("invalid capability version")
    core, separator, prerelease = value.partition('-')
    # Tagged tuple entries keep numeric identifiers below alphanumeric ones;
    # a stable release sorts above every prerelease of the same core version.
    suffix = tuple((0, int(part)) if part.isascii() and part.isdigit() else (1, part)
                   for part in prerelease.split('.')) if separator else ((2, ''),)
    return (*map(int, core.split('.')), suffix)


def satisfies(version, constraint):
    current = parse_version(version)
    if constraint == '*':
        return '-' not in version  # Wildcard is not consent to prereleases.
    if not isinstance(constraint, str) or not constraint:
        raise ValueError("invalid capability version constraint")
    operator = constraint[0] if constraint[0] in '^~' else ''
    required = constraint[1:] if operator else constraint
    target = parse_version(required)
    if not operator:
        return current == target
    if '-' in version and ('-' not in required or current[:3] != target[:3]):
        return False  # Opt in only to the requested prerelease core tuple.
    major, minor, patch = target[:3]
    if operator == '~':
        upper = (major, minor + 1, 0)
    elif major:
        upper = (major + 1, 0, 0)
    elif minor:
        upper = (0, minor + 1, 0)
    else:
        upper = (0, 0, patch + 1)
    return current >= target and current[:3] < upper
