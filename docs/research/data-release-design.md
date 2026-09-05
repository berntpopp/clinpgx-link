# ClinPGx immutable data-release design

Research/design input for the ClinPGx MCP specification, based on the local
GeneFoundry fleet as inspected on 2026-09-05. This document does not claim that a
ClinPGx release has been built or published. Digests and URLs for a real ClinPGx
release must be produced and verified by the implemented workflow; they must not
be invented in the application manifest.

## Decision summary

Adopt the fleet's `external-reference` data mode and current router data-release
manifest unchanged as the outer publication/runtime contract. Put ClinPGx's
multi-source detail in strict, versioned files inside a deterministic bundle. The
default production profile is `core`; larger formats are separate `extended`
releases, and unreviewed registry items remain `catalog-only`. Never use a profile
called `full`, because the current evidence does not support a completeness claim.

Build and validate candidates on a schedule, but make public publication manual,
protected and fail-closed until a human rights review has explicitly approved the
exact bundle and distribution mode. The currently observed ClinPGx/PharmGKB terms
include CC BY-SA obligations and additional data-usage wording, while related
projects are explicitly outside that agreement. A public GitHub release cannot be
assumed lawful merely because a file is downloadable. Until approval, operators
either use a controlled/private immutable artifact store or reproduce the bundle
locally from official sources.

Application images remain code-only. The authoritative SQLite generation and its
provenance are mounted read-only; the mutable live-API response cache is a separate,
deletable runtime cache and never contributes to data-release identity.

## Alternatives considered

1. **Fleet manifest plus a ClinPGx source-set manifest (recommended).** Preserve
   router/controller compatibility and add exact per-artifact dates, hashes,
   validation, coverage and rights in the bundle. This adds one local schema but
   accurately represents ClinPGx's asynchronous exports.
2. **Extend the fleet manifest itself.** A first-class `sources[]` and `licenses[]`
   would be elegant, but it would require a coordinated router schema/version
   change and updates across consumers before ClinPGx could ship. That is outside
   this repository's scope.
3. **Use only a project-specific manifest, as current `clingen-link` does.** This is
   the least work locally, but it forks the fleet contract. The current ClinGen
   generated manifest omits fields required by the current router schema, including
   `transformation`, `application_compatibility`, `disclaimer`, and the schema's
   singular `license` block
   (`../clingen-link/.github/workflows/data-refresh.yml:44-61`). Do not clone that
   manifest generator.

The recommended design can publish to GitHub Releases after rights approval, to a
private controlled store before approval, or remain operator-local. Distribution
location changes; identity, validation and installation semantics do not.

## Authoritative fleet baseline

### Pin and vendor

At inspection time, local `genefoundry-router` is tag `v0.8.7`, commit
`6568a0ad7d68925440aba550a2a678282ea5bb6b`. The canonical schema is:

- source: `../genefoundry-router/genefoundry_router/data/data-release-manifest.schema.json`
- SHA-256: `302d9ad1ae917988065f5dc8d054da62e7b7931ef2c3288d2ee7ea76d8aa893f`
- last schema-changing commit observed: `4afef94b4bb66c0780c2754d26c925809bc7e806`
  (2026-07-13)

Vendor that exact file into
`vendor/genefoundry/data-release-manifest.schema.json`. Record both its digest and
the router commit in `vendor/genefoundry/CONTRACT_SHA256`. `make vendor-check` must:

1. hash the vendored file and match `CONTRACT_SHA256`; and
2. in CI, check out router at the recorded commit and byte-compare the canonical
   file, so the check cannot silently degrade when a local router path is absent.

This improves on `../clingen-link/Makefile:111-118`, whose upstream byte comparison
only runs when `GENEFOUNDRY_ROUTER_DIR` happens to be set. ClinGen currently vendors
an older schema (`b1fc9f461b2c81e037712d8063812052bf07a8f42ee689dd162875fcf12b3b2d`),
whereas current router schema additionally rejects mutable release labels. It is a
useful pattern, not the source to copy.

Pin the fleet application workflows, separately from data publication, to the same
reviewed router revision:

- `.github/workflows/container-ci.yml` calls
  `berntpopp/genefoundry-router/.github/workflows/_container-ci.yml@6568a0ad7d68925440aba550a2a678282ea5bb6b`
- `.github/workflows/container-release.yml` calls
  `berntpopp/genefoundry-router/.github/workflows/_container-release.yml@6568a0ad7d68925440aba550a2a678282ea5bb6b`

Reconfirm that tag, commit and schema digest immediately before implementation.
The current ClinGen wrappers still pin router `v0.8.6` at `3d3cc204...`
(`../clingen-link/.github/workflows/container-ci.yml:40` and
`../clingen-link/.github/workflows/container-release.yml:20`), so they are not the
preferred pin for a new repository.

### Reuse matrix

| Need | Reuse/adapt | Do not copy blindly |
|---|---|---|
| Fleet publication schema | Current router schema plus `release/data.py:52-167`. | ClinGen's older vendored schema or custom generated manifest. |
| Safe artifact installation | Router `release/data_materialization.py:31-563` behavior and `tests/release/test_data_release.py`. | A direct extract command or digest-only check. |
| Runtime identity | ClinGen `clingen_link/runtime_data_identity.py:15-240`, kept byte-compatible with `runtime-v1`. | Treating artifact/tree/runtime digests as interchangeable. |
| Concrete init topology | ClinGen `container-release.json`, `docker/docker-compose.yml`, and `store/db.py:205-337`. | Bundling authoritative data in the image or allowing init egress. |
| Rights evidence | ClinGen `etl/rights_notice.py:20-134` bounded/exact-shape pattern. | Its one-license ClinGen assumptions. |
| Immutable publication | ClinGen `etl/release_identity.py:168-210`, `data-refresh.yml:149-355`, and workflow tests. | Its three-asset inventory, tag formula, or large inline implementation. |
| Local build/pull/pack UX | MaveDB `ingest/cli.py:65-220` and `ingest/bundle.py:65-76,149-209,266-397`. | Live-only production fallback, implicit newest-release lookup, multithreaded nondeterministic pack, or looser publish path. |
| Application CI/release | Router `_container-ci.yml` and `_container-release.yml` pinned to the reviewed router commit. | Treating the repo-local ClinGen data workflow as a reusable workflow; it is not one. |

