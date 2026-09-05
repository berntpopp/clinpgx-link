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
