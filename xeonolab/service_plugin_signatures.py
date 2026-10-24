"""DSSE/Ed25519 signatures over exact service-declaration bytes.

This checks a signing claim against current core-owned policy. It fetches no
artifact and grants no service admission, user capability or deployment right.
"""

from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .service_plugin_contract import (
    MAX_DECLARATION_BYTES,
    ServicePluginContractError,
    _load,
    validate_service_plugin,
)
from .service_plugin_trust import ServiceSignatureError, TrustPolicyStore, decode_base64, digest

PAYLOAD_TYPE = "application/vnd.xeonolab.service-plugin-declaration.v1+json"
MAX_ENVELOPE_BYTES = 64 * 1024
MAX_SIGNATURE_BYTES = 512


def pae(payload_type, payload):
    """DSSE v1.0.2 PAE: UTF-8 byte lengths, not character counts."""
    encoded_type = payload_type.encode("utf-8")
    return (
        b"DSSEv1 "
        + str(len(encoded_type)).encode()
        + b" "
        + encoded_type
        + b" "
        + str(len(payload)).encode()
        + b" "
        + payload
    )


def _valid_signature(public, signature, message):
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(signature, message)
        return True
    except (InvalidSignature, ValueError, UnsupportedAlgorithm):
        return False


@dataclass(frozen=True)
class SignatureEvidence:
    declaration_digest: str
    declared_artifact_digest: str
    capability_manifest_digest: str
    signers: tuple[str, ...]
    trust_revision: int
    trust_policy_digest: str
    checked_at: int
    valid_until: int


def verify_service_signature(envelope_bytes, manifest_bytes, store: TrustPolicyStore, *, minimum_revision=0):
    snapshot = store.snapshot(minimum_revision=minimum_revision)
    try:
        if not isinstance(envelope_bytes, bytes) or len(envelope_bytes) > MAX_ENVELOPE_BYTES:
            raise ValueError
        envelope_bytes.decode("utf-8")
        envelope = _load(envelope_bytes, MAX_ENVELOPE_BYTES)
        if not isinstance(envelope, dict) or envelope.get("payloadType") != PAYLOAD_TYPE:
            raise ValueError
        payload = decode_base64(envelope.get("payload"), MAX_DECLARATION_BYTES)
        signatures = envelope.get("signatures")
        if not isinstance(signatures, list) or not 1 <= len(signatures) <= 16:
            raise ValueError
        decoded = []
        for entry in signatures:
            if not isinstance(entry, dict):
                raise ValueError
            keyid = entry.get("keyid", "")
            signature = decode_base64(entry.get("sig"), MAX_SIGNATURE_BYTES)
            if not isinstance(keyid, str) or len(keyid) > 128:
                raise ValueError
            if len(signature) != 64:
                continue  # Decoded but not an Ed25519 signature; cannot count.
            decoded.append((keyid, signature))
        # Unknown envelope/signature fields are ignored per DSSE, not executed
        # or forwarded to the declaration parser. Decode each payload only once.
        message, accepted = pae(PAYLOAD_TYPE, payload), set()
        for keyid, signature in decoded:
            for key in snapshot.policy.keys:
                if keyid and keyid != key.fingerprint:
                    continue  # Unauthenticated candidate hint, never a grant.
                if key.fingerprint in snapshot.revoked_keys:
                    continue
                if not key.not_before <= snapshot.checked_at < key.expires_at:
                    continue
                if _valid_signature(key.public_key, signature, message):
                    accepted.add(key.fingerprint)
        if len(accepted) < snapshot.policy.threshold:
            raise ServiceSignatureError("service signature not verified")
        payload.decode("utf-8")
        declaration = validate_service_plugin(payload, manifest_bytes)
        # Re-read current authority before returning evidence. A change during
        # crypto/contract work safely fails; callers must reverify for admission.
        current = store.snapshot(minimum_revision=snapshot.revision)
        if current.revision != snapshot.revision or current.policy_digest != snapshot.policy_digest:
            raise ServiceSignatureError("trust policy changed during verification")
        eligible = [
            key
            for key in current.policy.keys
            if key.fingerprint in accepted
            and key.fingerprint not in current.revoked_keys
            and declaration.plugin_id in key.plugin_ids
            and key.not_before <= current.checked_at < key.expires_at
        ]
        if len(eligible) < current.policy.threshold:
            raise ServiceSignatureError("service signature threshold or namespace not verified")
        if declaration.artifact_digest in current.revoked_artifacts:
            raise ServiceSignatureError("declared service artifact revoked")
        chosen = sorted(eligible, key=lambda key: key.fingerprint)[: current.policy.threshold]
        return SignatureEvidence(
            digest(payload),
            declaration.artifact_digest,
            declaration.capability_manifest_digest,
            tuple(key.fingerprint for key in chosen),
            current.revision,
            current.policy_digest,
            current.checked_at,
            min(current.policy.expires_at, *(key.expires_at for key in chosen)),
        )
    except (ValueError, TypeError, UnicodeError, ServicePluginContractError):
        # Keep the public diagnostic fixed even when a malformed declaration or
        # unknown field contains a token, URL, filename or private error detail.
        raise ServiceSignatureError("service signature verification failed") from None
