# Source access contract

This is a binding addendum to the ClinPGx Link design and implementation plan.
It resolves the independent own-model review before implementation. Source payloads
remain untrusted data, not model instructions or clinical recommendations.

## Public tools and retrieval

All tools return the fleet envelope. `response_mode` is minimal/compact/standard/full
(compact default). Paged tools use limit=20 (1–100), offset=0 and optional cursor;
cursor and nonzero offset are mutually exclusive. Query hashes include all selectors,
including representation and pointer, but not response mode or requested page size.

| Tool | Required arguments | Optional arguments beyond common mode/page controls |
| --- | --- | --- |
| get_server_capabilities | none | none; not paged |
| get_diagnostics | none | probe_upstream=false; not paged |
| search_records | entity_type | query, filters={}, source=auto/api/download, view=base |
| get_record | entity_type, record_id | source=api/website/download (api default), view=max, pointer="" |
| get_related_records | record_id, result_type | other_id, entity_type=Gene, other_type=Chemical, source=api/download |
| get_api_schema | none | operation, namespace=all/api/website |
| get_api_data | operation | path_parameters={}, query_parameters={}, form_parameters={}, representation=json/jsonld/html/text, pointer="" |
| get_website_data | operation | path_parameters={}, query_parameters={}, pointer="" |
| list_datasets | none | query, include_legacy=true |
| get_dataset | dataset_id | none; members paged |
| search_dataset | dataset_id | member, query, filters={}, match=exact/member |
| get_dataset_record | record_id | pointer="" |
| get_source_content | content_ref | pointer="", representation=structure/text/base64, start=0, length=4096 (1–8192); not row-paged |

`get_dataset` applies presentation modes before fencing optional member metadata.
Every mode retains dataset/member identity, provenance, digests, license, counts,
limitations, profile status and drift/unprofiled evidence. Minimal retains profile
identity, shape, status and missing required fields; compact adds executable tabular
field/filter/type metadata plus exact profile field lists, modes and selectors without
descriptive prose; standard adds full sheet/field/profile descriptions except profile
field inclusion reasons; full returns the complete bounded declaration. A projected
response identifies the omission and supplies an executable full-mode command for the
same member page. Projection never labels source data truncated, changes cursor
membership, or replaces the complete metadata and exact source bytes available through
full mode and retained-content references.

Entity types and result types derive from verified registry mappings, with closed
enums in tool schemas. Dataset identifiers come from the catalog, never arbitrary
paths. Stable operation IDs are `GET /data/...`, `GET /report/...`, `POST /infobutton`,
or `GET /site/...`; namespace is inferred from a vendored allowlisted registry.
`get_api_data` cannot call `/site` operations; `get_website_data` cannot call arbitrary
paths. Path/query/body binding rejects unknown fields and validates against the
observed contract, including fixed values and runtime-required search criteria.
Infobutton form values are supported only by its read-only verified operation.
Representation changes use a route allowlist and Accept header, not URL concatenation.
JSON-LD retains the original `@context`, `@id`, and all source fields; absent route
support is an explicit invalid_input subtype, not silent JSON substitution.

Verified website families (allele/haplotype/function/frequency; CPIC gene listings;
prescribing; VIP; labels/FDA; AMP; publications; pathways) must each map to at least
one actual operation in `api/website_operations.json`, cited in `data/coverage.json`.
Website-only families are discoverable through capabilities and get_api_schema.
Examples use real sampled IDs; a working Python adapter alone is not MCP coverage.
Document/graphic links found in allowlisted responses register an opaque content_ref.
Attachment fetches occur only from those validated references and approved HTTPS
origins/path prefixes, never from a caller-supplied URL. References bind upstream
origin/path/version and, once acquired, SHA-256. The source page and final URL are
provenance, not permission to follow arbitrary embedded links.

## Large content without dead ends

The content store holds bounded complete API/website bodies and exact downloaded
archive/member bytes. Default decoded network body cap is 128 MiB; operator bulk
archive acquisition cap is 256 MiB per compressed artifact, 2 GiB aggregate expanded
archive, 128 MiB per member, with explicit configurable disk/row/expansion limits.
These are safety defaults, not assertions that all future upstream data fits them.
Do not emit successful partial HTTP bodies. A cap failure returns operation-specific
recovery: indexed equivalent, narrower verified filter, or an explicit unresolved
coverage error. Coverage acceptance fails if a required operation has no working
recovery under the tested configuration. Cap settings are operator-only.