The MaveDB implementation is useful evidence that build/pull/pack can remain small,
but the router and ClinGen release state machine are the stricter security and
publication baselines. In particular, MaveDB's `pack()` uses `threads=-1`
(`bundle.py:65-76`); ClinPGx packaging must instead pin settings that are proven
byte-reproducible.

### Outer manifest contract

Use the current `DataReleaseManifest` model verbatim. It is strict and rejects
unknown fields. Its required root fields are:

| Field | ClinPGx value/meaning |
|---|---|
| `schema_version` | Integer `1`, the fleet manifest format, not the SQLite schema. |
| `dataset` | Name, immutable release tag, and the aggregate source-manifest identity. |
| `transformation` | Repository `berntpopp/clinpgx-link` and exact 40-character build commit. |
| `schema` | Semantic-version `minimum`, `maximum`, and `actual` for the SQLite/bundle contract. |
| `record_counts` | Stable logical table/domain counts, all non-negative. |
| `artifact` | Exact filename, compressed and expanded identities and reviewed ceilings. |
| `license` | One reviewed aggregate publication decision; see the rights boundary below. |
| `previous_known_good_digest` | Exact retained compressed artifact digest with `sha256:` prefix. |
| `application_compatibility` | Minimum/maximum compatible ClinPGx application versions. |
| `disclaimer` | Research-use/no-clinical-decision-support warning and freshness qualification. |

The exact types and projection to a runtime requirement are in
`../genefoundry-router/genefoundry_router/release/data.py:52-167`. Notable rules:

- URLs are HTTPS, hashes are lowercase SHA-256, timestamps are RFC 3339, and
  transformation revisions are full Git SHAs.
- `artifact` records actual compressed size, expanded size and member count plus
  explicit maximums; actual values must fit all maximums.
- `dataset.release` rejects mutable labels such as `latest`, `current`, `stable`,
  `main`, `master`, and `head`.
- `validate_publication()` rejects `license.redistribution_allowed=false`
  (`data.py:142-146`). Private/local validation remains possible, but it must not
  be represented as public approval.

The router manifest intentionally has one `dataset.source`, one artifact and one
license. Those fields represent the aggregate release. They do not replace the
ClinPGx per-source inventory below.

## ClinPGx release unit

Publish exactly four assets per profile release:

1. `clinpgx-<profile>.tar.zst`
2. `data-release-manifest.json`
3. `validation-report.json`
4. `SHA256SUMS`

`SHA256SUMS` contains exactly one canonical, filename-keyed line for each of the
first three assets and no line for itself. Release validation rejects missing,
extra, duplicate, symlinked or non-regular assets. Every asset is separately
attested. Making the validation report a top-level asset keeps it inspectable and
lets it refer to the completed bundle digest without creating a circular bundle
identity.

The compressed tar has a fixed four-member inventory:

- `clinpgx.sqlite` — immutable authoritative local index and preserved source fields;
- `schema.json` — strict schema version and semantic probe expectations;
- `source-manifest.json` — canonical per-source provenance, coverage and build input;
- `licenses.json` — canonical per-source/category rights evidence and notices.

Do not add upstream ZIPs as separate tar members, but do retain the exact bytes of
every source admitted to the profile inside `clinpgx.sqlite`. A hash plus a mutable
logical download URL is not historical access. The storage contract includes:

- `source_archive`: artifact snapshot ID, media type, exact byte length, SHA-256,
  and the complete downloaded archive/blob as a SQLite BLOB;
- `source_member`: artifact snapshot ID, canonical member path, media type, exact
  byte length, SHA-256, parser status, and the complete original member bytes as a
  SQLite BLOB; and
- normalized/raw row tables that reference `source_member` plus row number or JSON
  accession without replacing the original bytes.

Retain both archive and member bytes because they serve different audit contracts:
the archive proves the downloaded object, while member bytes allow safe bounded
retrieval without decompressing an archive in the request path. Profile-specific
size ceilings apply to both tables and the packed database. Rights review applies
to these retained bytes exactly as it applies to normalized rows.

The repository exposes digest-bound reads from those BLOBs. The public source-asset
interface selects `archive` or an exact canonical `member`, accepts a non-negative
byte offset and a capped positive length, and returns media type, total bytes,
content SHA-256, offset, returned bytes/encoding, and the next offset or `null`.
Continuation binds the release runtime identity, source digest and member digest;
it cannot cross releases. An MCP-only client can therefore recover an installed
release's complete source content in bounded chunks after the build cache is gone
and even if the upstream logical URL now serves different bytes. A `live` source
fetch is a separately labelled current representation and never masquerades as the
historical release member.

Catalog-only artifacts are not retained merely because they appeared in the
registry. If a reviewed use case needs an additional raw archive, admit it to an
explicitly licensed profile and account for its storage/security ceilings rather
than silently inflating `core`.

