# ClinPGx MCP UX remediation design

Status: proposed; awaiting user approval before the detailed implementation plan.
Date: 2026-09-05. Baseline revision: a4b3319.

## Objective and scope

Fix the verified agent-interface defects and demonstrate strictly greater than
90/100 in each evaluated UX aspect using actual mounted Claude Code Opus sessions,
independent source assertions, and repeated runs. This is an acceptance criterion,
not a promise that a model can be made to assign a particular score.

This design implements the recommendations in
[the engineering review](../../reviews/2026-09-05-mcp-ux-engineering-review.md).
It supplements the existing ClinPGx design and source-access contract. It does not
replace the outstanding release, container, complete-source-coverage, or frozen
18-case acceptance requirements. End-to-end UX acceptance means real source data,
real HTTP MCP, real Claude Code, retained complete traces, and verified answers;
it must never be labeled whole-project completion while those other gates remain.

## Architecture decision

Use the existing tools and source adapters. Put supported search combinations in
one developer-owned contract, add bounded selection before response shaping, and
keep all source evidence reachable through existing retained-content references.

Alternatives considered:

- Documentation-only fixes: low risk but cannot remove the measured scalar-reading
  round trips or correct erroneous provenance; insufficient.
- New task-specific tools: potentially fewer calls, but overlap with existing tools
  and increase schema cost and maintenance. Defer until a fresh benchmark proves
  that improving the current interface is insufficient.
- Existing tools with shared contracts and bounded projections: selected. This
  directly addresses observed failures without adding an orchestration framework.

## Global invariants

- Python 3.12+, locked uv dependencies, FastMCP 3.x, MCP SDK 1.x; no dependency
  upgrade is required by this design.
- Preserve read-only research scope; no patient-specific inference or treatment.
- Preserve the six public error codes and exact TextContent/structuredContent
  mirroring, including errors. Output schemas remain optional.
- Preserve each emitted external-string fence, source provenance and raw digest.
  Code-owned field profiles never derive authority from untrusted source headers.
- Preserve the 100,000-byte serialized envelope ceiling, existing content limits,
  complete source-byte retention, explicit deferral, and usable continuations.
- Keep source selectors explicit; no automatic fallback execution or source switch
  following an explicit API/download/website request.
- Keep outbound scheduling at two requests per second during evaluation.
- Modules remain below 600 nonblank/noncomment lines. Split touched modules by
  responsibility where necessary; no unrelated reorganization.
- No tests, frozen prompts, safety limits or thresholds are weakened to pass.

## R1: Truthful source attribution

Multi-dataset envelopes identify the snapshot and the downloads collection, never
an arbitrarily selected archive. Dataset-specific row provenance comes from the
row's owning dataset response, including when a collection contains mixed families.
Every source fence, member reference, deferred value and recommended citation must
agree with that ownership.

Expose compact row provenance with dataset source URL, archive digest and its
recorded retrieval timestamp. A collection timestamp must be explicitly labeled
as aggregate/local snapshot context; it cannot claim simultaneous upstream
retrieval. Do not substitute a snapshot hash for an archive hash without declaring
its scope. Missing upstream publication/currentness remains unknown.

Implementation boundary: data/repository.py supplies SourceResponse identities;
mcp/dataset_record_tools.py and mcp/record_shaping.py apply owning-row provenance.
No source files or registry captures are rewritten to repair presentation.

Acceptance: mixed-source fixtures with different timestamps and URL orderings;
catalog, search, direct record and deferred-content assertions agree. A pathways
archive must never be cited as the origin of a genes or chemicals row.

## R2: Executable discovery and recovery

A shared developer-owned search contract drives tool descriptions, capabilities,
validation and recovery. Publish supported entity/source/filter combinations,
exact versus token semantics, installed-only coverage, and useful fixed examples.
Keep get_server_capabilities compact; do not repeat the entire OpenAPI document.

Preserve canonical gene and chemical selectors for API name resolution. Guideline
name queries receive a documented resolve-identifiers then pair-relationship
workflow. An accession-based shortcut is allowed only for strict validated PA IDs
mapped to captured related-accession parameters; unsupported names are not coerced.

Distinguish unsupported filters from unsupported source using a typed subtype.
Recovery includes a valid tool and schema-valid arguments when the necessary
validated identifiers are available; otherwise it explains the missing identifier
and offers contract discovery, not a broad unfiltered source-switch command.
Code-owned instructions may name required fields. Never reflect arbitrary rejected
values or exception strings. Recovery suggestions do not execute automatically.

