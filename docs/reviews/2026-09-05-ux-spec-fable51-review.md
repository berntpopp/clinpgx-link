# Fable 5.1 UX specification review attempts

Date: 2026-09-05. Status: completed third attempt returned SPEC FAIL;
corrections are proposed and require re-review.

The user requested Fable 5.1 adversarial review of the remediation specification
and subsequent implementation plan. No implementation plan has been written yet;
the Superpowers written-spec approval checkpoint remains pending.

Reviewed-input specification SHA-256:
b2cac8a497ca0b9271449ae297830928f814848f2c881957232394f853db7394.
Source-access contract SHA-256:
98722a6292715da09ace1d5b54cc72cf41cb2148796334c48aa55363f841e63e.
The proposed spec was subsequently clarified for non-JSON selections and to record
the requested review gate; those edits have no Fable review verdict either.

## Attempts

1. Claude Code requested claude-fable-5-1 with only Read available, safe mode,
   no session persistence and a 240-second timeout. Requested files: proposed spec,
   binding source contract and engineering review. Exit 124; no JSON result or
   stderr content. Actual model execution could not be established from this output.
2. A single retry supplied the documents directly on stdin and exposed no tools.
   Stream initialization confirmed claude-fable-5-1, zero tools and zero MCP servers.
   No assistant answer or result event arrived. The process did not exit promptly
   after timeout SIGTERM; its exact validated child PID was forcibly stopped.
   Final command exit 124. This is a terminated incomplete trace, not acceptance.

The second trace contains 185 events and has SHA-256
8b56e20de9cad28c38e436ff50a84f11ea38cca4188d2db319196f9a530f7d88.
Private artifacts are retained under
.superpowers/sdd/2026-09-05-clinpgx-link/ux-spec-fable51-review*.
No failed attempt was substituted with another model or labeled PASS.

## Completed attempt 3 and finding disposition

A visibly declared retry used a 600-second deadline, five-second forced-termination
grace and medium effort, with all documents supplied and no tools. It exited 0;
the client reported 136,098 ms and USD 0.833525. Initialization and the assistant
answer identify claude-fable-5-1. Aggregate usage also contains auxiliary Haiku
usage; no assistant answer-model substitution was observed.

Reviewed spec SHA-256:
6bac88312879aa32164d8e7d0c655ffadd5d2ee8426eb947d2849b23b37588ac.
Trace SHA-256:
3c14bc04c08f856fab2d19fb362a6fe1bf718f290ce531d041b51ba1f81d5e5c.
Verdict: SPEC FAIL, findings 1–4 blocking. The full answer remains in the private
attempt3 trace. Main-agent dispositions below are not reviewer approval.

| Finding | Disposition in revised proposed spec |
| --- | --- |
| 1. Underspecified/gameable scoring | Explicit self/judge rubric, integer scores, independent blinded Fable judgment, observed-aspect coverage, all-attempt ledger, fixed three-batch campaigns and nonpassing drift outcome. No statistical confidence claim. |
| 2. Admission contention/cancellation/timing | Reserved local/upstream pools, upstream waits occupy upstream slots, actual-work lifetime owns admission, real-stack cancellation tests, measured timing required. Capacity failure cannot pass evaluation. |
| 3. Unprofiled scalars and selector ambiguity | Repository records gain bounded pointers; fenced path/value records preserve untrusted keys. A tool/selector table and explicit-selection precedence define the interface. |
| 4. Suite/response budgets | Side-by-side twelve/held-out/eighteen limits; byte/token estimator and matrix placement made explicit. Limits remain unchanged; old performance does not establish that eighteen-case acceptance is impossible. |
| 5. Projection cursor binding | Retained deliberately and justified as binding query meaning. The existing contract already hashes selectors; mode/page-size exceptions do not imply a projection exception. Added clear restart/replay behavior. |
| 6. Timestamp provenance classes | Added explicit acquisition/admission/unknown classification. Corrected the premise: the existing contract permits frozen acquisition evidence in builds, while later observation timestamps stay external. |
| 7. Undefined lossy-query detection/order | Exact initial rejection rule for ASCII star/slash, retained hyphenated token queries, deterministic example ordering and bounded diagnostic execution. |
| 8. Profile maintenance/drift | Required/optional field distinction, source-column justification, explicit unprofiled shapes and profile-drift fallback/gate behavior. |

## Next required action

Fix-review 1 completed on spec digest
35c2b8e5b6119175cad1d7aebf617ba74e5610ac55ecc12575797ae23478b1dd,
with actual claude-fable-5-1, exit 0, 105,792 ms and USD 0.692736.
Trace digest: a3ebc36121a43179b75c6c3b77b42ac32472015b5269a76081c39dc103548d0f.
Verdict remained SPEC FAIL (three blocking, five additional corrections).

The next proposed revision defines per-row/page budgets and tail trimming,
container/absent scalar outcomes, tabular locators, cancellation-pending telemetry,
upstream diagnostic routing, typed rate-limit causes, fail-closed execution
deadlines, exact FTS rejection/diagnostic priorities and estimator serialization.
It adds intermediate rating anchors and a recorded adjudication that cannot
override raw low scores. No cosmetic change justifies re-running a failed campaign.

Two reviewer statements are not adopted as universal protocol facts: exact
text/structured mirroring, schema size budgets and closed domain enums are fleet
requirements; and an arbitrary running thread cannot safely have its resource slot
released just because a deadline elapsed. The spec explicitly distinguishes both.

## User direction: end document-review iteration

The already-running fix-review 2 completed with actual claude-fable-5-1, a
SPEC FAIL verdict and 169,773 ms / USD 0.967848. Its findings concern held-out
independence, rating calibration, container-selection ergonomics, wire timeout
outcomes, FTS rejection scope, tabular continuations, admission-time evidence and
serialization. Concrete dispositions are recorded in the implementation plan.
The earlier table's star/slash wording describes an intermediate revision: the
final rule rejects only ASCII asterisk in FTS, not slash or exact selectors.

The user explicitly stopped iterative spec/plan reviews and directed implementation.
No further spec review will be launched, and no passing spec verdict is claimed.
The plan receives one Fable review; implementation proceeds with findings addressed
through code/tests, without another document-review loop. Neither review establishes
measured MCP UX. Private full review traces remain retained and ignored.