Produce deterministic bytes: sort tar paths; normalize file modes to `0444`,
directory modes to `0555`, uid/gid to zero, owner/group names to empty, and mtimes to
a documented `SOURCE_DATE_EPOCH`; use fixed zstd parameters. Pin the Python, SQLite,
zstd, archive/XML/spreadsheet parser versions in `uv.lock` and record their effective
versions in the validation report. Build SQLite in a
stable insertion order, create indexes in a fixed order, set `user_version`, run
deterministic finalization (`ANALYZE` policy fixed and `VACUUM`), close/checkpoint it,
and reopen it immutable for validation. Two builds from identical source bytes,
the same frozen acquisition receipt, transform/rights contracts, toolchain and epoch
must produce the same expanded-tree and compressed digests.

### `source-manifest.json` schema

Define and vendor a project-owned JSON Schema, with `additionalProperties: false`
at every object. Canonical JSON is UTF-8, sorted keys, compact separators, and one
trailing newline. The strict model has:

| Field | Contract |
|---|---|
| `schema_version` | Integer `1`. |
| `profile` | `core` or `extended`; never an open string. |
| `source_set_identity` | `sha256:<64hex>` of the stable release-key projection below. |
| `registry` | URL, retrieved-at, response SHA-256, ETag/Last-Modified when supplied, parser version. |
| `artifacts` | Non-empty, sorted by stable logical name, no duplicates. |
| `coverage` | Exact operations/domains supported locally, live-fallback-only, metadata-only and excluded. |
| `anomalies` | Typed, stable warning identifiers with affected sources/counts; no free-form success override. |

Each `artifacts[]` item contains:

- stable logical name and exact registry filename;
- contract tier: `canonical_page`, `approved_registry`,
  `experimental_or_legacy`, or `related_project`;
- acquisition URL and observed final S3 URL;
- registry `lastModified` and size;
- HTTP `Last-Modified`, `ETag`, `x-amz-version-id`, and `Content-Length` when
  supplied, without pretending an absent header exists;
- local content SHA-256 and acquisition timestamp;
- embedded creation marker and its parsed timestamp when available;
- sorted member inventory with path, compressed/uncompressed sizes and content
  digest for every consumed member;
- parser name/version or transformation-contract digest;
- `indexed`, `metadata_only`, `quarantined`, or `excluded` status and reason;
- imported row/document counts, rejected-row count, and validation result;
- the `license_id` resolved in `licenses.json`;
- normalized coverage and explicit API fallback for source/API representation gaps.

This realizes the already identified provenance requirements in
`docs/research/clinpgx-sources.md:361-374`. The registry and HTTP timestamps are
source facts; acquisition time is a separate fact. There is no global
`upstream_publication_date`, because observed canonical files had different months
and registry dates range across years
(`docs/research/clinpgx-sources.md:54-64`).

`dataset.source` in the outer fleet manifest refers to this aggregate source set:

- `identifier`: `clinpgx-<profile>-source-set-v1`;
- `url`: the reviewed canonical ClinPGx download registry URL;
- `retrieved_at`: completion time of the acquisition used by this candidate;
- `sha256`: SHA-256 of the exact canonical `source-manifest.json` bytes;
- optional `etag`/`last_modified`: only when meaningful for the registry response.

Consumers must not interpret aggregate `retrieved_at` as the publication date of
every source.

### Reproducibility boundary and acquisition observations

`retrieved_at` is real evidence and must not be replaced with a build epoch or an
upstream `Last-Modified` value. Separate acquisition into two records:

1. An **acquisition receipt** contains the exact source bytes/digests, registry and
   HTTP metadata, and the wall-clock retrieval times for one acquisition. When a
   new stable release key is accepted, that receipt is frozen into the immutable
   `source-manifest.json`; its aggregate completion time becomes the outer fleet
   manifest's `dataset.source.retrieved_at`.
2. A **subsequent observation** records that the registry/source was checked later
   and yielded the same stable source bytes. It is CI/operator evidence only, is not
   copied into the release bundle or published validation report, and cannot alter
   the existing release.

The reproducible build inputs are therefore the exact retained source bytes, the
exact frozen acquisition receipt/source manifest, the transformation contract,
rights evidence, schema/toolchain pins and `SOURCE_DATE_EPOCH`. Given those inputs,
packaging must reproduce identical SQLite, tar/zstd, manifest and published
validation-report bytes. Merely downloading equal bytes at another time is a new
observation, not the same complete build input.

The candidate flow computes the stable release key from bytes and nonvolatile
contracts before packing. If that key already exists, it verifies the existing
sealed release and records `unchanged`; it must not regenerate a same-tag bundle
with new retrieval timestamps. A sealed retry consumes the original handoff and
does no network acquisition. A disaster-recovery rebuild downloads the existing
release's frozen source manifest and exact content-addressed source bytes first; it
does not synthesize replacement provenance.

`validation-report.json` is part of the immutable release and contains no workflow
run ID, current clock, or later observation time. Volatile validation execution
facts belong in the separately retained CI log/observation artifact. This makes
published exact-asset idempotency compatible with reproducibility.

### Stable release key and immutable tag

Compute a canonical `release-key.json` in memory from only stable inputs:

- profile name;
- ordered pairs of logical source name and source content SHA-256;
- digest of the exact transformation code/config/schema contract;
- SQLite/bundle schema semantic version;
- digest of canonical `licenses.json` rights evidence.

Its SHA-256 is `source_set_identity`, and the immutable tag is:

```text
data-clinpgx-<profile>-<first-16-lowercase-hex-of-release-key-sha256>
```

Exclude acquisition timestamps, workflow run IDs and build paths from the release
key. The frozen manifest still records the exact transformation Git revision and
first accepted retrieval times for audit. Before producing a publishable handoff,
compare the stable key with the last published release for the profile. If it is
unchanged, verify the sealed release, record the later observation separately, and
do not pack or mint another release. An exact retry uses the sealed handoff from the
original build. If the same tag is ever paired with different manifest or asset
bytes, treat it as a collision and fail; never normalize away the difference or
overwrite the release.

