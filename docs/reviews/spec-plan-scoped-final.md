# Scoped final review: source-access findings 1 and 3

**Verdict: READY for foundation implementation within the reviewed scope.**

**Model identity:** `gpt-5.6-sol`

This design-only review covers only findings 1 and 3 from
`docs/reviews/own-model-spec-plan-round2.md`, against the current source-access
contract's Local discovery section and the current plan's shared `BoundRequest`,
`ApiRegistry.bind`, and `DatasetRepository` interfaces. Release findings 5–8 and
all other review findings were outside scope. No implementation or tests were
assessed.

## Finding 1 — ADDRESSED

Validated form data now has a complete shared-interface path:

- `BoundRequest` carries `form` separately from query `params` and carries the
  selected representation
  (`docs/superpowers/plans/2026-09-05-clinpgx-link.md:68-75`).
- `ApiRegistry.bind` accepts `form_parameters` and `representation` and returns the
  complete `BoundRequest`; `ApiService.call` and `ClinPGxClient.request` accept the
  same values (`plans/2026-09-05-clinpgx-link.md:102-124`).
- The binding contract rejects unknown path, query, and body fields, restricts form
  use to the verified read-only Infobutton operation, and allowlists representation
  changes (`docs/superpowers/specs/2026-09-05-source-access-contract.md:30-40`).
- The local-discovery addendum explicitly requires all validated query and form
  values to travel in one `BoundRequest`; the client never receives unvalidated
  form data (`source-access-contract.md:126-128`).

The previous three-element tuple and missing body channel are gone. No unresolved
precedence or unknown-field path remains in the reviewed interface.

## Finding 3 — ADDRESSED

Local filter, membership, join, and public-result semantics are now concrete:

- `search_records(source=auto)` selects installed local discovery for broad text and
  aliases, while explicit source selection cannot silently switch representation
  (`source-access-contract.md:98-103`).
- Exact-cell versus declared member-token matching is defined, including
  source-specific CSV/delimiter semantics and an error for unknown semantics
  (`source-access-contract.md:105-114`).
- The accepted canonical filter keys, AND behavior, identifier/name/relation
  semantics, and literal AND-token FTS behavior are fixed
  (`source-access-contract.md:116-121`).
- Callers discover entity/filter/operator/result-type support through
  `get_server_capabilities.result.local_search`, and dataset field match/tokenizer
  semantics through `get_dataset.result.members[].fields[]`
  (`source-access-contract.md:130-133`).
- `get_related_records(source=download,
  result_type=evidence|allele|literature)` is the concrete public joined call. Its
  standard row plus `join` shape, provenance, pagination unit, and source-row
  preservation are fixed (`source-access-contract.md:122-140`).
- `DatasetRepository.search_entities` and `.related` provide the matching internal
  interfaces, including pagination and expected snapshot identity
  (`plans/2026-09-05-clinpgx-link.md:130-146`).
- Required MCP examples cover multigene/multidrug membership, evidence and allele
  children, reverse rows, Swissmedic labels, and non-CPIC/DPWG guidelines
  (`source-access-contract.md:137-140`).

The public `source` selector is resolved at the boundary before the local repository
call, so its absence from `DatasetRepository.search_entities`/`.related` is not an
interface gap. Public entity-type selectors likewise guide boundary resolution;
repository `record_id` is the validated canonical row identity. No new important
breakage was identified in the scoped changes.

## Reviewed input hashes

```text
b096e2570f47502153c1d697245fde69849f70523c164f3790bbb5b3e0aa18ab  docs/reviews/own-model-spec-plan-round2.md
f83dd23572fb4c498578d6ca9070b5c1527c9b846574e6e2651c55f55f50a248  docs/superpowers/specs/2026-09-05-source-access-contract.md
d322a73ceefadba440d324ad206dbd6df99f29036927c6d382df3105ea515428  docs/superpowers/plans/2026-09-05-clinpgx-link.md
```
