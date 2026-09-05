# Foundation and content-reader scoped review

Date: 2026-09-05  
Reviewer model: `gpt-5.6-sol`

## Scope and evidence

This review is limited to the Task 1 assigned files in `100e7d4..eecc56b` and the
pure content-reader files in `a4e8538^..a4e8538`. Later `ContentStore` work and the
concurrent website-registry normalization are outside scope. The coverage manifest's
website-operation hash is therefore treated as provenance for its captured snapshot,
not as a requirement to match the later `0f9d10a` registry.

Reviewed inputs:

- Task 1 brief: `b4270cdb7b5770b662fdd4c8951bacdfa9268e2d6910693aecd7f5b6eb0a9d5e`
- Task 1 report: `178eeecb0c160ca5623ee03cfc01608327c0fa99f5f2bf387a54728bd585986b`
- Foundation review diff: `05fb3e414b6342b650d89fb8ec6e47905e2cd535d9087609b3546f5510fd5049`
- Content-reader report: `31ee6b4f7d849818538c1594a9d3af8a68ca4c9703ce6439ff0675274e947a17`
- Content-reader review diff: `90f70a1e802485bf7653e025d62a3060732d037b18183cf04ce162da768845ec`
- Binding source-access contract: `f83dd23572fb4c498578d6ca9070b5c1527c9b846574e6e2651c55f55f50a248`

The assigned source files are unchanged from their reviewed commits. I did not rerun
the already evidenced suites. Focused probes checked log reflection, host validation,
reader digests/byte representations, long-key pointer behavior, and inventory shape.
Structural inventory results were: 34/34 unique API operations with byte-matching
OpenAPI digest, 120/120 unique download IDs, all 120 datasets discovered but none
claimed acquired/parsed/searchable/field-complete/MCP-retrievable, ten unique website
families with no fabricated MCP retrieval, and the broken VIP route retained with two
evidenced fallbacks.

## Task 1 — runtime foundation and source contracts

### Spec-compliance verdict: NOT APPROVED

The domain boundary, exact six-code taxonomy, bounded source origins and numeric
settings, immutable captured inventories, and conservative 34/120 coverage accounting
conform. One Important logging violation remains.

### Quality verdict: NOT APPROVED

No Critical findings. The Important logging issue is directly exploitable by an
incorrect or future caller. One Minor configuration-validation issue should also be
closed before the HTTP boundary consumes these settings.

### Important — the payload-free logger filters keys but reflects unrestricted values

`clinpgx_link/logging_config.py:18-50` preserves `event`, `operation`, `source`,
`request_id`, `release_tag`, and other string-valued fields without validating their
origin, grammar, or length. `bind_request_id` likewise binds any string at
`clinpgx_link/logging_config.py:59-61`. A focused probe placed the same credential in
`event`, `operation`, `source`, and `request_id`; the JSON log contained it verbatim.
The existing test at `tests/unit/test_foundation.py:235-263` checks only that unsafe
*keys* are dropped, so it gives a false sense of credential-reflection coverage. This
violates `AGENTS.md:34-36` and Task 1's no-credential-reflection requirement.

Required correction: validate or derive every retained field before rendering. Event
names and operation/source identifiers should come from fixed developer/registry
vocabularies; correlation IDs and release/snapshot identifiers need strict character
and length bounds; numeric/boolean fields need type bounds. Invalid values should be
dropped or replaced with a fixed marker, never echoed. Add a table-driven test that
injects a secret and hostile name into every retained string field, including bound
context values, and asserts neither appears in rendered output.

### Minor — `allowed_hosts` does not validate exact host names

`clinpgx_link/config.py:129-134` rejects a few metacharacters but accepts whitespace,
ports, backslashes, and malformed DNS names. Focused examples accepted
`" example.org "`, `"example.org:443"`, `"example\\evil.org"`, and `"a..b"`.
This is fail-closed for many middleware implementations, but it does not meet the
setting's documented exact-host contract and can produce surprising deployment
failures.

