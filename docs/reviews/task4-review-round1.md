# Task 4 review, round 1

Date: 2026-09-05
Reviewer: independent gpt-5.6-sol (`/root/clinpgx_research`)
Verdict: NOT APPROVED. A focused 75-test pass did not cover the defects below.

## Reproduced findings

1. High: a hostile upstream Content-Type declaration was stored and emitted unfenced.
   Acquisition and retained-store admission need canonical MIME validation. Assigned
   to the API implementer with original-byte and hostile-header regressions.
2. High: structure pagination materialized every child tuple before slicing. A
   1,000,001-byte array peaked at 50,219,205 traced bytes for a one-child late page.
   Root regression reproduced 5,063,009 bytes for a 100,001-byte input. Range/islice
   iteration fixes this extra allocation; whole-body decode and concurrency bounds
   still need assessment. Do not treat the whole finding as closed yet.
3. Important: cursors use a one-hour clock/TTL independently of content retention.
   A ten-second store expired while its cursor remained valid. Original body, derived
   page-state and cursor expiries must be bound to one shared deadline.
4. Important: successful empty arrays put no content reference in their envelope,
   losing access to a correctly retained original response.
5. Important: every row advertises source_pointer="" although the actual row path
   may be /data/0 or below a selected nested pointer. Preserve the adapter base
   pointer, caller selection and row index in a truthful source descriptor.
6. Important: promoting verified text/plain JSON after storage duplicates identical
   bytes. Capacity equal to one valid response admits the first copy and rejects
   the second, leaving no returned reference. Choose effective media before a single
   admission. Assigned with finding 1; canonical upstream declaration stays separate.

## Verified strengths and outstanding scope

Reachable content reconstructs exactly through base64. Cursor authentication and
selector binding work, as do warning restoration while storage remains live,
bounded RFC 6901 syntax, wire budgets and focused unknown-name/argument guards.

Diagnostics, normalized search/detail/relationships, dataset tools, validated linked
attachments, PDF extraction, full capabilities and release integration remain pending.
The current implementation_in_progress capability label is accurate.

The root added installed asset retrieval after this review's inspected boundary;
that addition needs its own independent review. Snapshot references alone are not
proof of a completed release installer or historical-release resolver.

## Fix progress (not independent approval)

- Finding 2: 6f0bd1f removes the input-sized descriptor tuple allocation; full
  parsing/concurrency assessment remains open.
- Findings 3 and 4: c2e2326 preserves a top-level source reference/recovery command
  for empty results and binds cursor expiry to both retained objects using the
  store's wall clock. A fake-clock regression expires the older original body
  while page state remains live and now gets cursor_expired. Focused verification:
  33 MCP/presenter/cursor tests passed, Ruff and strict mypy clean.
- Findings 1 and 6: adapter implementer is applying canonical MIME admission and
  single-body retention, with capacity and hostile-header regressions.
- Finding 5: original-body pointer composition is still open.

No finding is independently approved solely because its author reports a fix.

## Scoped independent adapter verification

Root reviewed dab2f61 after the API implementer's fix: canonical MIME admission
rejects the hostile declaration before retention, stored MIME is revalidated on
read, the verified text/plain-JSON route decodes before its single put, and a
separate cache variant prevents representation collisions. All 47 focused
client/store/website tests passed in a fresh root run. Findings 1 and 6 are
addressed for their reproduced scope; this is not approval of all Task 4 work.

The original-body pointer fix 2d84f8c includes a real MCP recovery test for
/data/a~1b/0 and its 148,743-character evidence field. A row pointer is fenced,
its exact executable recovery selector is preserved, and pointer fences count
toward page admission. Finding 5 awaits independent re-review.