Including the transformation contract and rights digest avoids the ClinGen pattern
of deriving a tag only from upstream content
(`../clingen-link/clingen_link/etl/release_identity.py:76-165`), which would collide
when parser/schema or reviewed rights change over identical source bytes.

### `licenses.json` and publication boundary

Use an exact-shape collection, not one global guessed label. It contains:

- `schema_version` and a sorted `licenses[]` keyed by `license_id`;
- name, SPDX expression when valid, canonical HTTPS license/terms URLs;
- exact notice text or an exact notice SHA-256 plus immutable source reference;
- attribution, citation, change-indication and ShareAlike obligations;
- distribution modes independently reviewed (`public`, `controlled`,
  `operator_local`) with `allowed`, review date, named reviewer and rationale;
- affected artifact logical names;
- `needs_confirmation` for related/legacy material that has not been reviewed.

The design must preserve these known boundaries rather than collapsing them:

- ClinPGx/PharmGKB exports are described as CC BY-SA 4.0 and carry attribution,
  link, change-indication and ShareAlike duties plus research/no-resale policy
  wording.
- CPIC curated content is described as CC0 1.0 but requests attribution, version,
  access date and citation.
- “From Related Projects” files are not covered by the ClinPGx agreement and must
  inherit their authors' terms.

See `docs/research/clinpgx-sources.md:66-75`. This is technical provenance design,
not legal advice.

The outer fleet `license` block records the conservative aggregate decision for the
specific publication mode. It may set `redistribution_allowed=true` only when every
included artifact is affirmatively approved for that mode and the bundle has one
coherent governing publication decision. If no coherent aggregate license can be
stated, split the profile by rights boundary. Unknown is false, not true. The
workflow validates the committed evidence and the candidate's exact licenses
digest; an environment approval is not a substitute for complete evidence.

The safe initial state is:

- scheduled/manual candidate builds and private validation: enabled;
- public GitHub data publication: disabled/fails `--public`;
- controlled distribution: enabled only if its own review says so;
- operator-local acquisition/build: available under upstream terms.

Current ClinGen's `RIGHTS.json` validator is a good exact-shape, bounded, no-follow
reading pattern (`../clingen-link/clingen_link/etl/rights_notice.py:20-134`), but
its single-license model must become a strict collection for ClinPGx.

## Profiles and source coverage

### `core` (production default)

Freeze the following 15 artifacts as the initial *candidate* core inventory because
they are the exact set used in the measured local-index experiment:

`chemicals.zip`, `clinicalVariants.zip`, `drugLabels.zip`, `drugs.zip`,
`genes.zip`, `guidelineAnnotations.json.zip`, `occurrences.zip`,
`pathways-biopax.zip`, `pathways-tsv.zip`, `pathways.json.zip`, `phenotypes.zip`,
`relationships.zip`, `summaryAnnotations.zip`, `variantAnnotations.zip`, and
`variants.zip`.

The experiment indexed 444,508 tabular rows/complete JSON documents and produced a
roughly 331 MiB SQLite database; that is evidence of practical local indexing, not
an entity count or a completeness proof. `pathways-biopax.zip` was not parsed and
must be `metadata_only` until its parser and invariants exist. The other artifacts'
actual parsed-member coverage must be frozen in profile configuration, not inferred
from archive presence. Exact observations are in
`docs/research/download-index-results.json:1-170`.

Core validation must include the real 148,743-character chemicals/drugs field as a
regression; Python's default 131,072-character CSV limit failed on this data. It
must also preserve and warn about the observed `Is VIP=Yes` export anomaly rather
than converting every gene to a normalized VIP assertion. These are release
validation cases, not reasons to skip rows.

### `extended` (separate optional release)

Start with no production promise. Admit formats only after each has a bounded parser,
member/path/size validation, schema drift tests, semantic sentinels and affirmative
rights mapping. `clinpgxHaplotypes.zip` currently has two usable TSVs for star/named
allele definitions, and `pharmcat.zip` has structured allele-function/phenotype JSON,
but each still needs an exact rights and coverage decision. The current
`haplotypes.zip` contains 49 inner XLSX files that are structurally corrupt
(`BadZipFile`, with UTF-8 replacement bytes), so it is quarantined rather than an
extended input. `cpic.drug.mapping.zip` is another candidate only after equivalent
validation. The extended bundle gets its own tag, manifest, artifact identity,
compatibility range and rollback chain. It is never an extra asset hidden under a
core release tag.

### `catalog-only`

Keep the rest of the registry discoverable as metadata, including legacy,
experimental and related-project items. Catalog visibility is not redistribution
approval and not local query coverage. An operator may explicitly select an item for
a local build only when the rights and parser state permit it. A quarantined source
never silently becomes a partial successful core release.

The production application pins exactly one authoritative profile (`core`) in
`container-release.json`. An optional profile becomes deployable only through an
explicit application/config update with a reviewed exact identity; the fleet's
current `container-release.json` data block represents one authoritative dataset,
not an arbitrary set of add-ons.

## CLI contract

Expose one Typer `data` command group. Commands emit bounded JSON on stdout, human
diagnostics on stderr, stable exit codes, and never publish as a side effect of
build/install:

```text
clinpgx-link data catalog [--profile core|extended] [--refresh]
clinpgx-link data build --profile core|extended --out DIR [--source-cache DIR]
clinpgx-link data refresh --profile core|extended --out DIR [--source-cache DIR]
clinpgx-link data validate --manifest PATH --manifest-sha256 HEX --artifact PATH --report PATH --public|--private
clinpgx-link data pack --profile core|extended --database PATH --out DIR --previous-known-good sha256:HEX
clinpgx-link data pull --profile core|extended --release-tag EXACT --manifest-sha256 HEX --out DIR [--predecessor-tag EXACT --predecessor-manifest-sha256 HEX]
clinpgx-link data install --manifest PATH --manifest-sha256 HEX --artifact PATH --data-root DIR [--predecessor-manifest PATH --predecessor-manifest-sha256 HEX --predecessor-artifact PATH]
clinpgx-link data status --data-root DIR
clinpgx-link data rollback --digest sha256:HEX --data-root DIR
```

