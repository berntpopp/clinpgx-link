# Opus mounted-MCP baseline: failed target

On 2026-09-05, actual Claude Code 2.1.261 resolved `opus` to `claude-opus-5`
and used the local HTTP MCP with exactly its 13 tools. No built-in tools or
unrelated MCP servers were available. This is a development UX round, not the
frozen 18-case acceptance benchmark or validated installed-release acceptance.

## Results

| Subjective category | Opus score / 100 |
| --- | ---: |
| Correctness confidence | 80 |
| Completeness | 80 |
| Discoverability | 50 |
| Token efficiency | 50 |
| Speed | 80 |
| Error recovery | 50 |
| Provenance clarity | 80 |
| Overall usability | 60 |

No category exceeds the requested 80 threshold. Scores are the evaluator's own
ratings, not independent correctness proof. It received no target score or answer
key. Its main complaints were undiscoverable JSON filters, generic error recovery,
and many separate calls to read one deferred record's scalar fields.

| Measured item | Value |
| --- | ---: |
| Runner elapsed | 313.089 seconds |
| MCP calls | 42 |
| Error tool results | 16 |
| Client input tokens | 40 |
| Cache creation input tokens | 95,600 |
| Cache read input tokens | 991,782 |
| Output tokens | 21,810 |
| Reported total cost | USD 1.998897 |
| Complete stdout trace | 536,401 bytes |
| Process exit | 0 |

Cache counters are separate client accounting fields, not unique prompt tokens.
The runner's successful trace-integrity status is not a task or UX pass.

## Independent answer and trace audit

- The snapshot, archive hash, CYP2C19 row ID, symbol, and alternate-name field
  match the mounted SQLite records. Variant/haplotype IDs in the final response
  are distinct; their upstream result evidence remains in the full trace.
- The PharmCAT child record is real: ordinal 25400, pointer
  `/11/diplotypes/36`, ID
  `record:5eb2d995e33ed4a89ff28b4dd33f85f51f71eb73444c6a50c9816a38464b51cb`.
  Its stored `diplotypekey` is `{"*2":2}`. Opus omitted the numeric value, so
  the requested complete-field assertion fails despite its opening completion
  claim. A separately audited parent record also contains this child.
- There were 42 actual tool calls, not the claimed within-40-call execution.
  The harness's separate hard safety ceiling was 45 calls.
- The trace was independently replayed through the stricter final harness's
  integrity checker: no parse, model, server/tool allowlist, unmatched-call,
  missing-result, truncation, or digest failures were found.
- Local `retrieved_at` values represent this diagnostic run's re-admission of
  earlier downloaded files, not fresh upstream transfers or authenticated
  release installation. Opus's description of them as installation times is
  too strong. Unknown upstream publication dates must not be fabricated.

## Reproduction identity and retained evidence

- Prompt: `tests/eval/opus-ux-round1.md`, SHA-256
  `bcb79c55f0a33a0f69b944a31925730bf6130a467784c0f83a245b5bf74a8be2`.
- Repo HEAD at launch: `0db98fd`; MCP implementation: `a5cb6f4`.
- Loaded precommit harness SHA-256:
  `820c0ce978e97a3be300c8a9b54837ff775b9093af7a62393c70e6e2868ec79b`.
  Later integrity fixes landed in `91167ab`; the run is not attributed to that
  later executable. The later checker was used only for independent replay.
- Trace SHA-256:
  `b4d211e1bef2ae8d0f38ca2cad84722afdcb912957e85e4e2a1a6b790c646969`.
- Diagnostic snapshot:
  `sha256:f780aaf2ff3d34ef39a5c39ab919566e01d11bbfe6a51912ac41c04019a365b6`.
  Seven real archives, 576,427 indexed records. Its diagnostic tag is not an
  approved production release identity.
- Complete raw events, summary, and stderr are retained in the ignored plan
  workspace as `opus-ux-round1.jsonl`, `opus-ux-round1-summary.json`, and
  `opus-ux-round1-stderr.log`. They are machine-local, not portable committed
  evidence. Full evaluation artifact publication remains a handoff requirement.

## Follow-up

Reproduce and fix the PharmCAT internal search errors; make supported JSON fields
discoverable and small complete records economical to retrieve; provide actionable
typed recovery. Preserve untrusted-source fencing and exact provenance rather than
adopting the evaluator's suggested plain-string output without a safety contract.
Repeat the baseline and run the pre-frozen held-out task set after verified fixes.
