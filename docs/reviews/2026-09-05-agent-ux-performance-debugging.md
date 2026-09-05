# Agent usability and performance: evidence-backed debugging

Date: 2026-09-05. Audience: ClinPGx Link maintainers.

## Executive finding

The next improvements should repair the **handoff between discovery and retrieval**, make targeted responses smaller, and preserve specific safe recovery instructions. Increasing concurrency or adding more API wrappers will not repair those interfaces.

This report combines the completed mounted Opus interim batch, independent retained-response reproductions, source inspection, and current primary documentation. It is a diagnosis and prioritized implementation handoff, **not a claim that the defects below have been fixed or that UX exceeds 90/100**. It supplements the existing approved plan; no further spec/plan review was commissioned.

## What was actually measured

The frozen interim server ran commit `cfdd37b998599ec9376ef13ff588e03c03dbc96d`, snapshot `sha256:7709521451918131745b22a3cb3718d168c3845074bef396cb8e1600efd8f6d3`. Twelve unchanged tasks ran with four concurrent Claude consumers. The client-reported model was `claude-opus-5`.

All twelve traces passed transport integrity checks. There were **214 tool calls and 22 errors**, about **488.7 seconds batch wall time**, and **$14.6207155 reported cost**. These are not twelve verified factual passes.

| Aspect | Lowest observed self-rating /100 |
|---|---:|
| Correctness confidence | 80 |
| Completeness | 70 |
| Discoverability | 50 |
| Token efficiency | 40 |
| Speed | 85 |
| Error recovery | 45 |
| Provenance clarity | 80 |
| Overall usability | 60 |

Unobserved ratings remain null. No aspect clears the strict >90 gate. Self-reported confidence is not independently established correctness.

The prior batch used 222 calls and 33 errors: fewer calls/errors now, but no broad UX victory. Baseline cache was warm; the interim cache started cold and warmed across concurrent tasks. Task duration includes model work, tool work and other delays. Do not attribute the batch timing difference to server performance.

Raw traces total 5,462,911 bytes; this is **not model token consumption**. Keep actual model usage, serialized result bytes, trace bytes and estimated tokens separate.

The retained live-content backup has SHA-256 `11823771c4405e61007778b7455c13cf434bec7f67db926cc10beb46297ca9cd`. Detailed tool-call identifiers and raw traces remain private, outside tracked documentation. Source assertions and independent grading are still pending.

## Confirmed root causes and concrete changes

### 1. Discovery and selection use different pointer roots

The API adapter unwraps the upstream envelope to `data`, but retained-source discovery operates on the original envelope. Thus discovery exposes `/data/0/id` while scalar selection expects `/0/id`. For object results the wrong root can return successful all-absent selections and an original locator containing `/data/data/...`.

Observed in guideline and linked-literature tasks, with recurrence in other summaries. Independent reproduction on current checkout `0de30ef` using the retained annotation body confirms:

- `/0/id` selects numeric annotation ID `1454052260`.
- `/0/literature/id` selects internal literature ID `15178564`.
- `/data/0/id` fails array-token validation.
- A wrong-root object pointer can report absence instead of explaining the coordinate system.

Relevant code: [adapter selection](../../clinpgx_link/mcp/adapter_selection.py), [source discovery](../../clinpgx_link/mcp/facade.py), [API decoding](../../clinpgx_link/api/client.py).

**Recommended change:** expose an explicit selection base and reusable selector coordinates alongside original-source coordinates, tied to the exact response/view/content identity. Teach the distinction in the actual tool schema, not only README. Retained bodies can have multiple adapter interpretations; do not guess the conversion from a bare content reference.

Only diagnose a likely duplicated prefix when the response mapping provides evidence. Do not silently strip `/data`: a real source object can legitimately contain another `data` property. Distinguish a coordinate warning from proven field absence.

**Regression gate:** discovery → unchanged returned selector → successful value retrieval on API, website and record routes; nested real `data` keys, missing fields, arrays and escaped keys remain correct. Original locators must retrieve the same raw value.

### 2. Annotation IDs are hidden by the wrong profile

Variant annotation uses the generic identity profile, whose required `id` must be a string. The retained row instead has numeric `id` and string `accessionId`. Independent reproduction reports it unprofiled; compact results consequently hide actionable identity and force structure exploration.

The numeric detail route correctly rejects a PA accession. Relaxing that route would violate the captured contract.

Relevant code: [adapter profiles](../../clinpgx_link/mcp/adapter_selection.py), [record arguments](../../clinpgx_link/mcp/record_tools.py), [API route binding](../../clinpgx_link/api/registry.py).

**Recommended change:** introduce a verified family-specific profile with explicit identity roles: stable accession, numeric detail-route ID, and separately named literature cross-references. Advertise the per-family detail identifier and provide a validated search-to-detail continuation. Examine other numeric-ID families without assuming they share this exact shape.

