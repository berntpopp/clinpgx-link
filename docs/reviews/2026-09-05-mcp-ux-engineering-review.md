# MCP UX engineering review: evidence before a 90-point claim

Date: 2026-09-05. Audience: ClinPGx Link maintainers.
Reviewed revision: f53ce9fafb59a53e8add89c35d8b8742167f3c26.
Scope: current protocol/SDK guidance, official community proposals and issues,
fleet constraints, source-code inspection, and twelve actual mounted-Claude-Code
Opus tasks. This is a review and proposed implementation sequence, not a claim
that the proposed changes have shipped.

## Decision

Preserve the existing read-only architecture and fix its agent-facing contract.
Prioritize truthful provenance, valid search/recovery workflows, and bounded
field selection. Do not add a new framework or remove safety metadata to chase
ratings. The evidence does not establish any guaranteed route to >90/100.

All twelve batch2 traces completed with the requested Opus family
(resolved claude-opus-5). Trace integrity passed; the UX gate failed.
There were 222 MCP calls, 33 error results, 4,294,821 trace bytes and
USD 11.8524375 client-reported cost. Errors include intentional adversarial calls;
33/222 is not an operational failure-rate estimate. Individual task durations
were 77.32–186.54 seconds. Their sum, 1473.21 seconds, is not parallel wall time.
Trace bytes include client events and duplicated representations; they are not
model-visible tokens or standalone MCP response sizes.

## Complete observed scorecard

C = correctness confidence; F = completeness; D = discoverability;
T = token efficiency; S = speed; E = error recovery; P = provenance;
U = usability. Dash means unobserved, not a pass.

| Task | Calls/errors | C | F | D | T | S | E | P | U |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 01 Local gene provenance | 12/0 | 90 | 85 | 80 | 50 | 100 | — | 85 | 80 |
| 02 CYP2C19 diplotype | 9/0 | 95 | 100 | 80 | 50 | 100 | — | 100 | 80 |
| 03 Absent DPYD diplotype | 17/0 | 90 | 85 | 50 | 50 | 90 | 50 | 100 | 65 |
| 04 Current guideline | 29/5 | 85 | 60 | 50 | 40 | — | 75 | 95 | 60 |
| 05 DPYD guidelines | 33/5 | 85 | 80 | 50 | 50 | 100 | 50 | 100 | 65 |
| 06 Thiopurines | 26/8 | 90 | 85 | 55 | 45 | — | 70 | 95 | 65 |
| 07 Variant versus haplotype | 16/2 | 90 | 80 | 50 | 50 | — | 90 | 100 | 80 |
| 08 Regulatory label | 15/3 | 85 | 80 | 55 | 45 | — | 80 | 95 | 70 |
| 09 Population frequency | 14/0 | 90 | 80 | 80 | 50 | — | — | 100 | 80 |
| 10 Linked literature | 15/4 | 90 | 85 | 50 | 50 | 100 | 80 | 95 | 70 |
| 11 Pathway | 22/3 | 85 | 70 | 50 | 25 | 90 | 75 | 95 | 60 |
| 12 Recovery and pagination | 14/3 | 85 | 90 | 50 | 50 | — | 50 | 50 | 65 |
| Minimum observed | — | 85 | 60 | 50 | 25 | 90 | 50 | 50 | 60 |

No category's minimum is strictly greater than 90. Independent audits verified
core returned IDs and source values, but found answer-completeness limitations.
Task05's wording assumes two records while not restricting organization; its
CPIC-focused answer must not be called an exhaustive all-organization answer.
Task11 asked for one connected gene, so failure to enumerate the entire graph
is not itself a task failure. Preserve both facts when grading.

