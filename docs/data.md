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

## Response Modes

Tools accepting `response_mode` offer four levels of granularity:

- `minimal`: Bare identifiers, symbols, and core names.
- `compact` (default): Curated summary fields with provenance and next-command hints.
- `standard`: Expanded attributes and relationships.
- `full`: Complete uncompressed record fields and nested structures.

## Timing and Metadata

Every tool result includes standard metadata:
- `_meta.elapsed_ms`: Wall-clock execution time in milliseconds.
- `_meta.timing_scope`: `"tool_boundary"`.
- `source`: Upstream URL, timestamp, SHA-256 digest, and data provider.

## Citation

When using ClinPGx Link, cite the primary ClinPGx / PharmGKB publication:

> Whirl-Carrillo M, et al. Pharmacogenomics Knowledge for Personalized Medicine: 2021 Update. *Clin Pharmacol Ther.* 2021;110(4):883-891. doi:10.1002/cpt.2356