**Regression gate:** real numeric annotation shape profiles correctly; returned detail ID binds to the captured route; PA accession remains distinct; booleans, invalid types and shape drift remain unprofiled. No phenotype or other biomedical inference.

### 3. Capabilities omit an existing useful relationship workflow

Capabilities explain searches but not the implemented pair-versus-connected-object relationship grammar. The guideline task resolved a gene and chemical, then guessed the pair route to find the guideline annotation.

Relevant code: [capabilities](../../clinpgx_link/mcp/facade.py), [search contracts](../../clinpgx_link/mcp/search_contracts.py), [relationship dispatch](../../clinpgx_link/mcp/record_tools.py).

**Recommended change:** describe the existing gene/chemical pair route, required second identifier, verified result types and dependent steps. Derive discovery and validation from one closed contract. Improve short tool descriptions with “use when,” identifier semantics, and the next valid step. Do not invent unsupported gene filters for guideline search.

**Regression gate:** every advertised relationship example binds to the dispatcher; unsupported combinations are excluded; capabilities remain within the fleet discovery budget. Examples are syntax, never evidence of a current match.

### 4. Small answers have disproportionately large envelopes

The controller independently re-counted actual text bytes delivered in four trace results:

| Requested content | Text bytes | Provenance occurrences |
|---|---:|---:|
| Three selected fields | 5,846 | 9 |
| Eight selected scalars | 9,732 | 22 |
| Thirty-key structure page | 23,337 | 61 |
| One short PMID scalar | 2,043 | 2 |

These are examples, not distribution percentiles. The selection implementation fences both requested and original pointers as well as string values. Structure pages fence both keys and pointers. Several paths accept `response_mode` but do not use it to reduce this shape.

**Important rejected proposal:** shared provenance references are attractive but violate the currently binding fleet v1.1 normative object, which requires provenance inside each external-text fence. The independent diagnostic report suggested this conditionally; this research resolves that condition against changing the fence representation. Removing the JSON text mirror also conflicts with the fleet contract.

Relevant code: [text fencing](../../clinpgx_link/mcp/untrusted_content.py), [selection rendering](../../clinpgx_link/mcp/adapter_selection.py), [content rendering](../../clinpgx_link/mcp/facade.py). Governing external contract: GeneFoundry Response-Envelope Standard v1.1, normative object and mirrored-content sections, inspected in the sibling router repository.

**Recommended change:** make minimal mode omit redundant optional descriptors, retain one actionable coordinate where the contract permits, use requested selection order to avoid unnecessary repeated labels, and offer richer descriptors in standard/full. Preserve each emitted fence unchanged, exact source reachability, explicit pagination and limits. Any public shape migration must respect compatibility requirements.

**Regression gate:** measure exact serialized bytes for one/eight/twelve short selections and a thirty-key page before/after; require a material reduction with identical retrieved evidence and hostile-content safety. Set achievable byte budgets from the prototype, not unsupported promised percentages. Separately test model usability: a smaller but ambiguous response is not an improvement.

### 5. Error sanitization removes safe, useful instructions

A base64 request with a nonempty pointer is correctly rejected. The reader has a fixed explanation, but the envelope replaces it with generic guidance. The asset branch also fails to pass its recoverable reference, sending the agent back to capabilities.

Relevant code: [content reader](../../clinpgx_link/content/reader.py), [error envelope](../../clinpgx_link/mcp/envelope.py), [asset façade](../../clinpgx_link/mcp/facade.py).

**Recommended change:** closed code-owned subtypes/messages for common mistakes, plus a validated same-reference recovery command. For scalar batches, identify the offending selection by bounded numeric index and closed reason where useful. Never echo arbitrary pointers, keys, URLs or exception bodies.

**Regression gate:** asset and content references both produce executable recovery; invalid references never escape; selected containers, malformed indices and nonempty base64 pointers remain distinct; the six public error codes remain unchanged.

## Primary guidance and applicability