Local membership search remains membership search, not identity resolution. State
this distinction in discovery and results so a haplotype carrying a gene membership
cannot be mistaken for a gene-family record.

Acceptance: exercise every published example through a real FastMCP client with
stubbed source adapters, including malformed and hostile values. Replay the three
invalid API combinations from batch2 task04 and verify actionable recovery.

## R3: Bounded field and scalar selection

Add include_fields to search_dataset and get_dataset_record: optional list of
1–16 unique exact code-owned field names, each at most 512 characters. Reject an
empty explicit list, duplicates, unprofiled names and incompatible pointer use.
Projection is performed before safety shaping, but never changes row membership,
row count, order, pagination or source completeness. Bind include_fields into the
cursor selector identity. Requested fields absent from a row are explicitly
reported as absent, not replaced with invented null source values.

Projected values retain current fences and typed scalar handling, including safe
profile-validated PharmCAT nested numeric maps. Omit redundant field_names on
projected responses. The original complete row and member remain retrievable.
An oversized selected value returns its own bounded deferred descriptor.
An unsafe unselected field must not force otherwise safe selected fields to defer.

Add pointers to get_record, get_api_data and get_website_data: optional list of
1–12 unique RFC 6901 pointers, at most 4096 characters and 128 segments each.
Reject combination with nonempty pointer, cursor or nonzero offset. Multi-pointer
selection requires a JSON adapter value; HTML, text and binary representations
return invalid_input with fixed JSON-selection guidance. Use the
adapter-relative namespace; return the corresponding original source pointer
explicitly. Resolve all requested values from one retained adapter response,
without one upstream call per pointer. Return scalar selections; container values
receive a typed selection error with a usable structure-retrieval alternative.

Apply the existing inline scalar budget with a combined 12,000-byte selected-value
budget before envelope shaping; defer values that do not fit. Preserve selection
order and explicit absent-pointer outcomes. Never canonicalize selected data and
label it exact original-body bytes. Numeric scalars must be returned as values,
not left for an agent to infer from SHA-256.

Acceptance: two gene fields available in one call below 8,000 serialized payload
bytes; five PharmCAT fields retain numeric allele count; large guideline title and
source readable in one projected request; malformed, escaped, hostile, absent,
oversized and mixed-scalar cases remain bounded and source-correct.

## R4: Truthful modes and source profiles

Keep minimal/compact/standard/full for compatibility. Implement them centrally:
minimal returns identity, requested selected values and required provenance;
compact adds bounded code-profiled summary fields; standard adds available normal
profile fields; full exposes all fields, deferring where required. Explicit field
selection wins over optional mode-derived fields. No mode removes rows, required
source limitations or reachable full data. Scalar endpoints may have identical
modes when there is no optional content; document that explicitly.

Default fields must be code-owned, source-family-specific and general-purpose,
never selected using benchmark answer keys. Publish the exact mode profiles in
get_dataset for known dataset record shapes. Unknown shapes remain explicitly
unprofiled and use retained-content discovery. This must not imply that all JSON
members share one schema. Keep tool schema estimates within the existing per-tool
1,200 and aggregate 10,000 estimated-token budgets.

Acceptance: modes have measured size/field differences where optional fields
exist; explicit projection is stable across modes; every mode retains all matches,
counts, limitations and an executable path to complete source content.

## R5: Honest matching and continuations

