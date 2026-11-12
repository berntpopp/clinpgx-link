# Data & Provenance

ClinPGx Link aggregates evidence across public pharmacogenomics resources while maintaining
strict source attribution and integrity.

## Upstream Sources

1. **ClinPGx REST API** (`https://api.clinpgx.org/v1`): Real-time lookups for genes, drugs, guidelines, and annotations.
2. **CPIC REST API** (`https://api.cpicpgx.org/v1`): Clinical Pharmacogenetics Implementation Consortium guidelines and dosing recommendations.
3. **ClinPGx Website** (`https://clinpgx.org`): Verified HTML and JSON paths for guidelines, attachments, and citations.
4. **Curated Downloads** (`https://s3.pgkb.org`): 120 download entries captured in the source registry.

## Local Snapshots and Content Store

- **SQLite Dataset Repository**: Read-only local SQLite database indexing snapshot rows with FTS and structured queries.
- **Content Store**: SQLite-backed local cache retaining complete raw bodies, parsed representations, and member bytes. Stored content is addressed by opaque references (`content:<sha256>` or `asset:<sha256>`).
- **Private Permissions**: Cache directories are strictly owned (`0o700`) to protect local data.

## Immutable ClinPGx Core Release

The production snapshot is distributed as the immutable GitHub release
`data-clinpgx-core-d91c0c4082f14706`. It contains 15 ClinPGx datasets, 624,516 records,
714 source members, and 3,179,953 memberships under database schema `1.0.0`. Its source
manifest digest is `88d6d19c4acd88689139b35a09959a154ccca0e4eabf01bc94aabd2260980e4a`;
the published `clinpgx-core.tar.zst` digest is
`6b2fdb4a6b4a975de8c7cd58c17a99c5e96c8bc42c088c2393c5b037a6431446`, and the published
`data-release-manifest.json` digest is
`2db0287d6edf47368fd5833670026cd1264e80f5d23ce82c5dfc262406e5a351`. The release also
publishes `validation-report.json` and `SHA256SUMS`; every asset carries a GitHub
build-provenance attestation from `data-release.yml`. `SHA256SUMS` is a convenience, not an
installation prerequisite: installation trusts only the independently pinned digests.
It supersedes `data-clinpgx-core-cfde21bfec473b35` (453,102 records, archives of 2026-09-05).

The release preserves the source archives' notices and records the publisher-policy
discrepancy. The owner's 2026-11-12 authorization covers distribution of this new bundle;
it does not change the underlying source-license classification. Use the native offline
installer and independently pinned manifest/artifact digests described in
[`deployment.md`](deployment.md#production-data-contract). The application and bundle
versions remain independently immutable, and the application release declares the exact
runtime-v1 identity and snapshot ID it can serve.

A manifest's `application_compatibility` is evaluated at patch-tolerant semantic-version
granularity: an application version is admitted when it is `>= minimum` and its
`major.minor` is `<=` the maximum's `major.minor` (a maximum of `0.1.3` admits every
`0.1.x >= minimum`, never `0.2.0`). Patch releases must therefore never change the data
contract; any data-contract change requires a minor bump. The database schema version
(`schema.minimum`/`maximum`/`actual`) remains an exact, strict check.

## Building a Data Release

Data releases are produced only by the in-repository builder, `clinpgx-link data build`
(`make data-release` locally; the manually dispatched `.github/workflows/data-release.yml`
publishes). It downloads the 15 official core archives into a frozen acquisition receipt, or
rebuilds offline from one with `--acquisition`, then builds, packs, installs into a fresh
volume, and verifies the bundle before writing the four release assets. Identical archives,
receipt, authorization, transformation code and SQLite toolchain reproduce the same release
tag, snapshot ID and inner contract files.

The owner's distribution authorization is the reviewed input file
[`release/data-authorization.json`](../release/data-authorization.json): reviewer, date,
quoted decision, and the SHA-256 of the exact upstream `LICENSE.txt` it covers. A changed
upstream notice stops the build until a new authorization is reviewed. Authorization record:

| Date | Reviewer | Scope |
|---|---|---|
| 2026-10-04 | Human user (Codex thread `01a101a5-1039-7ac3-9724-36bfcdbe21be`) | Exact bundle `data-clinpgx-core-cfde21bfec473b35` |
| 2026-11-12 | Repository owner (operator decision) | A new core bundle built from the archives current on 2026-11-12 (notice `7156ee5b…`), published as `data-clinpgx-core-d91c0c4082f14706` |

## Response Modes

Tools accepting `response_mode` offer four levels of granularity:

- `minimal`: Bare identifiers, symbols, and core names.
- `compact` (default): Curated summary fields with provenance and next-command hints.
- `standard`: Expanded attributes and relationships.
- `full`: Complete uncompressed record fields and nested structures.

Compact dataset catalog and description responses retain archive-level limitations,
warnings, and provenance. When more than 16 per-member parse limitations would repeat
in one compact summary, the response reports `unparsed_member_count` and
`member_limitations_paged`; `get_dataset` returns each member's limitation on its
snapshot-bound cursor pages, with the exact member bytes available through its
`content_ref`. The regular response and untrusted-text size limits still apply.

## Timing and Metadata

Every tool result includes standard metadata:
- `_meta.elapsed_ms`: Wall-clock execution time in milliseconds.
- `_meta.timing_scope`: `"tool_boundary"`.
- `source`: Upstream URL, timestamp, SHA-256 digest, and data provider.

## Citation

When using ClinPGx Link, cite the primary ClinPGx / PharmGKB publication:

> Whirl-Carrillo M, et al. Pharmacogenomics Knowledge for Personalized Medicine: 2021 Update. *Clin Pharmacol Ther.* 2021;110(4):883-891. doi:10.1002/cpt.2356
