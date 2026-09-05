# Spec/plan review resolutions

The Fable availability probe succeeded, but its actual review request returned HTTP
429/spending-limit failure before consuming inputs. The user explicitly authorized
own-model or AGY substitution. No Fable review success is claimed.

Independent round one and round two ran through OpenAI Codex CLI v0.153.4 with
explicit `--model gpt-6-astra`, `model_reasoning_effort=high`, read-only sandbox,
ephemeral sessions, and no delegated agents. These configuration facts were observed
in the CLI startup banner by the controller; the reviewer itself could only identify
its instruction-level GPT-6 identity. Session IDs:

- Round one: `01a070a4-1663-7192-9a71-62d02c0753d6`.
- Round two: `01a070ad-8771-7eb3-a6ef-0c2c729a7585`.

Reports retain the reviewers' own verdicts and input hashes. Round two checked bytes
before the final BoundRequest/local-query corrections; its unresolved findings were
subsequently checked independently by gpt-5.6-sol in `spec-plan-scoped-final.md`.

| Finding | Resolution | Design verification |
| --- | --- | --- |
| 1 website/body public interfaces | Thirteen frozen tools, website namespace, BoundRequest form/representation channel | Scoped final: ADDRESSED |
| 2 oversized retrieval dead ends | Pointer discovery, scalar/byte chunks, retained references and explicit cap recovery | Round two: ADDRESSED |
| 3 local discovery/joins | Canonical filters, member-token matching, discoverable capabilities, repository/public joined-row contract | Scoped final: ADDRESSED |
| 4 cursor/snapshot races | One pinned immutable handle; expected_snapshot passed into count/select | Round two: ADDRESSED |
| 5 historical unparsed bytes | Exact archive/member SQLite BLOB retention, chunk retrieval, cache-loss test | Controller inspected revised release unit and binding addendum; addressed |
| 6 health/activation identity | Candidate-only builder; installer activation; production pinned readiness/restart | Controller inspected mode/activation contract; addressed |
| 7 volatile reproducibility | Frozen acquisition receipt input, observations external, toolchain image pin | Controller inspected reproducibility boundary; addressed |
| 8 predecessor bootstrap | Independently pinned seed with direct predecessor, stage without recursion then validate activation | Controller inspected stage/activate/rollback sequence against fleet contract; addressed |
| 9 explained gaps falsely pass | Independent accounting and usable-coverage gates; unavailable stays incomplete | Round two: ADDRESSED |
| 10 weak benchmark acceptance | Frozen 18-case sourced manifest, all cases complete, critical assertions and 90% rubric threshold | Round two: ADDRESSED |

Foundation implementation may proceed. These are reviewed DESIGN requirements,
not claims that the tools, release pipeline or benchmark are implemented or passing.
Implementation acceptance must supply the tests and live evidence named by each
resolution; the requirement ledger remains incomplete until those results exist.
