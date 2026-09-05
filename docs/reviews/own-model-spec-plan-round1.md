**Verdict: REVISE.**  
**Reviewer: GPT-6 (Codex), using the explicitly authorized own-model fallback.** This was a read-only review; no files were edited, agents spawned, or implementation tests claimed.

The hybrid approach is justified, but the plan does not yet specify a complete, consistently implementable retrieval and release contract. P1 findings below should be resolved before implementation; P2 findings before acceptance.

1. **P1 — Website fallbacks have no frozen public interface.**  
   [Spec, MCP tool surface](/home/bernt-popp/development/clinpgx-link/docs/superpowers/specs/2026-09-05-clinpgx-link-design.md:154) says parameter schemas are frozen in the plan, but [Task 4](/home/bernt-popp/development/clinpgx-link/docs/superpowers/plans/2026-09-05-clinpgx-link.md:237) still defers website/document tool additions. `get_api_data` promises documented operations, while required allele, frequency, AMP, prescribing and attachment routes are outside OpenAPI. Implementers can satisfy their module descriptions without making those adapters callable by an agent.

   **Required:** Freeze tool signatures, operation namespaces, source selectors and the family→tool→adapter mapping in Task 1. Include bounded document retrieval and representation selection. If Infobutton POST is verified, extend `ApiService.call`/`ApiRegistry.bind` to carry its body; their current signatures cannot. Account explicitly for the tested JSON-LD representation. Test every mapping through MCP, not merely registry membership.

2. **P1 — Oversized content can reach a permanent retrieval dead end.**  
   [Spec, pagination/budgets](/home/bernt-popp/development/clinpgx-link/docs/superpowers/specs/2026-09-05-clinpgx-link-design.md:186) proposes a smaller field/pointer request, but a pointer cannot subdivide a scalar string. The [measured exports](/home/bernt-popp/development/clinpgx-link/docs/research/api-vs-download-decision.md:66) contain a 148,743-character field. API object pointers are also absent from the frozen interfaces. Moreover, local pagination cannot help when acquiring the complete upstream response exceeds the HTTP body cap.

   **Required:** Define pointer discovery, nested-array pagination and digest-bound scalar chunks or an equivalent tool-readable artifact route. Specify actual input/output limits and a tested recovery path for each oversized operation. Test large scalars, nested evidence arrays, an oversized first row, fencing overhead, and HTTP-cap failures. Successful continuation must reconstruct the original content without skips, duplicates or zero progress.

3. **P1 — The public search contract does not deliver the selected local discovery strategy.**  
   [Source-selection decision](/home/bernt-popp/development/clinpgx-link/docs/research/api-vs-download-decision.md:137) selects downloads for broad discovery, but [spec tools](/home/bernt-popp/development/clinpgx-link/docs/superpowers/specs/2026-09-05-clinpgx-link-design.md:162) give `search_records` exact upstream filters. Local access requires choosing datasets and member-specific fields. [Task 3](/home/bernt-popp/development/clinpgx-link/docs/superpowers/plans/2026-09-05-clinpgx-link.md:208) promises annotation/relationship joins without a corresponding query interface.

   **Required:** Define a discoverable local search path for aliases, identifiers and annotation/entity membership, whether through existing tools or explicit additions. Specify multivalue-filter semantics and provenance-preserving joins. Test multi-gene/multi-drug cells, Swissmedic and non-CPIC/DPWG guideline sources, study/evidence joins, and reverse relationship rows. Exact whole-cell matching must not silently miss a member value.

4. **P1 — Snapshot validation and repository querying can race.**  
   [Shared interfaces](/home/bernt-popp/development/clinpgx-link/docs/superpowers/plans/2026-09-05-clinpgx-link.md:86) validate a cursor’s snapshot before calling `repository.search`, but `search` accepts no snapshot identity or pinned read handle. A refresh between validation and opening `current` can apply an old offset to a new database. Long-lived connections can produce the opposite problem: old rows with newly reported metadata.

   **Required:** Bind snapshot selection, identity verification, count and row retrieval to one immutable repository handle/transaction. Alternatively, verify the returned identity before exposing any results. Test activation and rollback deliberately interleaved with cursor validation, count queries and record reads.

