# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
