# ClinPGx MCP UX remediation design

Status: approved for implementation by user direction on 2026-09-05.
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

Preserve legacy retrieved_at and add retrieval_time_kind with values
upstream_acquisition/local_admission/unknown. Nullable acquired_at and admitted_at
are populated only by evidence establishing the corresponding class. Historic
snapshots without classification report unknown; the evaluation report separately
records the known local re-admission of batch2. Frozen acquisition evidence may
be bundled under the existing release contract; later observations remain external.
Never infer timestamp class from its field name or add current wall time to builds.
retrieval_time_kind classifies only legacy retrieved_at; independent acquired_at
and admitted_at may both be present without changing that classification.

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
total count, order or source completeness. Page length may shrink under the
existing byte budget, with a valid next cursor and unchanged total. Bind include_fields into the
cursor selector identity. Requested fields absent from a row are explicitly
reported as absent, not replaced with invented null source values.

Projection binding intentionally protects paged-query meaning, not only membership.
This addendum makes include_fields and pointers selectors; mode and page size remain
excluded. Preserve selection order in the hash. Changing selection starts a new
search; recovery states this rule without silently replaying a different selection.

Projected values retain current fences and typed scalar handling, including safe
profile-validated PharmCAT nested numeric maps. Omit redundant field_names on
projected responses. The original complete row and member remain retrievable.
An oversized selected value returns its own bounded deferred descriptor.
An unsafe unselected field must not force otherwise safe selected fields to defer.

Add pointers to get_dataset_record, get_record, get_api_data and get_website_data:
optional list of
1–12 unique RFC 6901 pointers, at most 4096 characters and 128 segments each.
Reject combination with nonempty pointer, cursor or nonzero offset. Multi-pointer
selection requires a JSON adapter value; HTML, text and binary representations
return invalid_input with fixed JSON-selection guidance. Use the normalized-record
namespace for repository rows and adapter-relative namespace for API/website
values; return the corresponding original source locator
explicitly. Resolve all requested values from one retained adapter response,
without one upstream call per pointer. Return scalar selections; container values
receive a typed selection error with a usable structure-retrieval alternative.

Apply a 12,000-byte canonical-JSON selected-value budget per row, greedily in
requested selection order. Defer a value that does not fit and continue trying
later values, so one oversized value does not suppress later small scalars.
The page's shaped rows, including fences and descriptors, have a 70,000-byte
budget; the complete envelope independently remains at most 100,000 bytes. Trim
only the page tail, providing a cursor for its first unreturned row. If even the
first row cannot fit, return a bounded whole-row deferred descriptor. If that
descriptor cannot fit, return a typed size error with exact-byte recovery, never
a successful empty nonfinal page. Preserve selection
order and explicit absent-pointer outcomes. Never canonicalize selected data and
label it exact original-body bytes. Numeric scalars must be returned as values,
not left for an agent to infer from SHA-256.

Unprofiled repository rows use pointers for multi-scalar access without treating
source keys as trusted output keys. Selections are ordered code-owned objects:
pointer (fenced), status (value/absent/deferred), value only for value status, and
fallback metadata only for deferred status. Stored null is status=value with
value=null, not absent. Container selections fail before emitting partial output.
Repository selections retain normalized-row and original-member references
separately; a derived /fields path never addresses the original member.
For JSON members, the original locator includes the source JSON pointer. For
TSV/CSV members it includes member reference, original row ordinal and fenced column
name; no RFC 6901 pointer pretends to address a tabular cell. If a transformation
cannot establish a direct locator, declare it unavailable and retain normalized
row identity plus original member bytes, never invent a pointer.

| Tool | Selection language | Exclusions/namespace |
| --- | --- | --- |
| search_dataset | include_fields | Profiled top-level source fields; cursor-bound |
| get_dataset_record | include_fields OR pointers OR pointer | Normalized /fields/... paths; mutually exclusive |
| get_record | pointers OR pointer | Adapter-relative for API/website; normalized row for download |
| get_api_data, get_website_data | pointers OR pointer | Adapter-relative JSON; no cursor/offset with pointers |
| get_source_content | existing pointer | Namespace belongs to returned content_ref |