5. **P1 — Installed releases lose historical access to unparsed source content.**  
   [Task 3](/home/bernt-popp/development/clinpgx-link/docs/superpowers/plans/2026-09-05-clinpgx-link.md:207) promises retained artifacts and complete original-document access, while the incorporated [release bundle contract](/home/bernt-popp/development/clinpgx-link/docs/research/data-release-design.md:163) excludes upstream archives. Core includes metadata-only BioPAX. A hash plus a mutable logical download URL cannot retrieve that release’s exact unparsed member after upstream replacement.

   **Required:** Specify which original bytes survive installation and how an MCP-only agent retrieves them. Use retained content-addressed assets, verified immutable upstream versions, or an explicit separate artifact profile. Distinguish historical availability from a current live fallback. Test an installed release after removing the build cache and replacing the upstream logical URL.

6. **P1 — Health, refresh and rollback disagree about authoritative identity.**  
   [Spec operations](/home/bernt-popp/development/clinpgx-link/docs/superpowers/specs/2026-09-05-clinpgx-link-design.md:217) defines health as process liveness, whereas [fleet readiness](/home/bernt-popp/development/clinpgx-link/docs/research/fleet.md:109) and [release deployment](/home/bernt-popp/development/clinpgx-link/docs/research/data-release-design.md:531) require failure on missing/mismatched required identity. Task 3 also publishes snapshots before Task 5 establishes the release activation machinery. Switching `current` alone cannot update a production process pinned to another runtime digest.

   **Required:** Define development API-only and production data-bound modes explicitly. Give one component ownership of activation; make build produce a candidate. Specify whether data changes require restart/configuration changes or coordinated reload. Test expected/actual identity and query provenance across upgrade, mismatch, failed activation and rollback—not just symlink identity.

7. **P1 — Reproducibility conflicts with volatile metadata inside the bundle.**  
   [Release determinism](/home/bernt-popp/development/clinpgx-link/docs/research/data-release-design.md:176) requires identical bytes from identical source/transform/epoch inputs. Yet bundled `source-manifest.json` contains registry retrieval and artifact acquisition timestamps. The [release key](/home/bernt-popp/development/clinpgx-link/docs/research/data-release-design.md:240) excludes those timestamps. Reacquiring unchanged bytes can therefore produce the same tag with different bundle and manifest bytes. Reusing a sealed handoff resolves retries, but does not establish rebuild reproducibility.

   **Required:** Define the reproducibility boundary precisely: either freeze and reuse the original acquisition manifest as an input, or separate subsequent observation metadata from immutable content. Test identical source bytes acquired at different times, sealed retries, and changes limited to parser/configuration, coverage policy or rights evidence.

8. **P1 — Installing a later release has an unspecified predecessor dependency.**  
   [Previous-known-good rules](/home/bernt-popp/development/clinpgx-link/docs/research/data-release-design.md:479) require the manifest’s predecessor to be retained before activation. Consequently, installing release B on a clean machine—or upgrading A directly to C when C names B—fails. The specified CLI downloads one release, and the deployment seed contract does not describe predecessor staging. The [router implementation](/home/bernt-popp/development/genefoundry-router/genefoundry_router/release/data_materialization.py:491) confirms this enforcement.

   **Required:** Define a bounded bootstrap/skipped-upgrade procedure that verifies and stages the required predecessor without recursively requiring all release history. Describe trusted predecessor metadata and offline seed contents. Test fresh installation of a noninitial release, skipped upgrades, missing predecessor, and schema-incompatible rollback.

9. **P1 — The coverage gate can pass with explained but unresolved omissions.**  
   [Task 6](/home/bernt-popp/development/clinpgx-link/docs/superpowers/plans/2026-09-05-clinpgx-link.md:283) fails only on “unexplained missing families.” This permits an explicit `unavailable` entry to satisfy the gate even though the objective requires indexed content or a tested working fallback. The foundation example tests operation enumeration, not usable coverage. Registry-only accounting also misses public attachments and external download links documented in the source matrix.

   **Required:** Separate **inventory-accounting pass** from **objective-coverage pass**. Each required entry needs independent discovery, acquisition, parsing, search, field-completeness and MCP-retrieval states. An explained unresolved gap remains incomplete. Include nonregistry public sources, and test that an omitted registry row, a known-but-unreachable adapter, or a PDF signature-only probe cannot establish queryable text/table coverage.