`build` performs acquisition, import and semantic validation and produces the
database/source metadata in a staging directory. `pack` creates deterministic assets
and is also callable by `build` for the normal path. `validate` first verifies the
out-of-band manifest SHA-256, validates both strict schemas and license policy, then
verifies artifact and report bytes. `--public` invokes the fleet publication gate;
`--private` does not turn a negative public decision into approval.

`refresh` is poll-and-build-candidate orchestration only. It never installs,
activates, restarts an application, or publishes. Local build, validation,
installation and rollback require no publication approval; only the chosen remote
distribution mode is subject to its rights publication gate.

`pull` requires an exact immutable tag and independently supplied expected manifest
digest. It does not accept `latest` in production. A convenience resolution of the
newest tag may exist only for interactive local development and must print the
resolved exact identity before acting. Predecessor tag and manifest digest are both
present or both absent; they are required when the direct predecessor is not already
retained. The workflow performs publication; do not put GitHub-token publication
logic in the ordinary application CLI.

The router's existing equivalent commands are
`validate-data-manifest`, `materialize-data`, and `rollback-data`
(`../genefoundry-router/genefoundry_router/release/cli.py:187-245`). MaveDB's
bootstrap/pull/pack UX can inform local ergonomics
(`../mavedb-link/mavedb_link/ingest/cli.py:124-205`), but its live-fallback and
looser publication behavior are not the production release contract.

## Verification, installation and rollback

Use the current router materializer as the behavioral baseline:
`../genefoundry-router/genefoundry_router/release/data_materialization.py`.
Do not add `genefoundry-router` as a runtime dependency. Either vendor a small,
reviewed implementation with parity tests or implement the same contract locally
behind ClinPGx types.

### Pull and artifact verification

- Require HTTPS with exact hostname allowlists for every hop; reject userinfo,
  non-443 ports, wildcards and unbounded redirects.
- Stream through a private `0600` temporary regular file with connect/stall/overall
  limits and a minimum-throughput policy. Enforce `Content-Length` when present and
  an independent byte ceiling while streaming.
- Verify the independently trusted manifest digest before trusting any URL, size or
  artifact digest in it.
- Require a regular, single-link, no-follow artifact; match exact compressed size and
  SHA-256 before decompression.
- Parse tar/zstd as a bounded stream. Reject absolute/parent/control-character paths,
  backslashes, duplicate names, symlinks, hardlinks, special files, set-id bits,
  too many members and expanded bytes above the reviewed ceiling.
- Match the exact four-member inventory, each file digest/mode, total expanded size,
  member count and `expanded_tree_sha256`.

These mechanics exist at router lines 31-168 (download/compressed verification),
171-321 (safe extraction) and 324-349 (atomic selection). Router tests cover digest,
oversize, redirect, stall, link, traversal, duplicate and expansion-bomb failures at
`../genefoundry-router/tests/release/test_data_release.py:195-548`.

### Atomic installation

Under a private, non-group-writable data root:

```text
DATA_ROOT/
  .materialize.lock
  versions/
    <artifact-sha256>/
      clinpgx.sqlite
      schema.json
      source-manifest.json
      licenses.json
      materialization.json
      data-identity-manifest.json
  current -> versions/<artifact-sha256>
```

Hold an exclusive file lock across staging, verification and selection. Materialize
into a private scratch directory, fsync each file and directory, set final files
read-only, probe SQLite with immutable/read-only mode, compute the expanded tree and
runtime identity again, then rename the completed directory into `versions/` and
atomically replace `current` with a relative symlink. Any failure removes only owned
scratch state and leaves `current` unchanged. Concurrent identical installs converge
on the same verified version; an existing version is reverified, never trusted by
name alone.

ClinGen provides a compact single-file example with locking, bounded copying and
decompression, schema verification, immutable version directories and atomic link
selection (`../clingen-link/clingen_link/store/db.py:205-337`). The generic router
adds archive member checks and retained-previous-good enforcement and should define
the final behavior (`data_materialization.py:440-563`).

`data-identity-manifest.json` uses fleet `runtime-v1`: canonical identity of every
authoritative expanded file, the exact `materialization.json` sidecar, and release
tag. The identity manifest itself is excluded to avoid a cycle. Its digest is the value pinned as
`container-release.json.data.digest` and compared by `/health`. It is **not** the
compressed artifact SHA-256 and not the expanded-tree SHA-256. Keep those three
identities visibly distinct in config and diagnostics. ClinGen documents and tests
this distinction in `../clingen-link/AGENTS.md` under “Data identity” and implements
the helper in `../clingen-link/clingen_link/runtime_data_identity.py`.

### Previous-known-good and rollback

For the first installation into an empty root only, the router permits
`previous_known_good_digest` to self-reference the candidate. Every subsequent
manifest must name the exact compressed digest of an already retained and verified
prior release. The router checks only that direct predecessor, but its combined
materialize/select command assumes the predecessor is already present
(`data_materialization.py:491-548`). A new ClinPGx installer must make that
precondition operable on clean machines and skipped upgrades without weakening it.

The repository carries a strict `data-release.lock.json` deployment recipe with the
candidate profile/tag/manifest SHA-256 and its direct predecessor tag/manifest
SHA-256/artifact SHA-256. Pre-deployment preparation downloads those exact immutable
releases and creates a canonical `seed-index.json`. The independently configured
seed-index digest is the trust root; each entry then binds the exact manifest digest,
artifact filename/digest/size and release tag. The offline `/seed` inventory is:

