# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.7] - 2026-11-12

### Fixed

- Silent false negative: a ClinPGx `PA` accession passed to a name-mapped filter (e.g. `summary_annotation` `chemical=PA449053` -> `relatedChemicals.name`) returned `success: true` with zero rows. Accessions now route to the captured accession field (`relatedChemicals.accessionId`, `relatedGenes.accessionId`, `accessionId`) where one exists, and are otherwise rejected with `accession_in_name_filter`; names sent to accession-only fields are rejected with `accession_required` (#1, #3).
- `summary_annotation` rows were all `deferred_content` in every non-`full` mode because the profile typed `allelePhenotypes` as an object; live rows carry a list. Compact rows now also carry the annotation `name` and its `PA` `accessionId` (#1, #3).
- `get_related_records.result_type` no longer advertises `literature_annotation`, `vip` and `vip_variant`: the live pair report rejects them for every pair even though the captured OpenAPI enum lists them (#1, #3).
- `dataset_unavailable` (no local snapshot) is no longer `retryable: true`, explains the live alternatives, and an `auto` search that falls through to the missing snapshot names the entity's API filters (#1, #3).
- `get_record(entity_type="haplotype", source="website")` returns the star-allele definition (symbol, function, function source, defining rsIDs) instead of a deferred envelope; website detail routes reject non-`PA` record ids up front (#1, #3).

### Added

- `search_records` `chemical` accepts `name` as an alias; `guideline_annotation` accepts `gene`/`chemical` PA accessions (returns all four CYP2C19/clopidogrel guidelines where the pair report currently returns two); `summary_annotation` accepts `level_of_evidence` (`1A`..`4`, upstream `levelOfEvidence.term`) (#1, #2, #3).
- A `guideline_annotation` pair result now leads its `next_commands` with the complete accession search, because the live pair report currently omits CPIC/DPWG for CYP2C19/clopidogrel (#1).
- Self-describing contract: per-entity filter keys in the `filters` schema, the relationship mode table in `result_type`, `response_mode` deferral semantics, `accession_filter_mapping` / `response_mode_contract` / `gene_chemical_guideline_workflow` in capabilities, and search-friendly descriptions for `get_server_capabilities` and `get_source_content`. Unprofiled rows that `full` would inline carry `recovery_action: retry_with_response_mode_full` (#3).

## [0.1.6] - 2026-11-11

### Security

- fastmcp / fastmcp-slim 4.1.8 (floor raised to >=4.1.2), Authlib 1.9.5 (critical/high), dnspython 2.9.2 (high), joserfc 1.8.0, opentelemetry-api 1.45.2, filelock 4.2.10, virtualenv 21.15.6.

### Changed

- GitHub Actions pins (setup-python, setup-uv, codeql-action) and python:3.14-slim base digest refreshed.

## [0.1.5] - 2026-10-20

### Security

- certifi 2026.10.19: drops emSign root CAs whose intermediate CA keys were compromised (critical).

### Changed

- Unify on Python 3.14 (requires-python, tooling, CI single-version matrix, router reusable workflows v0.10.5); cancel superseded PR runs; Dependabot no longer proposes Python minor jumps.

## [0.1.4] - 2026-10-20

- Security: update mcp to 2.3.0 (resolves the open Dependabot alert for mcp, fixed in 2.2.0) and raise the mcp floor to 2.2.0.
- Changed: consolidate Dependabot PR #11 (mcp 2.1.1 to 2.2.0) into this release.
- Changed: data-release `application_compatibility` is evaluated patch-tolerantly — an application version is admitted when it is `>= minimum` and its `major.minor` does not exceed the maximum's, so 0.1.4 serves the existing `data-clinpgx-core-cfde21bfec473b35` bundle (maximum 0.1.3) without a new data release. Patch releases must never change the data contract; a data-contract change requires a minor bump. Database schema checks remain exact.
- Added: generated `CITATION.cff`.

## [0.1.3] - 2026-10-04

- Bind the production NPM service and offline initializer to the approved immutable ClinPGx snapshot.
- Verify the runtime-v1 identity and exact release manifest/artifact hashes before production startup.
- Initialize the private data volume from the same-image offline installer before the read-only app starts.

## [0.1.2] - 2026-10-03

- Restore production fail-closed runtime mode in the deployed NPM Compose overlay.

## [0.1.1] - 2026-10-03

- Update PyJWT to 2.15.0 and virtualenv to 21.7.13.
- Add CodeQL and dependency-review workflows after security scanning was enabled.
- Refresh the pinned Python 3.14 base image and router v0.9.3 reusable container workflows.


## [0.1.0] - 2026-09-06

- Initial public release of ClinPGx Link: research-only MCP server for public ClinPGx evidence.
- Stateless HTTP MCP server providing tools for genes, drugs, guidelines, variants, and attachments.
- Retained content store and SQLite dataset snapshots with provenance and digest tracking.
- Production multi-stage Docker image and Compose deployment overlays.
- Hardened container runtime with private cache permissions, startup exception masking, and immutable release gates.