Required correction: validate canonical DNS names and IP literals (including a clear
IPv6 representation), reject rather than normalize whitespace/ports, enforce
lowercase/IDNA policy explicitly, reject duplicates, and add focused tests for those
cases before wiring the Host guard.

## Task 4 reader slice — lossless bounded source content

### Spec-compliance verdict: NOT APPROVED

The reader correctly preserves root original bytes, provides progressing UTF-8 text
and byte chunks, validates bounded RFC 6901 syntax, and rejects duplicate keys,
non-finite numbers, invalid Unicode and undecodable JSON while keeping malformed raw
bytes retrievable. Two Important output-contract violations remain.

### Quality verdict: NOT APPROVED

No Critical findings. In addition to the two spec issues, structure discovery can
emit a child pointer that the same reader refuses and can construct an output far
beyond the intended response bound.

### Important — scalar structure descriptors omit the selected scalar digest

The binding contract requires scalar `type/length/digest`. `_describe` at
`clinpgx_link/content/reader.py:69-80` returns no digest and gives no length for
null/boolean/number. The `sha256` added at `clinpgx_link/content/reader.py:153-156` is
the digest of the complete raw source body, not the selected scalar. For
`{ "x" : "\\u00e9" }`, structure at `/x` reported the body's digest
`eefd1076...`, while the selected UTF-8 scalar digest is `4a99557e...`.
`tests/unit/test_content_reader.py:52-67` checks only type and length, so it does not
catch the missing/wrong digest.

Required correction: retain `source_sha256` for whole-body identity, but put a
separately and deterministically defined selected-value digest and length on every
scalar descriptor. Add pointer-selected string, number, boolean and null assertions
that distinguish body and selected-value digests.

### Important — pointer-scoped `base64` is derived reserialization, not original bytes

The binding contract defines `base64` as independently chunked slices of ORIGINAL
bytes. The reader's own docstring admits a different behavior at
`clinpgx_link/content/reader.py:105-110`, and `clinpgx_link/content/reader.py:167-180`
decodes and canonicalizes a pointer-selected value before base64 encoding it. The test
at `tests/unit/test_content_reader.py:127-141` enshrines that incompatible behavior.
Although `derived=True` avoids silent substitution and root base64 remains exact, a
publicly valid `pointer + base64` request no longer has the specified byte semantics.

Required correction: reject nonempty pointers for `base64` unless the implementation
can map the selected JSON value to an exact span in the original serialization.
Derived canonical JSON/string bytes, if retained, need a distinct representation name
and contract; they must not overload the exact-byte `base64` representation. Add a
fixture with escapes/whitespace proving concatenated base64 chunks reproduce source
bytes exactly.

### Important — structure discovery advertises unusable, unbounded child pointers

`clinpgx_link/content/reader.py:145-153` copies each source key into both `key` and
`pointer`, while `_select` rejects every pointer longer than 4096 characters at
`clinpgx_link/content/reader.py:52-53`. A long JSON key therefore produces a child
pointer which the same API cannot traverse. The count bound does not bound serialized
size either: a one-item page for a 100,000-character key serialized to 200,410 bytes
in a focused probe. This breaks lossless pointer discovery and can exceed the 25,000
token response budget before the future MCP fence can make the advertised child
usable.

Required correction: never emit a pointer the reader will reject. Define and test a
bounded field-name/nesting policy at acquisition/decoding with explicit exact-base64
recovery, or introduce a reviewed bounded selector mechanism for oversized names.
Test the boundary at maximum pointer length and a single oversized key; a generic MCP
truncation must not turn it into an undiscoverable field.

## Readiness

**NOT READY for foundation acceptance.** The log-value reflection is the immediate
foundation blocker. The reader should not be integrated into the public MCP tool until
the selected-scalar digest, exact-byte base64 semantics, and advertised-pointer bound
are resolved. No issue was found with the honest 34/120 accounting, typed domain
response/error boundary, strict invalid-JSON handling, ordinary pointer escaping, or
root original-byte reconstruction.
