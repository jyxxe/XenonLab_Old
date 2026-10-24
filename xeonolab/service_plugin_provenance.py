"""Verify source-build claims against explicit signer/builder trust rules.

No SLSA level is inferred. Evidence still requires independent publisher,
execution-policy, dependencies, admission and deployment verification.
"""

from dataclasses import dataclass

from .service_plugin_contract import _load, validate_service_plugin
from .service_plugin_signatures import _verified_payload
from .service_plugin_trust import SOURCE_BUILD_TYPE, ServiceSignatureError, TrustPolicyStore, digest

PAYLOAD_TYPE = 'application/vnd.in-toto+json'
PAYLOAD_TYPES = (PAYLOAD_TYPE, 'application/vnd.in-toto.provenance+json')
STATEMENT_TYPE = 'https://in-toto.io/Statement/v1'
PREDICATE_TYPE = 'https://slsa.dev/provenance/v1'
MAX_PROVENANCE_BYTES = 32 * 1024


@dataclass(frozen=True)
class ProvenanceEvidence:
    declaration_digest: str
    statement_digest: str
    artifact_digest: str
    capability_manifest_digest: str
    release_commit: str
    builder_id: str
    repository: str
    build_config: str
    signers: tuple[str, ...]
    trust_revision: int
    trust_policy_digest: str
    checked_at: int
    valid_until: int


def _object(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError
    return value


def _array(value, limit):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError
    return value


def _claims(payload, declaration):
    statement = _object(_load(payload, MAX_PROVENANCE_BYTES))
    if statement.get('_type') != STATEMENT_TYPE or statement.get('predicateType') != PREDICATE_TYPE:
        raise ValueError
    subjects = _array(statement.get('subject'), 16)
    matching = {declaration.artifact_digest.removeprefix('sha256:'): 0,
                declaration.capability_manifest_digest.removeprefix('sha256:'): 0}
    if len(matching) != 2:
        raise ValueError  # This profile requires distinct OCI and manifest outputs.
    for subject in subjects:
        subject = _object(subject)
        hashes = _object(subject.get('digest'))
        if not hashes or any(not isinstance(k, str) or not isinstance(v, str) for k, v in hashes.items()):
            raise ValueError
        if hashes.get('sha256') in matching:
            matching[hashes['sha256']] += 1
    if any(count != 1 for count in matching.values()):
        raise ValueError
    predicate = _object(statement.get('predicate'))
    definition = _object(predicate.get('buildDefinition'))
    builder = _object(_object(predicate.get('runDetails')).get('builder'))
    if definition.get('buildType') != SOURCE_BUILD_TYPE or not isinstance(builder.get('id'), str):
        raise ValueError
    parameters = _object(definition.get('externalParameters'))
    if set(parameters) != {'repository', 'ref', 'config'} or any(
        not isinstance(value, str) for value in parameters.values()
    ) or parameters.get('ref') != declaration.release_commit:
        raise ValueError
    # This versioned build profile defines exactly how repository/ref become a
    # material URI. No URL normalization, redirects, fetching or ref resolution.
    source_uri = 'git+' + parameters['repository'] + '@' + declaration.release_commit
    sources = []
    for dependency in _array(definition.get('resolvedDependencies'), 64):
        dependency = _object(dependency)
        if dependency.get('uri') == source_uri:
            sources.append(_object(dependency.get('digest')).get('gitCommit'))
    if sources != [declaration.release_commit]:
        raise ValueError
    # Other standard/extension metadata is ignored; externalParameters is the
    # explicitly interpreted build interface and must have no unknown fields.
    return builder['id'], parameters['repository'], parameters['config']


def verify_service_provenance(envelope_bytes, declaration_bytes, manifest_bytes, store: TrustPolicyStore,
                              *, minimum_revision=0):
    snapshot = store.snapshot(minimum_revision=minimum_revision)
    try:
        payload, accepted = _verified_payload(envelope_bytes, snapshot, PAYLOAD_TYPES, MAX_PROVENANCE_BYTES)
        declaration = validate_service_plugin(declaration_bytes, manifest_bytes)
        builder, repository, config = _claims(payload, declaration)
        current = store.snapshot(minimum_revision=snapshot.revision)
        if current.revision != snapshot.revision or current.policy_digest != snapshot.policy_digest:
            raise ValueError
        eligible = [key for key in current.policy.keys
                    if key.fingerprint in accepted and key.fingerprint not in current.revoked_keys
                    and declaration.plugin_id in key.plugin_ids
                    and key.not_before <= current.checked_at < key.expires_at
                    and any(rule.builder_id == builder and rule.repository == repository
                            and rule.build_config == config and declaration.plugin_id in rule.plugin_ids
                            for rule in key.provenance_builders)]
        if (len(eligible) < current.policy.threshold or declaration.artifact_digest in current.revoked_artifacts):
            raise ValueError
        chosen = sorted(eligible, key=lambda key: key.fingerprint)[:current.policy.threshold]
        return ProvenanceEvidence(digest(declaration_bytes), digest(payload), declaration.artifact_digest,
                                  declaration.capability_manifest_digest, declaration.release_commit,
                                  builder, repository, config,
                                  tuple(key.fingerprint for key in chosen), current.revision,
                                  current.policy_digest, current.checked_at,
                                  min(current.policy.expires_at, *(key.expires_at for key in chosen)))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ServiceSignatureError('service provenance verification failed') from None
