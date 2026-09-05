# Preliminary real-agent diagnostic: PGX-10

This is a diagnostic run, **not the primary 18-case acceptance benchmark**.
The terminal capture truncated part of the tool trace, so this run cannot satisfy
the complete-trace requirement even though the agent completed successfully.

## Setup

On 2026-09-05, Claude Code `2.1.261` ran `claude-fable-5` with low reasoning effort
against an actual loopback Uvicorn MCP server. The server used the four-archive
sourced fixture snapshot and the normal live API/website adapters. MCP code was at
`ecef5ef`; concurrent source-contract work did not modify the server or adapters.

The question was exactly the frozen PGX-10 prompt:

> Look up CYP2C19 rs4244285 and the alias CYP2C19*2 separately. Report the resolved
> stable ID for each, or a source-backed ambiguity if they differ.

The agent received no expected IDs or answer key. It ran in a temporary directory
with built-in tools disabled, strict per-invocation MCP configuration, no skills,
no plugins, no hooks, and noninteractive permissions allowing only this server's
MCP tools. The initialization event listed exactly the 13 ClinPGx tools and one
connected MCP server. No subprocess agents were spawned.

## Observed result

| Measurement | Observation |
|---|---|
| Process outcome | Exit 0; completed |
| Runner elapsed | 63.534 seconds |
| MCP tool calls | 22 |
| Model output tokens | 3,725 |
| Input tokens, excluding cache counters | 34 |
| Cache creation input tokens | 31,417 |
| Cache read input tokens | 258,700 |
| Reported total cost | USD 1.074685 |
| Permission denials | None |
| Server shutdown | Clean |

These token counters are separate fields reported by the client, not a sum claimed
to be unique prompt content. The client additionally reported auxiliary
`claude-haiku-4-5-20251001` usage: 940 input and 23 output tokens, USD 0.001055,
included in the total cost. Fable's reported cost was USD 1.07363.

The final answer reported variant `PA166154053` for `rs4244285`, and a separately
modeled haplotype `PA165980635` for `CYP2C19*2`. It cited the live variant search,
variant-to-haplotype endpoint, and haplotype detail, with retrieval timestamps.
It explicitly distinguished entity classes and noted that its evidence came from
live sources after the small local fixture produced no matching records.

This is useful evidence that an agent can discover and use the API/website fallback
through the MCP without a hand-scripted tool sequence. It is not a scored assertion
that every statement in the answer is correct: the incomplete trace prevents the
required full evidence audit.

## Required harness correction before acceptance

The process streamed large tool results into the terminal observer; a polling
response exceeded that observer's output cap. The partial capture was retained in
the ignored execution workspace, but missing bytes cannot be reconstructed from
the final answer. The acceptance harness must capture complete bounded trace data
directly to a run artifact before printing only a compact terminal summary.

Run all 18 cases with the frozen assertions and complete traces. Keep this
preliminary run separate; do not relabel it as a primary run, silently rerun a
failure, or remove unavailable cases from the denominator. The fixture snapshot
used here is not a substitute for the required full installed release.
