# Source downloader review and live verification

Scope: bounded official-source acquisition in `releases/acquire.py`, not release
packaging, installation, or complete export/API equivalence.

Root independently reviewed the initial implementation `8aa530e` and requested
recoverable publication, explicit monotonic admission around synchronous disk I/O,
strict finite timing options, and raw redirect validation before URL normalization.
The different-author GPT-5.6 Sol review of fix `d5accf9` confirmed three fixes but
found that failed rollback could delete the retained recovery inode. It also flagged
the post-commit cleanup deadline boundary and a huge-integer conversion exception.

Fix `2b00774` preserves the old recovery inode if restoration fails and reports a
safe typed recovery failure. It rejects timing integer overflow. Tests inject both
failed rollback replacement and failed rollback unlink, and distinguish these
storage failures from successful rollback. Root independently read the complete
fix diff and its tests and accepted this scoped remediation; this final re-review
was performed by root, not by another child reviewer.

## Explicit commit and failure boundary

The commit is accepted only after the replacement directory fsync and the final
monotonic deadline check. Elapse before acceptance triggers rollback. Cleanup after
acceptance is best effort: a failed recovery-link unlink can leave hidden garbage
but cannot turn an accepted durable destination into a falsely reported acquisition
failure. Kernel filesystem calls cannot be preempted by asyncio, so caller return
latency—including cleanup—is not hard bounded when kernel I/O stalls.

If the storage system also fails the rollback mutation or durability barrier,
automatic restoration cannot be guaranteed. The implementation reports recovery
failure and does not deliberately destroy any retained old recovery inode. This is
not a claim of atomic recovery under arbitrary repeated filesystem failures.

Fresh root verification after the fix:

```text
uv run --frozen pytest tests/unit/test_release_acquire.py tests/unit/test_api_client.py tests/unit/test_catalog.py -q
92 passed in 0.71s
```

## Actual official download

On 2026-09-05, the implemented downloader fetched `data/genes.zip` twice into one
private temporary destination. The second call used the first call's SHA-256 as an
expected digest, exercising verified replacement of an existing file.

| Observation | First download | Verified repeat |
|---|---:|---:|
| Elapsed seconds | 4.5956 | 0.6907 |
| Returned and on-disk bytes | 2,905,541 | 2,905,541 |
| Retrieved at (UTC) | 12:45:20.281779 | 12:45:20.972663 |

Both calls followed the official API URL to
`https://s3.pgkb.org/data/genes.zip`, returned `application/zip`, and recorded
`Last-Modified: Sat, 05 Sep 2026 07:37:36 GMT` and S3 version
`dRIikLcubNSfMS3XLbiDV5QklOFp9vYK`.

Both complete on-disk files were independently hashed as
`ac6ab0512f56fce47812a41adf47f43b551cb226df7b6ed0ca96d4ad2c566bcd`.
The harness asserted exact receipt/Content-Length/file-size agreement, file mode
`0600`, single-link status, and no partial/recovery entries. The client was closed
and its temporary directory removed after verification.

Two sequential transfers are not a latency distribution or proof of all-source
coverage. The first digest was observational, not an independently authenticated
publisher digest; the repeat proves consistency with that observation only.
