# Pure release-contract review

Scope: manifest admission and stable release-key generation only. This is not an
installer, packaging, publication-rights or production-readiness approval.
Independent reviewer: `release_contract_review`, GPT-5.6 Sol.

## Initial findings

The review of `368a1bb` and `87cd9d7` requested changes:

1. Important: local manifest timestamp types and generated JSON Schema metadata
   differed from the pinned fleet model. The happy-path wire-schema test did not
   detect this drift. Addressed in `80ef6ed`; scoped re-review below.
2. Important: release-key tests did not pin canonical bytes, the complete digest
   or exact tag. Serialization drift could therefore remint immutable identities
   without failing the tests.
3. Minor: malformed source-pair shapes raised raw Python exceptions before typed
   validation.

The reviewer found digest-first byte authentication, bounded strict JSON admission,
the vendored schema pin, stable-input projection and tag formula sound within this
scope. Reported test suites were not rerun by the reviewer.

## Identity remediation

Commit `e19e7a2` adds source-pair shape validation and a literal golden vector
covering UTF-8 bytes, key ordering, compact separators, final newline, full SHA-256
identity and the exact tag. The digest expectation was independently calculated
over the manually specified bytes, not through the implementation helper.

Root verification: malformed tuple and `None` cases reproduced raw exceptions
before the fix; all 20 identity tests passed after it. Temporarily changing Unicode
serialization to ASCII escaping caused the golden test to fail; restoring UTF-8
returned the suite to green. Ruff, strict mypy and commit hooks passed.

The independent reviewer re-read `e19e7a2` and approved both identity findings with
no remaining scoped findings. `git show --check` was clean. That re-review did not
cover the unfinished manifest changes or rerun the tests.

## Manifest remediation

Commit `80ef6ed` restores aware datetime values, exact schema-version semantics,
immutable-tag exclusions and top-level metadata. Its regression test compares the
entire generated schema to the pinned vendored fleet schema. Independent re-review
approved the outer-model/schema and trusted-byte admission work with no remaining
scoped findings. Strict/hide-input configuration and the timestamp pre-parser are
local adaptations preserving or strengthening JSON admission; the Router runtime
projection is still outside this slice and remains future work.

Root read the actual diff and ran `make ci-local` after the fix: 449 tests passed,
with Ruff, strict mypy, module budgets, vendor verification and FastMCP imports
passing. Two upstream TestClient deprecation warnings remain. The reviewer did not
rerun these suites. Neither verdict establishes complete release delivery.
