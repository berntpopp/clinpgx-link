# Fleet behaviour probe: initial live result

Date: 2026-09-05. Code revision: `00d48d9`. Probe: unchanged
`../genefoundry-router/docs/conformance/behaviour.py` at the pinned Router checkout.
An actual loopback Uvicorn process served a locally built sourced-fixture snapshot;
the probe used `--name clinpgx-link --timeout 15 --quiet`. The controller imposed a
240-second overall bound and shut the server down cleanly after the probe.

Result: **NON-CONFORMANT — 100 passed, 16 failed, 3 UNGATED, 2 inconclusive**.
Probe exit status: 1. This is not the 18-case real-agent benchmark and is not
source-coverage acceptance.

## Findings and verified causes

- Thirteen tools: unknown-argument errors provide no actionable field. BoundaryGuard
  emits InvalidInputError without a field; the envelope intentionally replaces
  exception prose with fixed safe text. Remedy must identify a code-owned field
  without reflecting hostile argument names or values.
- Three UNGATED tools: get_dataset and search_dataset omit examples for required
  dataset_id; get_dataset_record omits an example for required record_id. Dynamic
  record IDs must remain snapshot-bound, not a fabricated globally valid record.
- get_api_data and get_website_data: required operation examples name parameterized
  routes, but the probe builds only top-level required arguments and therefore
  omits their optional path_parameters. These calls are correctly rejected as
  incomplete. Example selection/documentation still needs correction.
- get_related_records: default source=api requires other_id, which is optional
  because source=download supports single-record joins. The probe supplies only
  top-level required record_id/result_type. Do not fabricate a default second ID or
  change the frozen source semantics to satisfy this probe. Further investigation
  found a missing promised connected-object mode; see below.

Inconclusive: get_record returned a singleton that supplied no rows for filter
probes; get_source_content used a well-formed but absent dynamic handle and returned
not_found. Neither was counted as a successful data-coverage test.

The actionable-error and missing-example fixes are assigned as one scoped change.
The three rejected example controls remain separately tracked. The initial result
must remain in the record even after remediation; reruns report their own totals.

## Parameter-free source examples checked live

Root verified two alternatives without caller parameters, with a 1 MiB body cap,
15-second request timeout, no redirects and at least 0.5 seconds between requests:

| Operation | HTTP | Bytes | Seconds | SHA-256 |
|---|---|---|---|---|
| `GET /report/stats` | 200 | 1976 | 0.871 | `0823726e3a0cb2b8457feb97070a35e67433f760e80b8e797526748b1b548d53` |
| `GET /site/pathwayCategories` | 200 | 1827 | 0.210 | `3c758a032c5fc21762c769be8f4cb004efc95ea35393e49bd156c6bf5a0f0533` |

Both responses decoded as JSON objects. Their captured operation contracts require
no path/query fields. These are suitable first examples without inventing defaults
for parameterized calls. They have been added to the remediation scope; this direct
upstream probe does not yet prove the revised MCP example controls pass.

## Relationship-mode investigation

The main design explicitly promises a connected-object **or** pair report. Current
code only implements pairs. The registered connected-object endpoint interprets
its `type` as a target object family, not a pair annotation type. Root confirmed:

- `/v1/report/connectedObjects/PA124/Chemical`: HTTP 200, 24,094 bytes, 170 rows,
  containing `connectedObject` and `connectionTypes`.
- The same route with `summaryAnnotation` as type: HTTP 404, 65 bytes.

The implementation assignment therefore completes the missing feature:
`result_type=relationship` without `other_id` uses connected-object discovery for
`other_type`; supported pair result types with `other_id` retain the existing pair
route. Invalid combinations still fail explicitly. The first required result-type
example becomes `relationship`, making the default-source example meaningful
without fabricating a second identifier or altering source defaults. Completion
and a fresh unmodified-probe rerun are still required.