Exact structured selectors preserve scientific punctuation. General FTS remains
token search, but lossy scientific query forms such as *1/*1 are rejected with a
fixed instruction to use exact gene/name filters rather than being called literal
matches. Document this narrowing as a corrective contract change.

For an exact zero-result query with supported bounded canonical filters, return
diagnostics computed within the same snapshot: counts under the remaining filters
and at most three stored example values. Examples remain fenced and explicitly
state that they are not equivalence claims. Do not echo the failed caller value,
infer an alias, retry automatically, or scan an unbounded field vocabulary.
Implement only indexed diagnostics with bounded result and query work.

PharmCAT discovery must expose gene/name mappings and actual record-profile fields.
DPYD *1/*1 continues to return zero even when Reference/Reference is suggested as
an observed stored example. Cursor recovery explains selector binding, expiry
and offset incompatibility without modifying the cursor or concealing failures.

Acceptance: positive exact queries, the negative DPYD case, tokenization loss,
hostile examples, changed selectors, expiry and nonfinal-page progress.

## R6: Measured latency and bounded inbound work

Use a shared monotonic boundary timer for model-visible elapsed_ms on success and
typed errors. Define elapsed time as tool-boundary receipt through result creation,
including validation and admission but excluding HTTP serialization and network
transit. Use a fake clock for exact timing tests. No default zero may represent
unmeasured source execution; submillisecond rounded zero is legitimate if measured.

Add a configurable per-process maximum of 16 active tool invocations by default,
bounded to 1–128. Reject excess work promptly with rate_limited and a fixed retry
hint; do not create an unbounded wait queue. Release admission on success, error
and cancellation. Metadata tools share the bound. Preserve outbound two-RPS limits.
No queries, source bodies, credentials or exception text enter telemetry.

The evaluator records monotonic tool-call send/result times in addition to total
task time where client trace instrumentation supports them. Mark unavailable
measurements unavailable. Record scheduler queue time for tasks and separate it
from execution; stratify local/cache/live observations and successful/error calls.

Remote ingress authentication and deployment isolation remain separate existing
project gates. No blanket ban on internal container 0.0.0.0 binding is introduced.

Acceptance: fake-clock tests, mixed concurrent reads with correct identities,
overload, cancellation and recovery; no mirrored-envelope divergence.

## R7: End-to-end evaluation and reporting

Preserve baseline and batch2 artifacts and prompts. Run the twelve frozen tasks
against the updated mounted MCP, four isolated Opus sessions concurrently. Freeze
code, snapshot and source configuration during each batch. Retain every attempt,
complete bounded traces, actual model identity, task/prompt hashes, cache conditions,
call counts, errors, timing and distinct token/cache usage counters.

Use the existing per-task limits: 35 instructed MCP calls, 40-call hard safeguard,
600-second deadline and 32-MiB trace ceiling. The hard safeguard does not excuse
violating the 35-call task budget. Transport acceptance remains separate from
answer acceptance. No silently substituted model or truncated trace can pass.

Independently check IDs, exact fields, archive/member hashes, source URLs, dates,
negative results and reported coverage against retained source evidence. Live
answers are audited against captured live responses, not stale installed rows.
No source-provided lack of evidence is converted into global absence.

Eight UX aspects: correctness confidence, completeness, discoverability, token
efficiency, speed, error recovery, provenance clarity and overall usability.
Require every observed score to be strictly greater than 90, taking the minimum
across tasks; every aspect must have actual coverage. Null is unobserved, never
100. Do not give the evaluator a target score or answer key. Report subjective
ratings separately from independent correctness and measured performance.

After a passing development batch, require three consecutive fresh passing
twelve-task batches on the candidate revision, not best-of-three. Add a versioned
held-out set of at least five tasks covering local exact retrieval, grounded
negative, live relationship, source distinction and invalid-filter/cursor recovery.
Resolve task05's organization ambiguity only in that new set; retain the old task
and report its scope limitation. A failed batch triggers a scoped reproducible
fix and fresh verification; do not discard the failure or change its denominator.

Publish a concise comparison report with all per-task scores, measured counts,
cost and limitations. Keep full traces private/ignored with digest manifests.
The existing eighteen-case acceptance manifest remains unchanged and independently
required for whole-project completion.

## Delivery boundaries and verification

Implementation plan work packages, each with RED/GREEN tests and review:

1. Repository/row attribution (R1).
2. Shared search contracts and actionable recovery (R2).
3. Projection and mode/profile shaping (R3–R4).
4. Matching diagnostics and cursor recovery (R5).
5. Boundary timing/admission and evaluator measurement (R6).
6. Mounted evaluation, held-out checks, iteration and report (R7).

Shared-file work must be serialized or explicitly owned. In particular repository,
facade, recovery and dataset-record shaping changes must not be independently
committed by competing workers. No agent stages another agent's files.

Each package runs targeted tests, Ruff and typing. The integrated candidate runs
make ci-local with the pinned sibling router checkout and real MCP conformance.
Repository hooks remain mandatory. Documentation includes field/mode semantics,
source guarantees, migration notes for lossy FTS rejection and reproducible eval
commands. No completion claim precedes fresh evidence of its exact gate.

## Approval checkpoint

The user requires Fable 5.1 adversarial review of both this spec and the subsequent
implementation plan. Record requested and actual reviewer identity, exact reviewed
document hashes, findings and their disposition. A timeout, refusal, model fallback
or missing verdict is an incomplete review, never approval. Review corrections
must be independently checked; unresolved blocking findings prevent execution.

Approve this design before writing the detailed implementation plan. The plan will
map every R1–R7 requirement to concrete files, failing tests, implementation steps,
review ownership and acceptance commands. Execution follows that approved plan;
any material contract expansion returns to design review.
