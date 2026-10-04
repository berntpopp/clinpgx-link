# Complete the existing production data contract

This implements the already binding ClinPGx design and the production lifecycle
in `docs/research/data-release-design.md`. It does not authorize distribution of
the private candidate or activate a production service.

## Interfaces and ownership

Runtime work owns settings, startup, health, MCP admission, and a read-only data
probe. Operator work owns the offline installation CLI and deployment templates.
The integration owner reviews their combined behavior and runs `make ci-local`.
Shared settings are `expected_snapshot`, `expected_release_tag`, and
`expected_runtime_digest`; all three are required in production.

## Runtime task

1. Write failing tests for incomplete production pins, wrong runtime identity,
   startup without a complete generation, and generation drift after startup.
2. Resolve the installed generation once, verify its native runtime identity,
   then open its SQLite repository read-only. Fail before serving on mismatch.
3. Retain the admitted generation and bounded filesystem fingerprints. Reject
   later drift in readiness and source tool calls without rehashing gigabytes
   on each request or following a newly activated current link.
4. Report fleet-compatible expected/actual release identity. Add a read-only
   probe using the controller's existing exact output contract.
5. Preserve development behavior and safe canonical errors. Confirm focused
   tests pass and production Python modules stay within the 600-line budget.

## Operator and deployment task

1. Write failing CLI tests for independently pinned manifest validation,
   incompatible application/schema, idempotent installation, and safe errors.
2. Expose the existing installer through an offline operator-only command;
   support independently pinned previous input for replacement. Never expose
   installation as an MCP tool or bypass native bundle/license verification.
3. Prepare explicit production data pins and a named-volume deployment template:
   application read-only, initializer writable, both using the exact same image.
   Apply non-root identity and the existing hardening/resource limits.
4. Test configuration rendering with synthetic pins. Keep release metadata
   truthful: no published mandatory data contract without an approved artifact.

## Integration and review

Run focused tests first, then one resource-capped `make ci-local`. Independently
review startup-before-listener failure, no silent source fallback, bounded drift
checks, CLI import-time configuration, volume ownership, and rollback inputs.
Use synthetic releases for repeatable tests; the existing private corpus can
support a bounded optional local proof without publication or VPS mutation.

Public data publication, exact production release binding, DNS/TLS activation,
and controller adoption remain dependent on the named human rights decision and
the authoritative ClinPGx DNS record. Preserve existing sealed evidence and all
rollback state.