Every acquired result has a content_ref derived from source identity plus bytes.
References carry immutable SHA-256, media type, byte count, expiry/retention policy,
and whether exact bytes are available offline. API content/cache eviction must not
invalidate references during their advertised TTL (default 1 hour); pinning is
bounded by cache capacity, and admission failure is explicit. Repository references
bind immutable snapshot identity and survive while that release is retained.

`get_source_content` traverses RFC 6901 pointers after validation. `structure` returns
scalar type/length/digest, or bounded object-key/array-index descriptors with child
pointers, lengths and types. A directly selected scalar and immediate scalar children
include `value` when the existing finite scalar serialization is at most 256 UTF-8 bytes;
strings are fully fenced at the MCP boundary while numbers, booleans and null retain their
JSON types. The presence of `value: null` distinguishes an inlined null from no inline
value. Large keysets are sliced by start/length. Containers and larger scalars remain
descriptor-only, and no descriptor recursively embeds a container. The normal record
tools may return a typed content descriptor for an oversized field, explicitly marking
deferred_content=true; they may not silently omit the field. All descriptor names are
safely fenced.

`text` returns lossless Unicode codepoint slices of string values, with start,
returned_characters, total_characters, has_more, next_start and full UTF-8 raw digest.
Sanitization remains mandatory in visible text. If sanitization changes source bytes,
exact reconstruction uses `base64`, not sanitized text. `base64` slices ORIGINAL bytes
using byte offsets, independently encodes each chunk and reports full digest. An MCP
agent can concatenate decoded chunks to reconstruct exact bytes without external
network access. At least one unit of progress is required on every nonfinal chunk.
Exact-byte base64 requires the empty pointer; nonempty pointers are rejected with
guidance to retrieve the original body. It never canonicalizes a selected object.
Scalar structure `sha256` is the UTF-8 decoded string digest, or canonical JSON scalar
digest for number/boolean/null (with `digest_representation` declared); `source_sha256`
always identifies the original complete body. Scalar length follows the same declared
representation. Structure pages have a 32 KiB serialized descriptor budget and real
continuation. A child whose escaped pointer exceeds 4096 characters/128 segments
causes an explicit size error with empty-pointer base64 recovery, never an unusable
advertised pointer or silent omission. Exact original-body retrieval remains available.
Binary documents also support text via bounded PDF extraction (page provenance),
and XLSX via the importer; extracted content has its own digest and is explicitly a
derived representation. Unsupported binary formats remain byte-retrievable with a
typed parsing limitation, never described as queryable text or tables.

Tests reconstruct the measured-size 148,743-character scalar and control characters,
nested evidence arrays, oversized first rows, multibyte boundaries and binary PDFs;
exercise fencing overhead, TTL expiry, changed digest, and HTTP-cap fallback. MCP
response budget remains 25,000 estimated tokens, including fences and metadata.

## Local discovery and joins

`search_records(source=auto)` selects local indexes for free text/aliases and broad
discovery when installed, and explicit API exact filters otherwise. It reports the
selected source and its own coverage; absence from a snapshot is not current upstream
absence. Unsupported broad discovery without a mirror is an actionable error, never
an attempted unfiltered gene API request. Explicit source selectors never silently
switch representation. API live detail hydration is a separate get_record call.

Preserve raw fields and maintain normalized entity/alias/identifier and membership
tables with source member + row + field provenance. `search_dataset(match=exact)`
means exact cell matching; `match=member` uses only a declared per-field tokenizer
(CSV-aware delimiters and source-specific semantics). Unknown membership semantics
are an error. General substring splitting cannot infer gene/drug lists.
Aliases and normalized memberships feed source=download searches and relationships.
Summary annotations join evidence/allele rows by documented annotation identity, not
text similarity. Reverse relationship export rows preserve their published direction
and do not imply biological causality. No source enum excludes Swissmedic, FDA or
non-CPIC/DPWG records from local discovery. Every joined component retains provenance.

Local `search_records.filters` accepts canonical `id`, `name`, `gene`, `chemical`,
`variant`, `source`, and `annotation_id` keys, AND across keys; entity-specific
unsupported keys fail. `id` is exact normalized identifier, `name` includes declared
aliases, relation fields mean exact member identity or source label, not whole-cell
equality. `query` is literal FTS token search (AND tokens) across indexed source text.
Capabilities declare which entity/filter combinations exist in the installed profile.
`get_related_records(source=download,result_type=evidence|allele|literature)` resolves
summary annotation IDs through validated membership tables and returns original joined
rows; other result types use the declared entity/relationship projection. Pagination
counts refer to joined source rows, not distinct biological claims. Implementations
use DatasetRepository.search_entities and .related; do not route these through the
API-only ApiService. Form/representation validation returns one BoundRequest carrying
all validated query and form values; the client never receives unvalidated form data.