10. **P2 — Benchmark execution is specified, but acceptance is not fully measurable.**  
    [Task 6](/home/bernt-popp/development/clinpgx-link/docs/superpowers/plans/2026-09-05-clinpgx-link.md:284) expands the cases beyond the referenced benchmark matrix without defining their answer keys or completion thresholds. The [benchmark protocol](/home/bernt-popp/development/clinpgx-link/docs/research/benchmark-design.md:64) excludes availability failures from accuracy; without a separate minimum completed-case requirement, a high score can describe very little working coverage.

    **Required:** Freeze required cases, fixture-derived assertions, model identity, run limits and acceptance criteria before tuning. Report attempted/completed/unavailable counts alongside accuracy. Add real-agent cases for website-only retrieval, document content, multivalue joins and budget continuation. Unit workflow tests do not substitute for those agent traces.

The unfinished website work is **research uncertainty, not itself an architectural defect**. The latest reviewed audit reports working VIP, allele/function/frequency, AMP testing, FDA-association and several attachment fallbacks. Remaining questions include typed interaction coverage, joined literature tabs, uncaptured website chunks and other document formats. Also reconcile the older source matrix’s VIP gap and heterogeneous-XLSX description with newer verified VIP routes and the release document’s corrupted-XLSX finding before freezing fixtures.

Strengths include the measured hybrid decision, explicit separation of export rows from live records, per-source freshness, preservation of known anomalies, fleet envelope/security requirements, trusted manifest verification, and real MCP/agent evidence requirements. **Local build, validation, installation and rollback need no additional publication approval.**

Input hashes below were obtained with `sha256sum`. The website file changed during review; its final reviewed bytes were read once and passed to `sha256sum` together, avoiding a hash/read race.

```text
b44e9db6b4c6d5974d3a207e8cbf6ce682656f2f50a519e98ab15297a5d83f51  docs/superpowers/specs/2026-09-05-clinpgx-link-design.md
49460bf1f9fa4ed003fa98f9f273b28d0ab1bdcceea293cc3da4b372db58b3ae  docs/superpowers/plans/2026-09-05-clinpgx-link.md
ceb38ed765407adad200c0cc4c352361fa5322b50beecd35a833c3eded3ee499  docs/research/api-vs-download-decision.md
33cef61a649fe2b1f4ae42b3b70a348066d0b1e9d539d8253a796d4578ac168e  docs/research/clinpgx-sources.md
74c62b4745a1be5c9568a95a28d4cde1c2b6cead34f33f0fa068651313852b97  docs/research/website-coverage.md
46c25726b53d9343f022b595fa50c78390ad2e3a88503c054321b729565ba3b8  docs/research/data-release-design.md
909bfad93bcfb8d70feeb674e58fbd6b137939bbfb139652956702fa15322ac4  docs/research/fleet.md
```

Additional evidence inspected:

```text
f5f18aca27fac90ab0362d9f34f2ae5d8dbd7355dd4c7bba8b35041cd215982c  docs/research/requirements-ledger.md
cc960068d129a85c487d1e54c71308dabeee30c23f50b668baeda5fc5ae1d7b6  docs/research/benchmark-design.md
ffcd58419427920a1e2685b31f4eddd6c7cc427cfade8f188ffee6ba8efe5bb3  docs/research/clinpgx-openapi-operations-2026-09-05.json
20f22ece9d0406f357be6bef5e2c5f3c44a488c36586984fbe9bb0c8b6d6de99  ../genefoundry-router/genefoundry_router/release/data_materialization.py
61846310051968583b6c236824cec5c9b585e82170a62fe55fd92f3c70cfc7f1  ../clingen-link/clingen_link/runtime_data_identity.py
```
