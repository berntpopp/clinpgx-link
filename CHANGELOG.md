# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