```text
seed-index.json
candidate/data-release-manifest.json
candidate/clinpgx-core.tar.zst
predecessor/data-release-manifest.json
predecessor/clinpgx-core.tar.zst
```

The predecessor directory is omitted only when its digest is already retained and
reverified, or for the initial empty-root self-reference bootstrap. Extra seed files,
wrong profile/tag, and absent or mismatched manifest/artifact identities fail closed.
The networked preparation step and offline init both verify the seed-index digest;
the init container still has `network_mode: none`.

Internally, installation separates **stage** from **activate**:

1. Verify the predecessor's independently pinned manifest, artifact, safe expanded
   tree, schema and runtime identity, then materialize it as a retained generation
   without selecting it. Staging deliberately does not require or stage the
   predecessor's own predecessor, because no release-history recursion is needed.
2. Verify and stage the candidate identically.
3. Run the router-compatible activation check: the candidate manifest's
   `previous_known_good_digest` must equal the staged/retained predecessor artifact
   digest; both generations are reverified and compatible with the deploying
   application; only then atomically select the candidate.

`data install` performs that bounded sequence under one exclusive lock. On a clean
machine installing release B, it stages A then activates B. Skipping from installed
A to C stages C's direct predecessor B then activates C; it does not walk back from
B to A. A failure at any point leaves the original `current` selection unchanged.
This is a local extension around the router behavior, not a relaxation of the actual
fleet activation validator.

Retain current plus previous-known-good at minimum; pruning is a separate
post-activation operation that never removes either. Release validation must choose
a predecessor usable by the advertised application/schema compatibility. Merely
staging an incompatible historical artifact does not make rollback valid.

`rollback --digest` selects only a retained generation after rechecking its runtime
identity, expanded tree and compatible schema, then atomically swaps `current`. It
never downloads during rollback. If a pruned historical release is needed, the
operator separately pulls and installs its exact immutable tag before requesting
selection, supplying its direct predecessor if it is not retained. Router
enforcement and tests are at
`data_materialization.py:479-563` and
`tests/release/test_data_release.py:535-646`.

## Deployment and data/application separation

Adopt the `clingen-link` external snapshot topology, adjusted for a multi-member
ClinPGx bundle:

- `container-release.json.data.mode = "external-reference"`;
- `definitions.contract = "data-bound"`;
- `data_identity_contract = "runtime-v1"`;
- smoke profile `immutable-bundle`;
- exact `data.release_tag`, runtime identity `data.digest`, and schema compatibility;
- an init sidecar declared with no egress, `/seed` read-only and `/data` writable;
- application starts only after init completes successfully and mounts `/data`
  read-only;
- image scan permits no authoritative ClinPGx database/source bundle in image
  layers;
- production has no direct published backend port and uses the hardened fleet
  Compose contract.

The concrete reference is `../clingen-link/container-release.json:1-56` and
`../clingen-link/docker/docker-compose.yml:7-144`. Current router consistency rules
require exact data identity for data-bound services
(`../genefoundry-router/genefoundry_router/release/models.py:233-358`).

### Mode, activation owner, health and restart contract

There are exactly two explicit runtime modes:

- `api-only-development`: no authoritative local data is expected. `/health` may
  return 200 when the process and bounded API client are ready, but reports
  `authoritative_data.mode=api-only-development`; this mode is not accepted by the
  production fleet release gate.
- `data-bound-production`: the deployment config pins one exact release tag and
  `runtime-v1` digest. Missing, unreadable, schema-incompatible, or mismatched core
  data prevents readiness and cannot degrade silently to live API answers.

The fleet controller is the sole production activation owner. Data acquisition,
`build`, `refresh`, `pack`, and `pull` only produce or stage candidates. The
controller prepares a replacement physical data volume, runs the offline installer,
starts a replacement application container with the candidate's expected tag and
runtime digest, passes smoke/readiness probes, and only then switches deployment
traffic. It never changes the `current` link underneath a serving production
process. An operator invoking `data install` or `rollback` in a standalone local
environment owns that local activation; this does not grant publication authority.

The application resolves `/data/current` once at startup, verifies the complete
runtime identity, and opens an immutable read-only repository handle bound to that
generation. Every query and cursor uses that handle's identity; it does not follow
the symlink again mid-request. Data upgrades are restart/replacement operations, not
hot reloads. Rollback likewise selects a verified retained generation on a
replacement volume and starts an application configured for that exact prior tag,
digest, schema and compatible application version.

The fleet health endpoint `/health` is readiness, not mere PID liveness. It returns
200 in production only when expected and actual runtime identity match and the bound
SQLite semantic probe succeeds; it returns 503 with a bounded mismatch/unavailable
reason otherwise. A startup mismatch is fatal before the listener opens, allowing
the declared restart policy to retry. If verified identity drifts after startup,
health becomes 503 and tools return the canonical unavailable error; Docker does not
restart a container merely because it is unhealthy, so the controller must alert
and replace it. The separate liveness concern may use an internal process probe but
must not weaken fleet `/health`.

The live API cache is configured separately, for example:

```json
{
  "runtime_cache": {
    "path": "/cache/clinpgx-api",
    "eviction": "TTL and bounded LRU; safe to delete",
    "deletable_without_authoritative_data_loss": true
  }
}
```

It lives on a separate writable volume. Cached API objects retain their own retrieval
time/TTL/provenance and never change the local snapshot's release identity. A
zero-bootstrap API-only development mode may operate without local data only when
configured explicitly. Missing optional extended data is reported as unavailable;
it does not falsify a correctly pinned core health result.

