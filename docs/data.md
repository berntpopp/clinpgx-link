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
`data-clinpgx-core-cfde21bfec473b35`. It contains 15 ClinPGx datasets, 453,102 records,
713 source members, and 2,513,400 memberships under database schema `1.0.0`. Its source
manifest digest is `94d032c42d43bbc742f4278a5a524b3026515e749d9d169d45387b9d7721ddf1`;
the published `clinpgx-core.tar.zst` digest is
`481f30612a1711683d52faea535a29327a88374f12b907d4c942cbefd2c8246b`, and the published
`data-release-manifest.json` digest is
`80e22f9ace76ff136129c8eb5cc67fb1454c291ee2365d7e797e22d2da2845e7`. The release also
publishes `validation-report.json`. The separate local `SHA256SUMS` file is build evidence;
it is not a release asset or an installation prerequisite.

The release preserves the source archives' notices and records the publisher-policy
discrepancy. The human owner's authorization covers distribution of this exact bundle only;
it does not change the underlying source-license classification. Use the native offline
installer and independently pinned manifest/artifact digests described in
[`deployment.md`](deployment.md#production-data-contract). The application and bundle
versions remain independently immutable, and the application release declares the exact
runtime-v1 identity and snapshot ID it can serve.

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
