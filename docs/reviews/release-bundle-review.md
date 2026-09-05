# Release bundle scoped review

Reviewed fix: `686d72b`, following initial implementation `ff86f18`.
Review date: 2026-09-05. Scope: bundle codec and filesystem helpers, not the
installer, publication workflow, data completeness, or production deployment.

## Review provenance

Fable 5 completed the initial review and identified byte-at-a-time verification,
noncanonical USTAR identity fields, and the missing independent decoder-window
bound. The coordinating reviewer also required early reviewed expansion limits
and descriptor cleanup on admission failure.

The Fable scoped re-review process ended with timeout exit code 124 and no verdict.
It is not counted as approval. The coordinating model performed the user-authorized
fallback review of the complete codec/helpers and the fix's regression-test diff.

## Findings and resolution

- The verifier now scans block headers with positional reads, then performs one
  bounded native decoding pass. It authenticates compressed bytes first and rejects
  trailing or additional frames before extraction can be published.
- Header-offset arithmetic, block-type interpretation, the one-byte RLE payload,
  and the four-byte checksum boundary agree with the authoritative
  [Zstandard format specification, version 0.4.5](https://github.com/facebook/zstd/blob/dev/doc/zstd_compression_format.md).
  Native decoding remains responsible for compressed-block validity and checksum
  verification; the structural scan alone does not prove valid decoded content.
- The header's window size is checked against an independent 8 MiB default before
  allocating the decoder. Reviewed member counts and expansion bounds are enforced
  before decoded file writes, in addition to local limits.
- Nonzero USTAR user/group names and device fields are rejected. Injected `fstat`
  failures close the admitted descriptor. Failed staging publication fsync reports
  a durability error and attempts removal of only the call-owned output.

No remaining Critical or Important finding in this scoped fix. Scoped specification
and quality verdict: pass, with the limitations below. This is not whole-release
or whole-repository security approval.

## Verification

Fresh coordinating-agent verification at `a5cb6f4`, which includes this fix:

```text
make ci-local GENEFOUNDRY_ROUTER_DIR=../genefoundry-router
102 files already formatted
All checks passed!
Both vendor checks: digests and pinned router bytes verified
Success: no issues found in 54 source files
732 passed, 2 warnings in 7.14s
FastMCP/Client/CallToolResult import check succeeded
```

The two warnings concern existing third-party Starlette/AnyIO deprecations.
Regression coverage includes matching-outer-hash checksum corruption and a second
frame, over-window rejection before decoder construction, impossible reviewed
bounds, early expansion rejection, descriptor cleanup, and publication failure.

Earlier local codec-only measurements on a synthetic 4 MiB incompressible payload
were 1.248 seconds for baseline verification versus 0.0047/0.0045 seconds after the
fix. Repeated packing produced identical compressed digests. These are diagnostic
measurements, not a representative data-release benchmark or valid SQLite fixture.

## Limitations and minor follow-ups

- The no-byte-at-a-time regression observes `os.read`, not every possible future
  input primitive. Keep the bounded structural scan visible in future reviews.
- Postcommit temporary hard-link cleanup is best effort. An unlink failure can
  leave an extra link, causing subsequent single-link artifact admission to reject
  the output until the owned temporary link is removed. This is an availability
  limitation, not permission to bypass admission.
- Repeated storage failures can prevent cleanup; no power-loss recovery guarantee
  is inferred from injected single-process failure tests.
- Structural scanning cost depends on block count as well as compressed size.
  The compressed-byte cap bounds it, but the synthetic timing is not a worst-case
  adversarial CPU bound.