## Candidate and publication workflows

### Scheduling and authority

Create `.github/workflows/data-refresh.yml` with two authority levels:

1. A no-write candidate job runs manually and on a daily schedule during the
   monthly publication window, recommended cron `17 8 6-12 * *`. ClinPGx states
   files are generated on the sixth, but artifacts can have different dates, so
   daily polling for one week is safer than assuming one atomic monthly cut. The
   stable release-key check makes unchanged runs cheap no-ops. The job has only
   `contents: read`, builds in `RUNNER_TEMP`, and uploads the sealed candidate and
   validation report with a finite retention period.
2. Publication initially runs only for `workflow_dispatch` with `publish=true`,
   protected `main`, a protected `data-release` environment and explicit rights
   approval. Only this job gets `contents: write`, `id-token: write` and
   `attestations: write`. Scheduled builds never publish automatically until a
   separate policy change is reviewed.

No workflow waits an hour for source stability. Instead, conditional registry/object
identities are recorded, downloads are content-hashed, and a later daily run builds a
new candidate only when the stable source set changes. A source that changes during
one acquisition fails the candidate; it is retried on the next run.

### Immutable publication state machine

Port the behavior, not the large inline YAML, from
`../clingen-link/.github/workflows/data-refresh.yml:149-355` into tested project
Python helpers:

- no release/tag: create the exact tag at the validated workflow commit, then an
  exact draft;
- matching draft with exact four assets and identity: revalidate and promote it;
- matching published release with exact bytes and attestations: successful no-op;
- any mismatched target commit, duplicate release, extra/missing asset, byte/digest
  difference, unsafe asset ID, missing attestation or ambiguous state: collision and
  hard failure.

Search paginated releases because tag lookup may omit drafts. Never delete, replace,
retag or overwrite. Create the Git tag at the exact build commit and reject a tag
that already points elsewhere. After upload, re-fetch every asset by numeric release
asset ID; verify server-reported size/digest, local digest, byte equality,
`SHA256SUMS`, workflow/source attestation and exact draft identity immediately before
the single promotion operation.

Bind build -> rights approval -> publish with GitHub artifact ID/digest, run
ID/attempt, source commit, handoff SHA-256, release tag, artifact SHA-256, source-set
identity and licenses digest. This prevents a later job from approving a same-named
but different handoff. ClinGen's workflow tests exercise the important state
properties at `../clingen-link/tests/unit/test_data_refresh_workflow.py:36-185` and
`317-474`, and its typed release-state helper is
`../clingen-link/clingen_link/etl/release_identity.py:168-210`.

Avoid a 300-line inline shell/Python publisher in the new workflow. Put bounded JSON
parsing, release discovery and state comparison in modules under
`clinpgx_link/data_release/`; YAML should only declare authority, pins, job handoff
and invoke tested commands.

### Pinned workflow actions

The current local fleet pins these exact action revisions; reuse after reconfirming
them during implementation:

- `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1` (`v7.0.1`)
- `actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97` (`v7.0.0`)
- `astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d` (`v10.0.1`), with locked uv version
- `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` (`v7.0.1`)
- `actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` (`v8.0.1`)
- `actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8` (`v4.2.2`)

The sources are current router reusable workflows and the ClinGen data workflow;
for example `../genefoundry-router/.github/workflows/_container-release.yml:67-87`
and `597-689`, and `../clingen-link/.github/workflows/data-refresh.yml:32-36` and
`293-299`. Guard every `uses:` with a full-SHA static test.

## Required tests and gates

### Contract and deterministic-build tests

- vendored fleet schema digest and byte parity with router at its recorded commit;
- generated manifest validates against both the vendored JSON Schema and strict
  local model; unknown fields and mutable tags fail;
- project `source-manifest` and `licenses` schemas reject missing, extra, duplicate,
  mistyped and noncanonical fields;
- two builds with identical source bytes, frozen acquisition receipt, transform,
  rights, schema/toolchain pins and epoch produce identical SQLite, tree, tar/zstd,
  outer manifest, release-key and published validation-report bytes;
- acquiring identical source bytes at two different times produces the same stable
  release key; the later observation takes the verified `unchanged` path before
  packing and cannot collide with or mutate the sealed release;
- an exact sealed retry performs no acquisition and republishes only byte-identical
  handoff assets; changing only parser/configuration, coverage policy or rights
  evidence changes the release key;
- release-key changes for source bytes, transformation contract, schema, profile or
  rights evidence, but not for run ID/path/acquisition time;
- `core` has exactly the frozen 15-source inventory, with explicit consumed members
  and `pathways-biopax.zip` metadata-only until implemented;
- different per-source creation/registry dates survive round-trip and no global
  source date is synthesized;
- 148,743-character field round-trips without truncation/skip;
- `Is VIP` inconsistency remains raw plus typed warning, never normalized to false
  universal VIP status;
- missing required rows/members, skipped rows, parser failure, changed required
  columns or quarantined required core artifact fails the whole candidate;
- additive unknown columns are preserved and surfaced as reviewable drift;
- all records point to a valid artifact/member/row-or-document provenance and
  license ID;
- every admitted source archive and member can be reconstructed byte-for-byte from
  installed SQLite BLOBs after deleting acquisition/build caches; bounded chunk
  continuation has no gaps/duplicates and remains bound to release/member digests;
- replacing the upstream logical URL after installation cannot change historical
  source-asset reads, while explicit live reads report a different representation;
- public validation fails if any included source is false/unknown for public
  distribution or the committed rights digest differs.

### Acquisition and materialization tests

Port the router cases in
`../genefoundry-router/tests/release/test_data_release.py:195-678`:

- exact-host HTTPS redirects succeed; foreign host, userinfo, port, wildcard,
  redirect loop/limit, timeout, stalled/slow and oversize streams fail;