With explicit selection, response_mode adds no unrequested values; only optional
presentation metadata varies. Full then means full selected values, not all fields.

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

Each profile records its source member/shape, source column documentation and
reason for default inclusion. Candidate validation checks required profiled fields
against supported shapes and emits profile_drift for incompatible changes. Optional
fields may be absent and are declared optional. Drift disables that profile, not
raw-byte access or inventory accounting; required coverage/UX gates cannot pass on
the degraded path. Every known shape is profiled or explicitly marked unprofiled.
Detailed entity/filter matrices live in capabilities output; tool descriptions
contain only concise route rules and valid examples. CI measures both schema
budgets and the bounded capabilities payload.

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

The initial rejection rule is deliberately narrow: FTS queries containing ASCII
asterisk (*) fail with exact-selector guidance. It does not claim to detect every
scientific expression. Expected tests: *1/*1 and CYP2C19*2 reject; Reference/Reference,
5-fluorouracil, N-acetyltransferase, drug combinations containing slash and rs IDs
remain valid token queries, explicitly not exact scientific-identity matches.
Example diagnostics select at most three distinct values in binary lexical index
order under the remaining filters. Diagnose at most the first two present canonical
filters in priority order id/gene/chemical/variant/name/source/annotation_id. Each
indexed diagnostic has a 50,000-SQLite-VM-step budget plus a 50-ms safety deadline;
tests inject the step budget deterministically rather than depending on host speed.
Either limit yields explicit diagnostics_unavailable, never a fabricated zero
count or changed search result. Deadline/progress hooks are scoped to the locked
connection and restored before unrelated queries.

Acceptance: positive exact queries, the negative DPYD case, tokenization loss,
hostile examples, changed selectors, expiry and nonfinal-page progress.

## R6: Measured latency and bounded inbound work

Use a shared monotonic boundary timer for model-visible elapsed_ms on success and
typed errors. Define elapsed time as tool-boundary receipt through result creation,
including validation and admission but excluding HTTP serialization and network
transit. Use a fake clock for exact timing tests. No default zero may represent
unmeasured source execution; submillisecond rounded zero is legitimate if measured.
Remove the implicit elapsed_ms=0 construction default. The boundary supplies a
measured duration and timing_scope=tool_boundary on every returned envelope.

Add a configurable per-process maximum of 16 active tool invocations by default,
bounded to 2–128. Reserve ceil(maximum/2) slots for local/retained/metadata work
and floor(maximum/2) for requests that may acquire upstream data. Route classification
uses the validated source contract; uncertain auto routing uses the upstream pool.
Outbound scheduler waiting occupies only an upstream slot. Do not borrow local
slots for upstream work. Reject excess work promptly with rate_limited and fixed
retry guidance; do not create an unbounded admission queue.

Release admission when underlying work actually terminates, not merely when its
awaiting client cancels. Track unfinished thread-backed work until completion.
Prove cancellation propagation and slot recovery through the pinned real HTTP
stack; if interruption is unsupported, expose cancellation-pending counts only in
diagnostics/telemetry while work finishes and retains its slot, not as a response
to the cancelled call. Metadata tools share the local pool except diagnostics
with probe_upstream=true, which uses the upstream pool. Preserve
outbound two-RPS limits. No query, body or exception text enters telemetry.

Active work has a 60-second execution deadline; queue waiting inside an upstream
slot counts toward it. Use bounded upstream requests and cooperative SQLite
interruption. Work that cannot actually terminate stays accounted for and makes
readiness degraded until completion or supervised restart; a timer must not free
its slot while it still consumes resources. This is a fail-closed limitation,
not a promise to kill arbitrary threads. Admission and upstream throttling retain
the public rate_limited code with separate admission_capacity and upstream_throttle
subtypes and cause-specific fixed retry guidance.

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

| Suite | Instructed calls | Hard calls | Deadline/task | Purpose |
| --- | --- | --- | --- | --- |
| Frozen twelve, all UX repeat batches | 35 | 40 | 600 seconds | Existing development UX comparison |
| New held-out UX tasks | 35 | 40 | 600 seconds | Generalization without answer hints |
| Existing frozen eighteen | 30 | 30 | 180 seconds | Separate whole-project acceptance |

All suites retain a 32-MiB trace ceiling. The different suites do not override
each other's limits. Current twelve-task performance does not prove the eighteen
will pass; optimize and test rather than relaxing the latter. In the four-session
UX batch, any unexpected admission rate_limited result is a nonpassing environment
capacity outcome, retained in the ledger, not discarded from the denominator.

For deterministic budget gates define estimated_tokens=ceil(serialized_UTF8_bytes/4).
Serialize with compact JSON separators, sorted keys, ensure_ascii=false and finite
JSON values. The response ceiling applies to one serialized envelope (each mirrored
copy independently), not the sum of the two protocol representations or framing.
The same serialization is used for schema estimates. Exact mirroring, these budgets
and closed domain enums are fleet decisions, not universal MCP requirements.
This is explicitly an estimate, not model-token usage. Both the 100,000-byte and
25,000-estimated-token ceilings apply; the stricter check wins. Model-reported
usage stays separate. CI measures schemas with this same documented estimator.

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

Opus assigns one self-review per trace using the existing frozen prompt rubric:
50 substantial friction, 80 good with clear friction, 100 no observed friction;
all integer scores 0–100 are allowed, not only multiples of five. Before a new
candidate run, freeze and hash a supplementary judge rubric defining observed
evidence for each aspect: 90 means the task is usable but has a concrete material
remaining issue; 91–99 means only minor nonblocking friction, with score and
explanation tied to the trace. This supplementary anchor does not retroactively
edit the frozen Opus prompts or raw self-ratings. One independent Fable 5.1 judgment per trace sees the
task, tool trace and verified source assertions, but not Opus's self-ratings,
candidate label or desired threshold. Report both ratings and absolute per-aspect
differences; disagreements are reviewed against concrete interactions, not averaged
away. A passing aspect must exceed 90 in both observed judgments. Grader model,
effort and exposed sampling settings are recorded; unavailable temperature controls
are explicitly unconfigured, never claimed deterministic.

Aspect coverage means at least one actually observed task per aspect per batch,
not every aspect on every task. Nulls remain visible and are excluded only from
the numerical minimum, never counted as passes. All deterministic source assertions
must pass independently of ratings. These empirical gates make no confidence-level
or population reliability claim.

After a passing development batch, require three consecutive fresh passing
twelve-task batches on the candidate revision, not best-of-three. Add a versioned
held-out set of at least five tasks covering local exact retrieval, grounded
negative, live relationship, source distinction and invalid-filter/cursor recovery.
Resolve task05's organization ambiguity only in that new set; retain the old task
and report its scope limitation. A failed batch triggers a scoped reproducible
fix and fresh verification; do not discard the failure or change its denominator.

Maintain one append-only attempt ledger across candidate revisions and repeated
batches, including timeouts, drift and discarded candidates. Each candidate gets
exactly its declared three validation batches; any failure makes that validation
campaign nonpassing. Do not retry unchanged code until it happens to pass. Start
a new campaign only after an evidence-backed code/contract correction, with all
earlier failures still reported. Rubric changes create a separately labeled series,
not a comparable improvement. Captured upstream drift is a nonpassing outcome
requiring a versioned source assertion update; never quietly absorb it as success.
Allow one recorded adjudication per candidate for a disagreement unsupported by
any concrete interaction. Keep raw scores unchanged and the campaign nonpassing;
adjudication explains the discrepancy, not overrides the >90 gate. If no
reproducible defect supports further changes, request user direction rather than
make a cosmetic code change to justify another campaign. No finite stochastic
evaluation is represented as a guarantee that all future users will score >90.

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

## Review and execution direction

The user requires Fable 5.1 adversarial review of both this spec and the subsequent
implementation plan. Record requested and actual reviewer identity, exact reviewed
document hashes, findings and their disposition. A timeout, refusal, model fallback
or missing verdict is an incomplete review, never approval. Review corrections
must be independently checked; unresolved blocking findings prevent execution.

The user explicitly directed effective implementation without iterative spec or
plan reviews. The already completed spec reviews remain recorded as evidence;
there will be one implementation-plan review, not another document-review loop.
Concrete findings are resolved in implementation decisions and regression tests.
Runtime evaluations and correctness reviews remain separate from document review.
