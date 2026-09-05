# Task 4 early content-reader slice

Owner: main. Files: clinpgx_link/content/{__init__,reader}.py and
tests/unit/test_content_reader.py. Pure bounded source reader; not yet MCP-exposed,
not a complete content store, no PDF extraction or cache retention implementation.

RED: python3 -m pytest tests/unit/test_content_reader.py -q -> 16 failed because
clinpgx_link reader module did not exist. Implemented read_content with source/body
digest, structure descriptors/RFC6901 pointers, scalar Unicode slices and original
byte chunks. After foundation exceptions became available, 16/16 passed.

Second RED: malformed JSON fixture tests ->4 failed,18 passed (duplicate keys,
NaN/Infinity, surrogate incorrectly accepted). Added strict loss-preserving decoder;
original byte retrieval remains available for malformed documents.

GREEN: uv run --frozen pytest tests/unit/test_content_reader.py -q ->22 passed .10s.
Lint: uv run --frozen ruff check clinpgx_link/content tests/unit/test_content_reader.py
->All checks passed. Format applied with frozen Ruff. Strict mypy on content ->
Success in2files (unused global override note only).

Self-review: exact source bytes and selected-derived bytes are distinguished; raw
text intentionally leaves fencing to MCP boundary. Offsets beyond end fail, exact
end is explicit terminal, binary text is rejected. Decode limits are owned by
acquisition; callers must fence output and enforce total response/fence count.
Next acceptance: independent review, then wire this reader to immutable references
and actual MCP tool; no full-task completion claim.

## Reader review corrections — round 1

Implemented the three Important reader findings against the clarified large-content
contract. Test-first RED was:

```text
$ uv run --frozen pytest tests/unit/test_content_reader.py -q
10 failed, 22 passed in 0.11s
```

The failures demonstrated that pointer-scoped base64 was still derived, scalar
descriptors lacked selected-value metadata, over-limit child pointers were advertised,
and structure pages exceeded 32 KiB. The implementation now:

- rejects nonempty pointers for base64 with explicit empty-pointer original-byte
  recovery and contains no selected-value serializer branch;
- distinguishes `source_sha256` from every scalar's selected `sha256`, declaring
  decoded-string or canonical-JSON-scalar digest representation and matching length
  unit;
- refuses child pointers beyond 4096 characters or 128 segments with typed
  `response_too_large` recovery; and
- constructs progressing structure pages under a 32 KiB compact UTF-8 JSON descriptor
  budget, with an explicit error when even the first item cannot fit.

One first GREEN attempt had 31 passes and one test failure because the test incorrectly
expected a 49 KiB original body in the default 4 KiB chunk. The test was corrected to
reconstruct every independently encoded chunk; the exact-byte guarantee was not
weakened. Final focused verification:

```text
$ uv run --frozen pytest tests/unit/test_content_reader.py -q
32 passed in 0.12s
$ uv run --frozen ruff format --check clinpgx_link/content/reader.py tests/unit/test_content_reader.py
2 files already formatted
$ uv run --frozen ruff check clinpgx_link/content/reader.py tests/unit/test_content_reader.py
All checks passed!
$ uv run --frozen mypy clinpgx_link/content/reader.py
Success: no issues found in 1 source file
```

The descriptor budget is measured as compact UTF-8 JSON with non-ASCII characters
emitted directly. The future MCP boundary still owns its stricter 25,000-token budget
and fencing overhead. String descriptor length is in decoded Unicode characters while
its digest is over UTF-8 bytes; canonical JSON scalar length is in bytes, and both
units are explicit.
