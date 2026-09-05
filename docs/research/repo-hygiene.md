# Repository hygiene implementation

The baseline follows the checked-out `../clingen-link` editor configuration,
pre-commit categories and `.github/workflows/ci.yml` structure. Siblings were read
only. This is a partial hygiene implementation, not a release/conformance gate.

- Ruff, strict mypy and the package size check use this repository's frozen uv
  environment in both hooks and CI. Hooks check rather than silently rewrite files.
- The dependency hook checks lock freshness. The staged diff check rejects whitespace
  errors and conflict markers introduced in staged changes.
- Git normalizes application text to LF but does not normalize captured fixtures,
  vendored API JSON or research JSON/TSV. Exact source bytes remain evidence.
- CI uses read-only repository permissions, cancellation of superseded runs, a
  bounded job timeout, credential-free checkout, and immutable action references.
  The three action commits copied from the fleet baseline were verified through
  their owning GitHub repositories on 2026-09-05. uv is pinned to the locally used
  `0.11.28`; Python 3.12 is the supported floor tested by this initial job.

Local checks: `uv run pre-commit validate-config`, a hook run over the runtime
integration files, and `git check-attr text eol` for application code and a sourced
fixture. Whole-tree checks and remote workflow execution are separate acceptance
steps; adding a workflow does not prove GitHub CI has run.

Outstanding: release/container/conformance workflows, coverage gate, broader
interpreter matrix tied to the eventual container, and relocation of tracked
implementation reports out of the ignored `.superpowers` scratch tree. Those
reports have not been deleted or silently untracked.