`get_server_capabilities.result.local_search` publishes per-entity filter keys,
operators and supported result types. `get_dataset.result.members[].fields[]`
publishes exact field names plus `match_modes`, delimiter/quoting tokenizer IDs and
semantic target (if known). Joined rows retain the standard repository row shape
(`record_id`, `dataset_id`, `member`, `ordinal`, `fields`) plus `join` containing
validated parent record ID, relation kind and contributing source row IDs; `_meta`
identifies the pinned snapshot. No joined text replaces its original source row.
Required real-MCP acceptance examples: canonical gene+chemical filters find a member
inside multigene/multidrug annotation cells; annotation ID→evidence and →allele return
all fixture child rows once; reverse relationship rows retain endpoints; Swissmedic
labels and non-CPIC/DPWG guidelines remain discoverable with source=download.

## Snapshot/activation and release boundaries

Builder creates candidates only. The release installer exclusively owns activation.
Repository calls resolve and open one immutable snapshot handle, validating identity,
counting and selecting rows on that handle. `search` and `get_record` accept optional
expected_snapshot; mismatch fails before returning rows. Cursor decoding passes this
identity into the repository, not a separate check of mutable `current`. Test a
deliberate activation/rollback between cursor decoding, count and row selection.

Development API-only mode has liveness health and explicit no-mirror readiness.
Production data-bound mode requires configured expected runtime identity; readiness
and data tools fail closed on missing/mismatched identity. `/health` includes fleet
required-data readiness in production; `/api/live` provides process-only liveness.
Activation of a new production release requires a coordinated restart with its new
expected digest; an old process cannot silently serve newly activated data. Rollback
uses the retained release digest and restarts under that expected configuration.

The four-file release bundle retains exact source archives and member bytes in
content-addressed SQLite BLOB tables, including unparsed members. Immutable upstream
version URLs may supplement, never replace, required retained bytes. Build cache
removal and upstream URL replacement must not break installed historical retrieval.
Observation timestamps live in an external acquisition/validation report; immutable
source manifests use frozen source-input acquisition evidence supplied to the build.
Same source bytes/transform/config/epoch inputs produce identical bundled bytes even
when later observations occur at different times. Parser/config/coverage/license
evidence changes alter release identity. See the revised release design for the exact
fleet manifest, staged predecessor bootstrap, trust and rollback contracts.

## Coverage acceptance

Inventory accounting and usable objective coverage are separate gates. Accounting
requires every one of the captured 120 registry entries plus observed nonregistry
reference sources. Each entry independently records discovered, acquired, parsed,
searchable, field_complete, mcp_retrievable, source date, limitations, and evidence.
An explicit unavailable entry passes accounting only; it cannot pass objective
coverage. Objective coverage requires actual indexed data or a tested callable live
fallback for each public reference-data family/field claim. Source withdrawal or
corruption remains an explicit incomplete requirement unless an equivalent working
fallback is demonstrated. A PDF magic-number probe establishes bytes, not text.
External patient computation is out of scope; public packaged reference tables are
in scope even if distributed by an external source linked by ClinPGx.

## Benchmark acceptance

Freeze a checked-in case manifest before implementation tuning: the existing twelve
benchmark-design cases plus website allele function, population frequency, highlighted
PDF text, PharmCAT-vs-monthly guideline distinction, multivalue annotation join and
large-content continuation. Each has sourced record IDs, assertions/citations and
required tools/coverage, not an LLM-generated answer key.
Real-agent acceptance: all 18 attempted and completed, all deterministic data/provenance
and no-fabrication assertions pass, and at least 90% of the frozen noncritical rubric
points. An unavailable case is reported separately and prevents completion, not
dropped from the denominator. Run limits: at most 30 tool calls and 180 seconds per
case, one primary run with any reruns separately reported. Record exact model/client
versions, source identities, attempted/completed/unavailable counts, latency and
tokens. Use an available explicitly authorized agent model if Claude quota blocks
the harness; preserve actual local HTTP MCP traces rather than claiming a mock is
an agent benchmark. Live changes require versioned answer-key updates with evidence.
