# Single Fable 5.1 implementation-plan review

Requested and observed assistant model: `claude-fable-5-1`, medium effort,
tool-free inline-document review. Completed successfully in 143,454 ms;
reported total cost USD 0.949828 includes auxiliary Haiku usage, not substitution
of the assistant reviewer. Sampling temperature was unconfigured.

Reviewed plan SHA-256:
2ff9eacc75a2c871bb53245adf7fd793517bb774e08cc4e146f4e387c9d8a656.
Reviewed spec SHA-256:
92a38163d738c2c75584ce67b9e3d7066e6489255a57e78a512b0d6d9f76d65e.
Verdict: PLAN PASS, with implementation findings. Full trace is retained privately
at `.superpowers/sdd/2026-09-05-mcp-ux-remediation/plan-review.jsonl`.
Trace SHA-256: 4add2d8fda32617a49608194963d45c59532034cf233b81ed514ce06e3931681.
Exactly one plan review was performed; no re-review is scheduled.

## Implementation dispositions

| Finding | Required implementation/test disposition |
| --- | --- |
| SQLite progress handler composition | Task 5 proves an expired outer deadline interrupts diagnostics; one composed handler owns the locked connection. |
| Live evidence retention | Task 6 exports audited live bodies while available, retaining hashes and access restrictions; expired evidence is unverifiable/nonpassing. Do not assume a late audit can recover expired cache entries. |
| Potential refetch under local admission | Task 5 checks actual content-ref resolution. If a reference can reacquire upstream bytes, route it upstream; offline-only resolution may remain local and must not silently refetch. |
| Profile drift RED gap | Task 3 removes a required fixture column and verifies drift disclosure, selection rejection, raw/pointer availability and failed coverage gate. |
| Page-tail RED gap | Task 3 verifies fewer-than-limit rows, unchanged total, nonfinal cursor and exact resumption without duplication or omission. |
| Non-JSON and acquisition-count RED gaps | Task 3 rejects multi-pointer HTML/text/binary with fixed JSON guidance and resolves JSON selections with one adapter call. |
| Capabilities size | Task 2 measures the complete capabilities envelope against 100,000 bytes/25,000 estimated tokens. |
| Indexed diagnostic work | Task 4 checks query plans for indexed bounded paths, no full table scans or temporary sorting. Add targeted indexes in the candidate schema if required; never mutate installed snapshots. |
| Aggregate provenance interface | Task 1 adds explicit source_scope and retrieval_time_scope to SourceInfo/MCP metadata. The 8,000-byte selection gate is one get_dataset_record call for two gene fields, not a twenty-row page. |
| Client cancellation timing | Task 6 records client timeout configuration and cancellation-pending capacity outcomes. |
| Local execution deadline wording | Task 5 fixed recovery describes execution timeout, not an inferred upstream outage. |

`source_scope` distinguishes snapshot/dataset/member/response identities;
`retrieval_time_scope` distinguishes aggregate_snapshot/source_recorded timestamps.
These labels do not classify acquisition versus admission: that remains
`retrieval_time_kind` and its evidence-bound nullable independent timestamps.

The review's possible refetch scenario must be verified against implementation,
not assumed true. Protocol claims are likewise not adopted solely on reviewer
authority. Existing isError behavior and fleet exact-mirroring tests remain binding.
This review is neither runtime verification nor a measured UX score.
