# Runtime-v1 identity slice review

Scope: `releases/runtime_identity.py` and its focused tests, not installation,
activation, runtime HTTP wiring or complete Task 5 acceptance.
Independent reviewer: `release_contract_review`, GPT-5.6 Sol.

Implementation `7ec1496` binds exactly five authoritative files plus the immutable
release tag to the fleet's newline-free canonical runtime-v1 digest. The separate
identity sidecar is bounded and validated, not included in its own input list.
Verification uses independently supplied expected tag/digest, real file hashes,
exact inventory, no-follow descriptors and fixed typed errors. Root ran 114
runtime/release/builder tests successfully before the review fix.

Initial review requested changes:

1. Important: a checked regular file replaced by a FIFO before blocking open could
   hang verification before type admission.
2. Minor: failure of fstat after opening the root could leak its descriptor.
3. Clarification: 2 GiB per-file/aggregate runtime defaults are chosen conservative
   defaults, not the existing per-archive expansion setting applied to snapshots.

Fix `f9a1e09` opens nonblocking before file-type admission, closes root descriptors
on every failure path, and adds deterministic regressions for source/sidecar FIFO
races and root fstat failure. The report corrects the ceiling rationale. Larger
profiles need separately reviewed runtime ceilings; measured core is about 1 GiB.

Independent re-review approved spec and quality with no remaining scoped findings.
Root subsequently ran runtime plus MCP-boundary tests: 58 passed. The implementer
reported 95 runtime/adjacent release tests, Ruff, strict mypy and commit hooks
passing. The reviewer inspected code/test evidence but did not rerun suites.

Concurrent-mutation detection remains explicitly best-effort, not an atomic
filesystem snapshot guarantee. Runtime verification is not yet wired into server
startup/health or an installer, and this approval does not claim otherwise.
