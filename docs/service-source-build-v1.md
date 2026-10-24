# Service OCI source-build profile, v1

Build type: `https://xenon.jyxxe.com/buildtypes/oci-source/v1`.
This document defines the verifier's first explicit producer contract; it does
not implement or certify a build platform, issue keys or grant deployment.
The URI identifies this contract. The verifier never fetches it.

Use an in-toto Statement/v1 with SLSA provenance/v1. Its DSSE envelope uses
`application/vnd.in-toto+json` or `application/vnd.in-toto.provenance+json`.
Each signature authenticates the actual media type and exact statement bytes.
The statement must contain one subject with the requested OCI SHA-256 digest
and one with the exact capability-manifest-byte SHA-256 digest. Both must occur
exactly once and be distinct. Subject names are informational; they cannot
substitute for digests. Other outputs may also be listed.

The external build interface has exactly these string fields:

```json
{
  "repository": "https://source.example/plugins.git",
  "ref": "0000000000000000000000000000000000000000",
  "config": "services/example/Dockerfile"
}
```

The repository and config path must match a core-approved rule for this exact
signer, builder and plugin namespace. `ref` is the requested declaration's exact
lowercase source commit, not a branch/tag. The producer checks out that immutable
commit from the specified repository, resolves the config **inside that source
tree**, builds the OCI output with that versioned recipe and emits the capability
manifest output. The recipe must define/pin the build inputs and dependencies;
there is no external command/environment/mount/secret override in this profile.
Unknown external parameters are rejected rather than silently ignored.

The source is also recorded among `resolvedDependencies` as the exact URI
`git+<repository>@<ref>` with `digest.gitCommit` equal to the same commit.
Exactly one such source entry is required. Other materials can be reported;
the verifier does not claim recursive dependency completeness or trust.
Standard extension fields are ignored outside the interpreted build interface.
No redirect, URL canonicalization, ref resolution or network request occurs.

`runDetails.builder.id` is an exact, core-approved HTTPS identity. Its public
signer must be explicitly authorized for that identity and namespace, and must
be responsible for accurate build claims and key custody. A tenant/publisher
key is not automatically a builder key. This code does not assess build-platform
isolation, configuration accuracy, reproducibility or a SLSA Build level. Those
need maintainer review before any real builder rule is provisioned.

[SLSA build provenance](https://slsa.dev/spec/v1.2/build-provenance),
[SLSA verification](https://slsa.dev/spec/v1.2/verifying-artifacts),
[in-toto Statement](https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md)
and [in-toto envelope](https://github.com/in-toto/attestation/blob/main/spec/v1/envelope.md)
provide the standard structures and verification model. This is a documented
application profile, not a general verifier for every build type or Sigstore
bundle, nor an independently certified SLSA implementation.
