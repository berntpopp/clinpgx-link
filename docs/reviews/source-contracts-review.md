# Source and rights contract review

Scope: project-owned source/rights models, canonical parsing, generated schemas,
cross-contract rights mapping and stable identity checks. This is not approval of
real redistribution rights or the unfinished release installer.

Claude Code `2.1.261`, using `claude-fable-5` with tools disabled, independently
reviewed the complete `ecef5ef..1f9c77d` task diff against its brief, implementation
report, and binding design excerpt. It identified two important issues:

1. Array bounds were emitted as `minLength`/`maxLength`, which external JSON Schema
   validators ignore for arrays. Root reproduced ten affected array schemas and
   acceptance of empty required inventories.
2. URL schemas lacked an enforceable HTTPS constraint; `format: uri` alone did not
   express the model's restrictions.

Fix `4e654d4` emits `minItems`/`maxItems` and an explicit URL pattern. Regression
tests use an external Draft 2020-12 validator, not just generated-schema parity.
The fix also adds a balanced deep-recursion test, distinguishes wrong input types
from resource-limit errors, and permits empty inventories for retained non-indexed
opaque artifacts without permitting an indexed artifact to have no indexed member.

A separate, scoped Fable re-review of `1f9c77d..4e654d4` marked every requested fix
addressed and returned **Spec PASS / Quality PASS**. It noted one minor remaining
test gap: explicitly combining indexed status with an empty member inventory.
Root confirmed the unchanged semantic validator rejects that combination; no
stronger test-coverage claim is made here.

Root's fresh verification at `4e654d4`:

- Source/rights focused suite: 72 passed.
- Full `make ci-local` with pinned Router parity: 687 passed; typing over 51 modules,
  formatting, lint, module-size, both vendor checks, and FastMCP import checks passed.
- Two existing third-party TestClient deprecation warnings remained.

The original requirement for an immutable notice reference still requires reviewed
evidence; URL syntax alone cannot certify that an external resource is immutable.
No actual license approvals were fabricated. Catalog-only/unacquired sources remain
separate from the retained-artifact projection. Standalone non-ZIP ingestion and
complete publication/installation integration remain unfinished.