Anthropic recommends task-oriented tools, concise useful outputs, clearer descriptions, and evaluation of calls, errors, runtime and token use. It also cautions that agent feedback can be misleading. This supports the trace-first changes above, not adding endpoint wrappers indiscriminately. [Writing effective tools for agents, Anthropic, 2025-09-11](https://www.anthropic.com/engineering/writing-tools-for-agents).

Google ADK derives model-facing declarations from tool names, descriptions and parameter definitions, and recommends simple, meaningful interfaces. Apply this to the emitted MCP schema and verified identifier meanings. Its Python declaration machinery is not a requirement for this server. [Function tools, Google ADK, living documentation](https://adk.dev/tools-custom/function-tools/).

MCP distinguishes tool execution failures, which can provide actionable feedback, from protocol failures. It supports structured content and optional output schemas; publishing an output schema entails conformance obligations. Our fleet additionally requires the identical JSON text mirror. Retain interoperability rather than counting its removal as a token optimization. [Tools, MCP 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).

Google documents that synchronous processing blocks concurrent async tools. That supports offloading blocking storage work and bounding actual worker lifetimes, while keeping independent reads concurrent. It does not justify unlimited fan-out or bypassing the existing upstream scheduler. [Tool performance, Google ADK](https://adk.dev/tools-custom/performance/).

Client-side filtering and code execution can reduce model-visible context, but are optional host features, not portable server guarantees. ADK offers `tool_filter`; Anthropic describes discovering tools and processing intermediate results outside model context. Do not make ordinary clients depend on these mechanisms or add arbitrary execution to this read-only MCP. [Google MCP tools](https://adk.dev/tools-custom/mcp-tools/), [Anthropic code execution with MCP, 2025-11-04](https://www.anthropic.com/engineering/code-execution-with-mcp).

Anthropic recommends isolated trials, deterministic grading where possible, calibrated model grading and transcript inspection. Google separately supports trajectory matching and final-response evaluation; permissive trajectory matching permits extra calls and cannot alone prove efficiency. Preserve alternative valid paths while measuring redundant calls separately. [Anthropic agent evaluations, 2026-01-09](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents), [Google evaluation criteria](https://adk.dev/evaluate/criteria/).

## Protocol drift: separate compatibility work

The official versioning page identifies **2026-07-28** as current. The locked local installation is FastMCP **3.4.7**, MCP SDK **1.29.1**, with `LATEST_PROTOCOL_VERSION=2025-11-25`. This is an observed documentation/runtime gap, not proof that existing mounted consumers fail.

The new revision uses per-request metadata and `server/discover`, while describing compatibility with older initialization-based clients. Its tool specification also recommends deterministic tool ordering for caching. Record the negotiated protocol in each benchmark and test the actual supported client/server combinations before claiming current-version compatibility. Do not mix a protocol/dependency migration into a supposedly identical UX candidate. [Current version](https://modelcontextprotocol.io/docs/2026-07-28/learn/versioning), [Compatibility](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning), [Current tools specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools).

## Implementation order and verification

1. Correct pointer handoff and safe recovery together through shared helpers, with RED/GREEN regressions.
2. Correct numeric identity profiles and publish relationship/detail contracts from a single source of truth.
3. Prototype smaller minimal shapes within the unchanged fence contract; verify source equivalence and exact byte savings.
4. Independently review the new timing/admission implementation at `0de30ef`; do not assume it fixes context or discoverability.
5. Finish the production cross-model harness and rerun the frozen twelve-task development batch on Opus and Terra/high with identical candidate data/configuration.

For speed, separate local/cache/live tool measurements, client-observed duration and complete task duration. Use controlled cold and warm runs, fixed concurrency and source rate limits; collect percentiles only with adequate samples. Keep raw query/body data out of production telemetry. A decomposition into queue, execution, serialization/transit and model time is a measurement framework, not a claim that the current traces expose every component.

The Task 5 implementer reports 914 passing tests and real-HTTP cancellation/lifetime checks; the commit and clean tracked scratch state were verified, but independent task-scoped review remains pending. Those changes were **not included** in the scored interim batch.

Terra's one-tool mounted smoke passed 29 assertions with effective client identity `gpt-5.6-terra/openai/high`, a paired successful MCP result and completed final answer. Its trace and summary hashes were independently checked. It used the older server and is **not a scored twelve-task Terra evaluation**; upstream backend identity remains unknown.

Maintain separate model/self/judge scores, source-grounded answer checks, frozen heldout protection and the strict >90 threshold for every observed aspect with coverage. Retain failed attempts. After development success, run the already specified three validation batches; do not select the luckiest run. Original eighteen-case acceptance, complete source coverage/fallbacks and release gates remain required.

## Limits and research completion

This pass verified five interface defects, not every API family or all twelve final answers. It did not change production behavior, run another paid scored batch, or promise a score improvement. No new spec/plan review was run.

The research used primary MCP specifications, Anthropic engineering guidance and Google documentation, then checked consequential claims against actual source and retained responses. Community discussions were discovery signals only, not normative evidence. Documentation was accessed 2026-09-05; Google pages did not expose publication dates. Further broad searching is unlikely to change the immediate fix order; unresolved work is now implementation, compatibility probing and controlled evaluation.

Superpowers systematic debugging enforced reproduction before recommendations; Deep Research enforced primary-source comparison and explicit uncertainty. The planning tool was unavailable in this environment; the research sequence and claim/gap ledger were retained privately instead.