Several agents miscounted their calls; harness counts above are authoritative.
Task03 correctly reported that exact DPYD *1/*1 is absent; the stored
Reference/Reference is not an established equivalent. A grounded negative is a
valid outcome, not a reason to manufacture an alias.

## Standards and applicability

The lockfile pins FastMCP 3.4.7 and MCP SDK 1.29.1. Installed SDK code supports
protocol versions through 2025-11-25. The official latest specification currently
redirects to 2026-07-28; this is a migration consideration, not evidence that this
server negotiated that revision. New wire fields must not be copied into the
older stack without interoperability tests.
Sources: [installed-target tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools),
[latest specification](https://modelcontextprotocol.io/specification/latest).

MCP output schemas are optional. When advertised, outputs must conform.
Structured results should also have serialized text for compatibility.
The fleet additionally requires exact text/structured mirroring. Therefore
output_schema=None is not a protocol defect, and replacing text with a shorter,
different summary would violate the local contract.
Source: [MCP structured results and schemas](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).

The official community's Final SEP-1303 supports model-visible input-validation
errors. Returning isError is useful only if the content explains a valid next
step. Other proposals must be judged by status and applicability, not treated as
requirements merely because they appear in a discussion.
Sources: [SEP-1303](https://modelcontextprotocol.io/seps/1303-input-validation-errors-as-tool-execution-errors),
[SEP lifecycle](https://modelcontextprotocol.io/seps/index).

Protocol pagination covers tools/resources/prompts catalog listing, not arbitrary
domain search results. Our selector-bound tool cursors are an application
contract; do not confuse them with standard tools/list pagination.
Source: [MCP pagination](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination).

Anthropic recommends task-oriented tools, selective outputs, clear parameters,
actionable errors, and real-agent evaluations with held-out cases. Applied here,
that favors improving the existing search/detail operations before adding
overlapping convenience tools. It does not imply stripping scientific IDs or
provenance that these tasks explicitly require.
Source: [Writing effective tools](https://www.anthropic.com/engineering/writing-tools-for-agents)
(2025-09-11).

Anthropic's evaluation guidance distinguishes objective checks, model judgments,
and human calibration, and emphasizes variability across repeated trials.
One favorable self-rating is not reliability evidence. Use independent source
checks and retain unsuccessful trials.
Source: [Demystifying agent evaluations](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
(2026-01-09).

## Prioritized engineering findings

### P0: Correct provenance before optimizing presentation

DatasetRepository._source(None) picks the most recently admitted dataset's URL.
Thus heterogeneous searches and catalogs cite pathways.json.zip for unrelated
genes, chemicals, or haplotypes. shape_dataset_row also uses collection provenance
even when an asset-specific response is available.

Use truthful snapshot-level context for collections and dataset-specific
acquisition provenance for each row. Keep local admission, upstream retrieval,
publication, and authenticated installation dates distinct; unknown remains
unknown. Regression tests must use at least two datasets with different timestamps
and verify every emitted row and source fence against its owning archive.

### P0: Make discovery and recovery describe executable workflows

The global FilterArg description suggests combinations the API mapping does not
support. Gene-name resolution uses the canonical gene selector; chemical-name
resolution uses chemical. Guideline related filters use accession IDs upstream,
not arbitrary names. Failed source=api searches currently suggest unfiltered
download membership searches, which can return the wrong entity family.

Generate descriptions, capability matrices, validation and recovery from one
developer-owned contract. Distinguish unsupported filters from unsupported source.
Provide a fixed name-resolution and pair-relationship workflow where needed.
Never auto-switch sources, echo hostile values, or invent a canonical website URL.
Test each advertised example through the mounted MCP and stubbed upstream adapter.

### P1: Make compact outputs genuinely compact

Several response_mode parameters are only echoed or do not change output shaping.
get_dataset_record shapes the whole row even when a pointer selects one value.
Unknown source fields can defer all fields, making simple reads expensive.

Add bounded optional field projection before shaping: at most 16 unique,
developer-profiled fields, preserving existing per-string untrusted fences,
digests and source identity. Bind projection into cursor selectors. Unknown
fields must remain safe and reachable via explicit content fallback. Omit
redundant field_names when the projected keys already provide that information.
For adapter data, consider at most 12 scalar pointer selections in one call,
with explicit adapter-relative and original-source pointer namespaces.
Do not introduce two competing selector languages if one can cover the workflow.

Maintain mirrored envelopes and all source/coverage warnings. Define and test
minimal/compact/standard/full semantics; a mode must not promise a smaller response
while merely changing a label. Suggested development targets, not standards:
an exact two-field local read below 8 KB and no mandatory structure-walking calls
to read ordinary selected scalars.

FastMCP automatically supplies traditional content in common structured-only
paths. Generic response-limiting middleware can truncate structured information;
a first-party issue documents resulting schema failures. It is not a substitute
for our bounded pages and lossless continuation.
Sources: [FastMCP middleware](https://gofastmcp.com/servers/middleware),
[FastMCP issue 3743](https://github.com/PrefectHQ/fastmcp/issues/3743)
(community bug report; not a protocol standard).

### P1: Explain literal matching and safe continuations

FTS converts punctuation-rich *1/*1 into token 1, producing thousands of matches.
Do not call this literal matching. Preserve existing exact selectors; either
reject lossy query forms or explain tokenization before callers mistake broad
matches for exact diplotypes.

Expose code-owned PharmCAT record profiles instead of empty field metadata.
A zero-result diagnostic can show bounded, source-backed examples under remaining
filters, explicitly labeled examples rather than equivalent alleles.
Cursor errors should say which fixed selector-replay rule applies without
reflecting arbitrary values. Tests must cover altered selectors, expiry,
zero-progress pages, hostile samples, and exact negative queries.

### P1: Measure latency honestly and bound server work

Some source-presenter responses use the envelope's default elapsed_ms=0.
That is not measured API latency. Add request-scoped monotonic measurements,
with defined validation/queue/upstream/shaping boundaries, without mutating only
one side of the mirrored envelope. Separate success and error timings, cache
state, and client task time.

Google's SRE guidance separates latency, traffic, errors and saturation and
warns that successful-looking responses can still contain wrong data.
Source: [Monitoring distributed systems](https://sre.google/sre-book/monitoring-distributed-systems/).
Our corresponding tests should include correctness under concurrency, bounded
inbound work, cancellation and cache pressure. Outbound two-RPS scheduling is
not an inbound concurrency bound. Do not log arguments or raw exception bodies.

Remote deployment also needs an explicit ingress authentication/trust boundary.
A container listening on 0.0.0.0 is not necessarily publicly exposed; validate
published ports and proxy isolation rather than blindly banning container binds.
This remains a deployment gate, not a demonstrated cause of current UX scores.

## Implementation and evaluation sequence

1. Correct archive attribution and filter/recovery contracts with failing tests.
2. Add bounded projection, truthful modes and discoverable record profiles.
3. Add measured latency and focused saturation/cancellation checks.
4. Independently review fixes; run ci-local and real MCP conformance.
5. Repeat the twelve frozen tasks in four concurrent isolated Opus sessions,
   preserving failed runs, model/code/data identities and full bounded traces.
6. Add fresh equivalent held-out cases; resolve ambiguous wording in a versioned
   new set, never retrospectively edit the frozen batch.
7. For a candidate passing batch, repeat it at least three times. Report all
   observed scores and objective answer checks, not a best-of-three result.

The acceptance target remains strictly >90 in every observed aspect, minimum
across tasks, with each aspect covered and independently correct answers. Keep
subjective ratings separate from objective latency, token and correctness gates.
No honest engineering review can guarantee an evaluator's score.

## Evidence and limitations

Raw batch2 artifacts remain machine-local and ignored at
/tmp/clinpgx-opus-batch-ld25q3mu/batch2/<task>/; prompts are versioned under
tests/eval/opus-batch/. Each summary includes trace/prompt hashes, git identity,
usage and actual resolved model. These artifacts are not published release assets.

Mounted snapshot:
sha256:b1112fe8465148a0217d59d8fbc85e15d09fd207df4cfbb602273a7d20334121.
Twelve locally re-admitted archives; diagnostic tag is not authenticated release
installation. Later tasks shared a warmer cache; concurrency was four, not twelve.
The baseline and this batch have different data inventories.

Research used bounded searches for official tool-design/eval advice, versioned MCP
requirements, FastMCP behavior and official community issue/SEP records, followed
by direct source reads and installed-code checks. Search stopped when the major
recommendations had primary support and concrete local evidence; no broad forum
consensus, exhaustive security audit or protocol migration is claimed.

The latest prior ci-local run passed 781 tests plus lint, formatting, typing,
module-size and vendor gates. That does not prove coverage equivalence, two-release
install/upgrade/rollback, container hardening, or the frozen eighteen-case
acceptance suite. Those remain separate outstanding work.