- preexisting destination survives failed download; partial files are removed;
- compressed size/digest mismatch and symlink/hardlink input fail;
- traversal, absolute/backslash/control paths, duplicate members, links, devices,
  set-id modes, member bomb and expansion bomb fail;
- wrong member inventory, file mode/digest/tree identity and incompatible schema
  fail;
- immutable SQLite semantic probe validates schema, required tables/counts and known
  core sentinels;
- interrupted extraction/rename preserves selected generation; concurrent install is
  serialized; an existing generation is reverified;
- data root and identity sidecars are private; tampered retained trees fail;
- missing previous-known-good prevents activation; bootstrap exception is allowed
  only for an empty root; rollback selects a retained verified compatible version;
- fresh install of a noninitial release stages exactly its direct predecessor and
  activates the candidate without recursively requiring older history;
- skipped upgrade A -> C stages C's direct predecessor B, preserves A until success,
  and selects C only after the router-compatible predecessor check;
- wrong/missing seed-index trust digest, missing predecessor, predecessor artifact
  mismatch and schema-incompatible rollback fail without changing `current`.

### Workflow and deployment tests

- default/scheduled jobs have no write authority; publication is manual, protected
  main plus environment only;
- all actions and reusable workflows use reviewed full commit pins;
- build, rights validation and publish are separate jobs with exact artifact
  ID/digest/source-commit handoff;
- release-state table tests absent, matching draft, matching published, mismatched,
  duplicates and pagination exhaustion;
- exact four-asset inventory, checksum parsing, capped manifest/report reads,
  numeric safe asset IDs, target commit, byte re-fetch and attestation binding;
- public workflow cannot run while rights state is false/unknown;
- code-only image scan finds no SQLite or authoritative source bundle;
- Compose init has no egress, seed is read-only, data is init-writable/app-read-only,
  app waits for successful init, and API cache is separate;
- seed preparation pins candidate plus direct predecessor manifest digests and the
  offline seed-index digest; init rejects any extra or mismatched seed inventory;
- `container-release.json` passes the pinned current router gates with
  `external-reference`, `data-bound`, `runtime-v1`, immutable-bundle smoke and the
  deployed overlay/seed declarations;
- build/refresh/pull never change `current`; only the fleet controller's replacement
  deployment invokes installation/rollback in production;
- startup with missing/mismatched expected identity exits before listening;
  post-start drift yields `/health` 503 and canonical unavailable tool responses;
- upgrade and rollback start replacement processes pinned to the selected identity;
  a serving process never follows an in-place `current` change;
- repository reads, counts, provenance and cursors remain bound to one immutable
  handle while activation/rollback is deliberately interleaved;
- end-to-end test: build -> validate -> install -> serve representative MCP core
  query -> install new generation -> query -> rollback -> obtain the original answer
  and provenance; health expected/actual runtime identities match at each step.

Failed candidate validation should retain the bounded `validation-report.json` and
non-sensitive logs as CI evidence for a documented retention period, but never
publish an invalid database or upstream raw data merely for debugging.

## Recommended repository layout

Keep modules cohesive and below the fleet 600-line cap:

```text
data/
  rights/licenses.json             # reviewed, versioned rights source
  profiles/core.json               # exact source/member/coverage inventory
  profiles/extended.json
  data-release.lock.json           # deployed candidate + direct predecessor pins
vendor/genefoundry/
  CONTRACT_SHA256
  data-release-manifest.schema.json
clinpgx_link/data_release/
  models.py                        # strict source/licenses/report models
  acquire.py                       # registry and bounded official downloads
  build.py                         # source adapters -> deterministic SQLite
  validate.py                      # structural/semantic/profile gates
  pack.py                          # deterministic tar/zstd and manifests
  materialize.py                   # verify/stage/select/rollback
  identity.py                      # release key, runtime identity, checksums
  publication.py                   # pure closed-state comparison/helpers
.github/workflows/
  data-refresh.yml                 # candidate + protected publish orchestration
tests/unit/data_release/
tests/integration/data_release/
```

The application query/store layer reads `/data/current/clinpgx.sqlite` and does not
know how to fetch/build releases. Acquisition and publication dependencies belong in
development/release groups, not the production request path, except for the minimal
offline installer invoked by the init container.

## Decisions for the main specification and plan

The main specification should make these choices explicit:

1. Use the current router v0.8.7 schema as an unchanged outer contract, with a
   strict ClinPGx `source-manifest.json` and `licenses.json` inside the bundle.
2. Default to private/operator-local candidates; public GitHub publication remains
   disabled until named human review approves every core source and the actual
   distribution mode. Never infer `redistribution_allowed=true`.
3. Define initial `core` as the exact 15 measured exports, but state actual parser
   coverage per member; keep BioPAX metadata-only. Define `extended` as an independent
   optional release and everything else as catalog-only until admitted.
4. Publish four exact assets and use content-/contract-addressed immutable tags.
   No `latest`, mutable assets or overwrite path.
5. Pin production to one exact core runtime identity. Keep API cache and optional
   profiles outside that identity.
6. Schedule candidate polling during days 6-12 monthly, with manual protected
   publication initially. A stable source-set no-op avoids duplicate releases.
7. Require independently trusted manifest digest, safe bounded extraction, atomic
   activation, retained previous-known-good and offline rollback before deployment.
8. Keep data releases independent of application SemVer. After a data release is
   actually available, update `container-release.json`, Compose defaults and smoke
   digests together in an application PR; never predeclare unbuilt release facts.

The only policy decision that cannot be made technically is where ClinPGx data may
be redistributed. The implementation should not block on that decision: it can
fully build, validate and install operator-local/private artifacts while the public
publication gate remains closed.
