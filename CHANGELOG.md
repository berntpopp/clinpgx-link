# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-06

- Initial public release of ClinPGx Link: research-only MCP server for public ClinPGx evidence.
- Stateless HTTP MCP server providing tools for genes, drugs, guidelines, variants, and attachments.
- Retained content store and SQLite dataset snapshots with provenance and digest tracking.
- Production multi-stage Docker image and Compose deployment overlays.
- Hardened container runtime with private cache permissions, startup exception masking, and immutable release gates.
