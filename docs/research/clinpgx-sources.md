# ClinPGx source audit for `clinpgx-link`

Research date: **2026-09-05** (Europe/Berlin). This is a read-only audit of the live ClinPGx API, downloads page, download registry, and representative artifacts. It is intended to drive MCP architecture, not to restate the Swagger document.

## Executive decision

Use a **hybrid, provenance-first design**:

1. Treat monthly download artifacts as the bulk/indexing plane. Download once, retain the original ZIP plus its S3 metadata, validate it, and build a local SQLite/FTS index.
2. Treat the REST API as a live detail/overlay plane for point reads, narrow supported filters, graph reports, variant frequencies, literature-ID resolution, statistics, and JSON-LD.
3. Never try to construct a complete mirror by crawling API object IDs. The documented gene collection cannot be enumerated: an unfiltered call fails, the only filters are exact `accessionId` and `symbol`, wildcard/prefix probes fail, and no cursor/offset/limit exists. An ID-by-ID crawl of the 25,041 genes in the tested archive has a rate-limit-only lower bound of 3.478 hours, before latency and retry costs.
4. Preserve each artifact's own creation/last-modified timestamp. The live registry on the same day contained 2026-09-05 genes/chemicals, but 2026-08-05 summary annotations/guidelines/relationships. A single global “ClinPGx version” would be false precision.
5. Validate and quarantine known-bad export fields. In the tested `genes.tsv`, all 25,041 rows say `Is VIP=Yes`; the live A1BG object instead has `vipTier="Undefined"` and no `vipId` or `vipSummary`. Do not use that TSV column as truth.

The REST API remains necessary because several live objects are richer than the flattened archives, but it is neither a sustainable nor a complete bulk source. Conversely, downloads are fast and complete for their declared snapshot, but are monthly, heterogeneous, and occasionally defective. The two planes are complementary.

## Authoritative sources and reproducible evidence

Primary web sources:

- [API landing page](https://api.clinpgx.org/) — rate limit, stability warning, hostname migration, license statement, and contact.
- [Swagger UI](https://api.clinpgx.org/swagger/) and [OpenAPI 3.0.1 document](https://api.clinpgx.org/openapi.json) — 34 documented operations and declared schemas.
- [Downloads page](https://www.clinpgx.org/downloads) — user-facing canonical artifact categories and monthly generation statement.
- [ClinPGx data usage policy](https://www.clinpgx.org/page/dataUsagePolicy) — ClinPGx/PharmGKB, CPIC, related clinical-content, and PharmCAT terms.
- [Infobutton documentation](https://api.clinpgx.org/infobutton.html).

Local, machine-readable evidence captured by this audit:

- [`clinpgx-openapi-operations-2026-09-05.json`](./clinpgx-openapi-operations-2026-09-05.json) — all documented methods, paths, parameters, and response declarations.
- [`clinpgx-download-registry-2026-09-05.json`](./clinpgx-download-registry-2026-09-05.json) — all 120 entries returned by the website's live file registry, including paths, dates, and sizes.
- [`clinpgx-api-probe-fixtures-2026-09-05.json`](./clinpgx-api-probe-fixtures-2026-09-05.json) — real response envelopes, projection key sets, JSON-LD, failures, and statistics.
- [`clinpgx-network-timings.tsv`](./clinpgx-network-timings.tsv) — 45 measured transfers with protocol mode, response size, and timing phases.
- [`probe-clinpgx-network.sh`](./probe-clinpgx-network.sh) — exact curl commands and original OS/curl/TLS/HTTP2 environment metadata for reproducing the timing protocol.
- [`clinpgx-network-timings.metadata.json`](./clinpgx-network-timings.metadata.json) — environment and provenance sidecar. Only the measurement date was retained; exact start/end instants are explicitly unknown.
- [`download-index-results.json`](./download-index-results.json) — separate local import/index/query benchmark over the downloaded artifacts.

Probe protocol:

- All requests were unauthenticated read-only `GET`s.
- Ad hoc probes were spaced by 0.6–0.7 seconds. The persistent-client benchmark used one HTTP/2 client at one request/second.
- Three-run “new connection” API tests used a fresh `curl` process for every transfer. Three-run “persistent HTTP/2” tests used one process/connection; `num_connects=0` after the first request.
- Download tests followed the API's HTTP 303 redirect to `https://s3.pgkb.org/data/...` and discarded the body after measurement. The files used for schema inspection were retained only in `/tmp/clinpgx_archives`.
- No attempt was made to provoke a 429 response. The server's stated rate-limit behavior was accepted as the contract.
- Exact measurement start/end timestamps were not recorded and must not be reconstructed from response `Date` headers; only the 2026-09-05 observation date and Europe/Berlin research context are known.

## Availability, stability, rate, caching, and licensing

### API service contract

The API landing page states a limit of **2 requests per second**, with HTTP 429 for excess traffic, and asks users planning more than trivial usage to contact `api@clinpgx.org`. It also says that endpoints are “pretty stable” but parameters and responses may change at any time until a final stable release is announced. The old `api.pharmgkb.org` hostname was scheduled to be turned off on 2026-07-20; only `api.clinpgx.org` should be used. There is no authentication scheme in the OpenAPI document, and CORS is enabled. There is no published SLA.

API object responses observed `Cache-Control: no-cache, no-transform`; the landing page also used private/no-cache directives. The API should therefore be wrapped with application caching, bounded retries with jitter, a global token bucket below 2 requests/second, and a circuit breaker. Respect `Retry-After` if it appears, although this audit did not intentionally trigger 429.

### Download service contract

The downloads page says files are generated on the **6th of every month**. The file registry exposes `fileName`, `lastModified`, `path`, and byte `size`. Download URLs of the form:

```text
https://api.clinpgx.org/v1/download/file/data/{fileName}
```

returned HTTP 303 to `https://s3.pgkb.org/data/{fileName}`. The tested S3 responses provided `Content-Length`, `Last-Modified`, `ETag`, `x-amz-version-id`, `Accept-Ranges`, server-side encryption metadata, and `Cache-Control: max-age=14400`. The MCP synchronizer should store all of those values plus a locally calculated SHA-256. Use conditional requests/range resume where supported and perform an atomic “download → verify ZIP/manifest → import → swap current snapshot” update.

Do not infer synchronized monthly versions across exports. Registry dates ranged from 2018-08-05 to 2026-09-05. Even canonical current files had different months on the probe date.

### License boundaries

- ClinPGx/PharmGKB data are offered under **CC BY-SA 4.0**, requiring attribution, a license link, change indication, and ShareAlike for altered data. The policy also says use is for research purposes, not with intent to offer all or part of the data for sale as a commercial item, and accuracy is not guaranteed.
- CPIC curated content is dedicated under **CC0 1.0**, while CPIC requests attribution, URL/access date/version, citation of relevant publications, and checking ClinPGx for the newest content. CPIC explicitly warns that database/API content may be unavailable or lag the guideline page and that table/API data lack nuances present in the full guideline.
- PharmCAT software is MPL-2.0.
- The downloads page explicitly says “From Related Projects” datasets are **not covered** by the ClinPGx Data Usage Agreement; their original authors determine restrictions.

Implementation consequence: license/provenance attaches to every artifact and row. Do not collapse everything under one license. A release bundle containing modified ClinPGx data should carry CC BY-SA obligations; CPIC-only data can retain a separate CC0 marker. Registry-only CPIC/legacy files whose exact boundary is unclear should be marked `license_status=needs_confirmation`, not silently assumed.

This is a technical reading of the published policy, not legal advice.

## REST API inventory

The OpenAPI document declares version `1.0`, server `https://api.clinpgx.org/v1`, **33 GET** operations and **one POST** operation. There are no mutation endpoints. The lone POST is the HTML Infobutton lookup, not a data write.

### Data objects: 26 GET operations

| Resource | Collection query | Item/detail | Filters beyond `view` |
|---|---|---|---|
| Pathway | `GET /data/pathway` | `GET /data/pathway/{id}` | `name`, `accessionId` |
| Gene | `GET /data/gene` | `GET /data/gene/{id}` | `accessionId`, `symbol` |
| Gene cross-references | — | `GET /data/gene/{id}/crossReferences` | path ID |
| Gene ontology terms | — | `GET /data/gene/{id}/ontologyTerms` | path ID |
| Chemical | `GET /data/chemical` | `GET /data/chemical/{id}` | `accessionId`, `name` |
| Disease/phenotype | `GET /data/disease` | `GET /data/disease/{id}` | `accessionId`, `name` |
| Variant | `GET /data/variant/` | `GET /data/variant/{id}` | query `symbol`; item ID |
| Literature | `GET /data/literature` | `GET /data/literature/{id}` | `id`, `resourceId`, `type` |
| Guideline annotation | `GET /data/guidelineAnnotation` | `GET /data/guidelineAnnotation/{id}` | `source` (`cpic`, `dpwg`, `pro`), related chemical ID, related gene ID |
| Drug-label annotation | `GET /data/label` | `GET /data/label/{id}` | `source` (`fda`, `ema`, `pmda`, `hcsc`), related chemical ID/name, gene ID/symbol |
| Summary/clinical annotation | `GET /data/summaryAnnotation` | `GET /data/summaryAnnotation/{id}` | `id`, chemical ID/name, gene symbol, variant fingerprint, evidence level (`1A`, `1B`, `2A`, `2B`, `3`, `4`) |
| Variant annotation | `GET /data/variantAnnotation` | `GET /data/variantAnnotation/{id}` | gene symbol, variant fingerprint |
| VIP | — | `GET /data/vip/{id}` | item ID only |
| Ontology term | `GET /data/ontologyTerm` | — | resource (`biomarkerStatus`, `alleleFunction`, `cpicLevelOfEvidence`, `cpicStatus`, `geneTestLevel`, `guidelineStrength`, `levelsOfEvidence`) |
| Data annotation | `GET /data/dataAnnotation` | — | target accession ID, annotation `type` |
| Connection | `GET /data/connection` | — | object 1/2 ID, name, and type |

All of these declared queries accept `view=min|base|max`; `view` is a projection, not pagination. Some collection-return declarations are incorrect: guideline and label query success schemas are declared as single objects, while live responses contain arrays.

### Report operations: 6 GET operations

| Path | Purpose/parameters | Actual shape observed |
|---|---|---|
| `/report/variantFrequency?fp={fingerprint}` | Population frequency rows for a fingerprint such as rsID | JSend-like wrapper whose `data` is an array; rs9923231 returned 22 rows |
| `/report/stats` | Object/annotation counts and collection time | Raw object; no `{data,status}` wrapper |
| `/report/crossReference?type=&accId=&resource=&view=` | Cross-references grouped by resource | Raw object with `resources[]` |
| `/report/pair/{firstObjId}/{secondObjId}/{resultType}?view=` | Connecting objects | Raw array on success per declaration; result types: `variantAnnotation`, `summaryAnnotation`, `literatureAnnotation`, `multilinkAnnotation`, `pathway`, `guidelineAnnotation`, `label`, `vip`, `vipVariant` |
| `/report/connectedObjects/{id}/{type}` | Connected objects and connection types | Raw array of `{connectedObject, connectionTypes[]}` |
| `/report/literatureId/{pmid}` | Map PMID to internal numeric literature ID | `text/plain` numeric body; 204 when absent |

### Service operations: 2 operations

`GET /infobutton` and `POST /infobutton` return HTML. The GET accepts HL7-style `mainSearchCriteria.v.c`, `.cs`, `.dn`, and `.ot` parameters. This should be exposed as a rendered/link-oriented compatibility tool, not parsed into the core biomedical model.

## Pagination and collection behavior

There is **no documented pagination**: no `limit`, `offset`, `page`, cursor, continuation token, `Link` header contract, or total-count envelope. Collection parameters are shown as optional in OpenAPI, but live unfiltered requests to pathway, gene, chemical, disease, literature, summary annotation, and variant annotation all returned HTTP 400:

```json
{"status":"fail","data":{"errors":[{"message":"Missing criteria."}]}}
```

For genes, `symbol=*` and `accessionId=PA` returned HTTP 404 “No results matching criteria.” This verifies that those obvious wildcard/prefix forms do not provide bulk traversal; it does not prove every undocumented query grammar impossible. The supported contract should be described narrowly: exact documented criteria only, with downloads as the supported bulk route.

No result cap was visible in the broad valid filters tested:

| Query | Count | Body | Total time |
|---|---:|---:|---:|
| Summary annotations, level 3, `view=min` | 4,478 | 4,903,830 B | 7.058 s |
| Variant annotations, CYP2D6, `view=min` | 1,646 | 5,777,536 B | 7.438 s |
| Connections for CYP2D6, `view=min` | 7,403 | 1,789,327 B | 0.969 s |
| FDA labels, `view=min` | 536 | 72,549 B | 0.605 s |
| CPIC guidelines, `view=min` | 79 | 13,061 B | 0.523 s |

These are complete returned-array lengths, not declared totals. Because there is no pagination or total metadata, the client cannot distinguish “complete” from a future silent cap. Enforce a configurable maximum response byte count, but do not truncate silently; return a structured `response_too_large` error suggesting the local index or a narrower filter.

## Actual response contracts and projections

The most important schema fact is that OpenAPI often describes an entity directly, while live `/data/...` responses use a JSend-like envelope:

```json
{
  "data": [
    {
      "objCls": "Gene",
      "id": "PA128",
      "symbol": "CYP2D6",
      "name": "cytochrome P450 family 2 subfamily D member 6"
    }
  ],
  "status": "success"
}
```

Queries use `data: []`; item routes use `data: {}`. Failures use `status:"fail"` and `data.errors[]`. Missing objects returned 404. Reports are heterogeneous as documented above. Runtime decoders must therefore validate both the HTTP status and these route-specific body shapes; generated OpenAPI clients alone are unsafe.

Projection sizes and observed keys show that `min`, `base`, and `max` vary by object type:

| Object/probe | `min` bytes | `base` bytes | `max` bytes | Notable `max` additions |
|---|---:|---:|---:|---|
| Gene CYP2D6 query | 133 | 3,849 | 11,141 | aliases, cross-references, terms, version |
| Summary annotation 1448100508 item | 1,265 | 4,435 | 36,769 | supporting variant annotations, version |
| Variant annotation 1450042177 item | 2,691 | 3,383 | 6,090 | history/related objects, version |
| Guideline PA166279741 item | 539 | 23,500 | 23,880 | base already contains markdown/literature; max adds terms/version |
| Label PA166114907 item | 158 | 5,338 | 5,961 | terms/version |

A matched CPIC clopidogrel/CYP2C19 summary item was 130,283 B at `max`, because it embedded 87 supporting variant annotations. A pathway `max` item was 114,684 B. `max` should never be the default for discovery/search; use `min`, then retrieve `base`/`max` explicitly.

JSON-LD is functional only with the correct media type. `Accept: application/ld+json` returned `Content-Type: application/ld+json` and added `@id`/`@context`; `Accept: application/json-ld` returned HTTP 406.

Observed broken or misleading contracts:

- `GET /data/vip/{id}` returned HTTP 400 with `"'vip' is an invalid ObjectType enum value."` when passed a gene object's `vipId` (`PA166170264`). OpenAPI declares the path parameter as a number, while gene `vipId` is a PA accession. Treat this documented operation as broken/unresolved pending a known working example from ClinPGx.
- OpenAPI query parameters are marked optional even though a criterion is required for normal collections.
- OpenAPI omits the JSend wrapper from most success schemas and gets some collection cardinalities wrong.
- OpenAPI source enums lag observed exports. Guideline JSON contained CPIC, DPWG, RNPGx, CPNDS, AIOM, SEFF/SEOM, CFF, CERSI-PGx, AusNZ, AHA, and ACR; the API enum lists only `cpic`, `dpwg`, `pro`. Drug-label TSV contained Swissmedic, but the API enum omits it. The meaning/coverage of `pro` needs confirmation.
- Same-day statistics differed from returned/exported counts: `allGenes=25,047` versus 25,041 TSV rows; `cpicAnns=78` versus 79 API query rows; `fdaLabels=533` versus 536 API query rows; `pgkbPathways=285` versus 283 pathway JSON objects. These could reflect collection-time skew, retirement/inclusion semantics, or defects. Never use `/report/stats` as an integrity checksum without understanding its counting rules.

## User-facing download inventory

The downloads SPA obtains metadata from the **undocumented** endpoint `GET /v1/data/file/data/?view=min`. It returned 120 registry entries totaling 81,911,941 bytes. The exact snapshot is in the local registry fixture. The user-facing page selects these canonical files:

| Page category | Artifact(s) | Snapshot contents observed |
|---|---|---|
| Summary and variant annotations | `summaryAnnotations.zip` | 4 TSV tables: 5,190 summaries; 16,117 allele/genotype texts; 14,054 history rows; 15,538 evidence rows |
|  | `variantAnnotations.zip` | phenotype (14,510), drug (12,997), functional-assay (2,158), study-parameter (35,991) TSV rows |
| Variant/gene/drug relationships | `relationships.zip` | 127,786 bidirectional relationship rows |
| Clinical guideline annotations | `guidelineAnnotations.json.zip` | 219 one-guideline-per-file JSON documents |
| Drug-label annotations | `drugLabels.zip` | 1,433 labels plus 238 gene-to-label rollups |
| Pathways | `pathways-biopax.zip` | 142 BioPAX OWL files |
|  | `pathways-tsv.zip` | 283 per-pathway interaction TSV files |
|  | `pathways.json.zip` | One JSON array with 283 rich pathway objects |
| Clinical variant data | `clinicalVariants.zip` | 5,191 flattened variant/gene/type/evidence/drug/phenotype rows |
| Literature occurrence | `occurrences.zip` | 146,520 source-object occurrence rows |
| Genes | `genes.zip` | 25,041 rows |
| Variants | `variants.zip` | 7,615 annotated variant rows |
| Drugs/chemicals | `drugs.zip`, `chemicals.zip` | 3,763 drug rows; 5,313 chemical rows; drugs are a subset/projection of chemicals |
| Phenotypes | `phenotypes.zip` | 1,621 disease/phenotype rows |
| Other datasets | AllOfUs and UK Biobank frequency ZIPs; `automated_annotations.zip`; training exercises; historical Papers of Interest; externally hosted NAT2 supplement | Not one coherent schema; automated annotations are explicitly machine-mined/unvalidated |
| Related projects | ITPC, ISPC, IWPC, TPP, Tatonetti et al. datasets/code | Separate provenance and license; several are Stanford repository/GitHub links or submission downloads |

Every inspected canonical ZIP contained a `LICENSE.txt` and `CREATED_YYYY-MM-DD.txt`; most contained a README PDF. Import should fail closed if the expected members are absent, but should allow versioned schema evolution through explicit adapters.

### Allele/haplotype and registry-only artifacts

Alleles are not presented as a main card on the public downloads page, but the registry exposes several public artifacts:

- `clinpgxHaplotypes.zip` (2026-09-05) contains `clinpgxHaplotypes_star_alleles.tsv` with 625 rows and `clinpgxHaplotypes_named_alleles.tsv` with 760 rows. Both have columns `Accession ID`, `Gene`, `Allele Name`, `HGVS`, `Structural Variation`, `AMP Level`.
- Its bundled `README.pdf` is broken: it is a 95-byte JSON failure saying `No page with key: downloadHgvsHaplotypeHelp`, not a PDF.
- `haplotypes.zip` (2026-04-05) contains 49 per-gene XLSX files plus license/creation markers. All 49 inner workbooks in the tested archive are structurally corrupt (`BadZipFile`); see `auxiliary-formats.md`. Retain the archive and validation evidence as a source asset, but do not normalize it or use it as an allele-function fallback unless a future release has a new hash and passes validation.
- `cpic.alleles.json` contains 4,592 `{allele, guidelines}` objects, but every tested `guidelines` array is empty. `cpic.alleles.csv` is only the 26-byte header `Gene,Allele,Guideline,URL`. Treat these as defective/incomplete, not evidence of “no guidelines.”
- `cpic.drug.mapping.zip` contains per-drug XLSX mapping workbooks.

Other registry-only groups, which should be catalogued and retrievable but not all imported by default:

- Legacy/alternate annotations: `annotations.zip`, `clinicalAnnotations.zip`, `clinicalAnnotations_LOE1-2.zip`, `clinical_annotations-public.zip`, `dosingGuidelines*.json.zip`, `guidelineAnnotations.extended.json.zip`, `variant.clinical.summary.zip`, `vip.zip`, `vip.variants.zip`.
- Genome/variant integrations: `clinpgx.annotations.bed`, `pharmgkb.annotations.bed`, `rsid.zip`, current/historical ClinVar XLSX submissions.
- Lookup/integration exports: `atc.zip`, `genecards.zip`, `lexicon.zip`, `uniprot.zip`, `pharmgkb.gene.phenotypes.json`, annotated PMID ZIPs, `publications.tsv`, `topPairs.json`.
- Pathway/software-related artifacts: `pathvisio.zip`, `pathway-illustrator.zip`, `pathway-overview.zip`, `pathways-overview.zip`, `pharmcat.zip`, `pharmcat-shc.zip`.

“Registry-listed” is not equivalent to “supported public contract.” The registry and download routes are absent from OpenAPI. The default MCP dataset should use the page-listed canonical files plus explicitly approved allele files; everything else belongs in an `experimental_or_legacy` catalog tier with dates, sizes, and warnings.

## Exact canonical file schemas

Actual headers on 2026-09-05 follow. These are more reliable for import than prose READMEs because one README described obsolete study-parameter columns that are absent from the current evidence TSV.

```text
summary_annotations.tsv
Summary Annotation ID | Variant/Haplotypes | Gene | Level of Evidence | Level Override | Level Modifiers | Score | Phenotype Category | PMID Count | Evidence Count | Drug(s) | Phenotype(s) | Latest History Date (YYYY-MM-DD) | URL | Specialty Population

summary_ann_alleles.tsv
Summary Annotation ID | Genotype/Allele | Annotation Text | Allele Function

summary_ann_history.tsv
Summary Annotation ID | Date (YYYY-MM-DD) | Type | Comment

summary_ann_evidence.tsv
Summary Annotation ID | Evidence ID | Evidence Type | Evidence URL | PMID | Summary | Score
```

```text
var_pheno_ann.tsv
Variant Annotation ID | Variant/Haplotypes | Gene | Drug(s) | PMID | Phenotype Category | Significance | Notes | Sentence | Alleles | Specialty Population | Metabolizer types | isPlural | Is/Is Not associated | Direction of effect | Side effect/efficacy/other | Phenotype | Multiple phenotypes And/or | When treated with/exposed to/when assayed with | Multiple drugs And/or | Population types | Population Phenotypes or diseases | Multiple phenotypes or diseases And/or | Comparison Allele(s) or Genotype(s) | Comparison Metabolizer types

var_drug_ann.tsv
Variant Annotation ID | Variant/Haplotypes | Gene | Drug(s) | PMID | Phenotype Category | Significance | Notes | Sentence | Alleles | Specialty Population | Metabolizer types | isPlural | Is/Is Not associated | Direction of effect | PD/PK terms | Multiple drugs And/or | Population types | Population Phenotypes or diseases | Multiple phenotypes or diseases And/or | Comparison Allele(s) or Genotype(s) | Comparison Metabolizer types

var_fa_ann.tsv
Variant Annotation ID | Variant/Haplotypes | Gene | Drug(s) | PMID | Phenotype Category | Significance | Notes | Sentence | Alleles | Specialty Population | Assay type | Metabolizer types | isPlural | Is/Is Not associated | Direction of effect | Functional terms | Gene/gene product | When treated with/exposed to/when assayed with | Multiple drugs And/or | Cell type | Comparison Allele(s) or Genotype(s) | Comparison Metabolizer types

study_parameters.tsv
Study Parameters ID | Variant Annotation ID | Study Type | Study Cases | Study Controls | Characteristics | Characteristics Type | Frequency In Cases | Allele Of Frequency In Cases | Frequency In Controls | Allele Of Frequency In Controls | P Value | Ratio Stat Type | Ratio Stat | Confidence Interval Start | Confidence Interval Stop | Biogeographical Groups
```

```text
relationships.tsv
Entity1_id | Entity1_name | Entity1_type | Entity2_id | Entity2_name | Entity2_type | Evidence | Association | PK | PD | PMIDs

drugLabels.tsv
PharmGKB ID | Name | Source | Biomarker Flag | Testing Level | Has Prescribing Info | Has Dosing Info | Has Alternate Drug | Has Other Prescribing Guidance | Cancer Genome | Prescribing | Chemicals | Genes | Variants/Haplotypes | Latest History Date (YYYY-MM-DD)

drugLabels.byGene.tsv
Gene ID | Gene Symbol | Label IDs | Label Names

clinicalVariants.tsv
variant | gene | type | level of evidence | chemicals | phenotypes

occurrences.tsv
Source Type | Source ID | Source Name | Object Type | Object ID | Object Name
```

```text
genes.tsv
PharmGKB Accession Id | NCBI Gene ID | HGNC ID | Ensembl Id | Name | Symbol | Alternate Names | Alternate Symbols | Is VIP | Has Variant Annotation | Cross-references | Has CPIC Dosing Guideline | Chromosome | Chromosomal Start - GRCh37 | Chromosomal Stop - GRCh37 | Chromosomal Start - GRCh38 | Chromosomal Stop - GRCh38

variants.tsv
Variant ID | Variant Name | Gene IDs | Gene Symbols | Location | Variant Annotation count | Clinical Annotation count | Level 1/2 Clinical Annotation count | Guideline Annotation count | Label Annotation count | Synonyms

drugs.tsv / chemicals.tsv
PharmGKB Accession Id | Name | Generic Names | Trade Names | Brand Mixtures | Type | Cross-references | SMILES | InChI | Dosing Guideline | External Vocabulary | Clinical Annotation Count | Variant Annotation Count | Pathway Count | VIP Count | Dosing Guideline Sources | Top Clinical Annotation Level | Top FDA Label Testing Level | Top Any Drug Label Testing Level | Label Has Dosing Info | RxNorm Identifiers | ATC Identifiers | PubChem Compound Identifiers | Top CPIC Pairs Level | FDA Label has Prescribing Info | In FDA PGx Association Sections

phenotypes.tsv
PharmGKB Accession Id | Name | Alternate Names | Cross-references | External Vocabulary
```

Per-pathway TSV header:

```text
From | To | Reaction Type | Controller | Control Type | Cell Type | PMIDs | Genes | Drugs | Diseases | Summary
```

`pathways.json` is an array of rich pathway objects. Representative top-level keys are `id`, `name`, `authors`, `description`, `summary`, `pharmacodynamic`, `pharmacokinetic`, `genes`, `chemicals`, `diseases`, `nodes`, `interactions`, `pathwayComponents`, `literature`, `relatedGenes`, `relatedChemicals`, `relatedDiseases`, `relatedPathways`, `crossReferences`, `terms`, `history`, `biopaxLink`, `gpmlLink`, `imageLink`, `thumbnailLink`, `pdfLink`, `illustratorLink`, `alternateViews`, `formatVersion`, and `objCls`.

Each guideline JSON file is an object with `guideline` and `citations`. The guideline holds `id`, `name`, `source`, prescribing/testing flags, `relatedGenes`, `relatedChemicals`, `relatedAlleles`, `literature`, `history`, cross-references, terms, and rendered `summaryMarkdown`/`textMarkdown` HTML. Store the original JSON and sanitize HTML at render time; do not discard embedded tables or reduce the recommendation to plain text during import.

Relationship semantics require special care. Rows are deliberately emitted twice (`A→B` and `B→A`) and do **not** imply biological directionality. `Association` is `associated`, `not associated`, or `ambiguous`; PK/PD blanks do not prove absence. Disease relationships can merely mean that an annotation was studied in people with that disease, not that the gene/variant causes disease. Model a canonical unordered pair plus source rows and evidence facets, while retaining original order/row provenance.

## Coverage matrix: website versus documented API versus archives

The downloads do **not** contain all data exposed through the documented API or website. The matrix below is deliberately conservative and based only on: (a) operations in the live OpenAPI file, (b) tested response bodies, (c) files actually unpacked, and (d) routes/file links present in the live website JavaScript. A route present in the SPA bundle establishes that the current website uses it; it does not make the route a supported public API contract.

Status meanings: **full** = the tested/declared source directly represents the family's core records; **partial** = useful but flattened, stale, source-limited, missing assets/fields, or not globally enumerable; **not present** = no corresponding source was found; **unknown** = a candidate exists but correctness/coverage was not established.

| Data family | Website/UI evidence | Documented REST API | Download evidence | Coverage decision and exact fallback |
|---|---|---|---|---|
| Genes | `/site/gene/{id}`, gene/haplotype tabs | **Full point detail; partial collection**: `/data/gene`, `/data/gene/{id}`, `/crossReferences`, `/ontologyTerms`; exact criteria only | **Partial** `genes.zip`: bulk identity/xrefs/coordinates/flags, but known-bad `Is VIP` and fewer rows than stats | Bulk from `genes.zip`; overlay `/data/gene/{id}?view=max` and the two gene subroutes. Do not answer VIP from TSV. |
| VIP gene overviews | `/site/vips`, `/site/vip/{id}` and gene VIP presentation | **Broken/unknown**: documented `/data/vip/{id}` failed for a live gene `vipId` | **Unknown/stale** `vip.zip` and `vip.variants.zip` dated 2018; broken genes VIP flag | No production-safe complete machine fallback was verified. Return an explicit gap; optionally link the website pending ClinPGx clarification. |
| Alleles/haplotypes and definitions | `/site/allele/{id}`, `/site/haplotype/{id}`, `/site/gene/{id}/haplotypes`; gene objects expose an `alleleFile` attachment | **Not present** as documented allele/haplotype collection/detail resources | **Partial**: current basic `clinpgxHaplotypes.zip`; corrupt legacy `haplotypes.zip`; PharmCAT allele/function JSON; incomplete `cpic.alleles.*`; CPIC mapping workbooks | Search the validated basic TSV locally. Use versioned PharmCAT JSON for its supported allele translation/function scope and verified `/site` routes for current website detail. Quarantine the tested legacy XLSX archive; do not pretend an attachment is normalized/queryable. |
| Haplotype/phenotype/activity-score frequencies | Website `/site/alleleFrequency`, `/site/alleleFrequency/{gene}`, `/site/haplotypeFrequency/_download/{gene}` | **Not present**; `/report/variantFrequency` is a different family | **Partial**: parsed All of Us v7 and UKBB tables plus external NAT2 data, not a universal frequency corpus | Query named local datasets with study/release provenance. Use the verified site JSON/TSV route for a supported gene when archive scope is insufficient; preserve a 204 as an explicit no-data result. Do not substitute variant frequency. |
| Chemicals and drugs | `/site/chemical/{id}` | **Full point detail; partial collection**: `/data/chemical` exact ID/name and item `max` | **Full bulk core, partial rich detail**: `chemicals.zip`; `drugs.zip` subset | Local bulk search, then `/data/chemical/{id}?view=max` for components, metabolites, structures/types, and current detail. |
| Diseases/phenotypes | `/site/disease/{id}` | **Full point detail; partial collection**: `/data/disease` exact ID/name and item | **Full bulk mapping, partial rich detail** `phenotypes.zip` | Local search/mapping; API item overlay for full object. |
| Variants and genomic locations | `/site/variant/{id}`, variant haplotype tab | **Full point detail; partial collection**: `/data/variant/?symbol=`, `/data/variant/{id}` | **Partial** `variants.zip`: page states dbSNP-tracked annotated variants; flattened coordinates/counts/synonyms | Local rsID/alias search, live item `max` for locations, history, terms, rarity, obsolete status. Non-dbSNP/site-only variants cannot be assumed present in ZIP. |
| Variant population frequency | Displayed on variant pages | **Full on demand** `/report/variantFrequency?fp=` | **Not present** as a canonical all-variant frequency archive | Live report with cache; no bulk fallback verified. Do not substitute biobank haplotype-frequency files. |
| Literature metadata | `/site/literature/{id}`, `/site/pmid/{pmid}`, publications UI/download | **Partial collection, full point**: `/data/literature` requires criteria; `/data/literature/{id}`; `/report/literatureId/{pmid}` | **Partial**: `occurrences.zip`, registry `publications.tsv`, annotated PMID lists; no full rich literature dump verified | Search local publication/occurrence subsets, resolve PMID, retrieve exact API item. State that global literature search/completeness is unavailable. |
| Summary/clinical annotations | `/site/clinicalAnnotation/{id}`, tabs and overview routes | **Full point and broad filtered arrays** `/data/summaryAnnotation[/{id}]` | **Full monthly core, partial rich nesting** `summaryAnnotations.zip` relational TSVs; `clinicalVariants.zip` flattened derivative | Local relational query by default; live `max` for nested supporting annotations/current detail. |
| Variant annotations and study parameters | `/site/variantAnnotation/{id}`, variant-annotation tabs | **Full point and filtered arrays** `/data/variantAnnotation[/{id}]` | **Full monthly core, partial history/version links** `variantAnnotations.zip` four tables | Local subtype/study query; live item overlay for history/version/related objects. |
| Guideline annotations/recommendations | `/site/guideline/{id}`, publications, downloads, supplements/flowcharts/file artifacts | **Partial-source query; full point object** `/data/guidelineAnnotation[/{id}]`; source enum does not name all observed sources | **Full monthly annotation JSON, partial total guideline package** `guidelineAnnotations.json.zip`; legacy extended/dosing files; publications/supplements not all embedded as bulk assets | Query normalized JSON locally, overlay API item. Expose publication/supplement/flowchart URLs as source assets via website/API links; do not claim the JSON alone is the complete clinical guideline. |
| Drug-label annotations and highlighted labels | `/site/labelAnnotation/{id}` and highlighted-label attachment link | **Partial-source query; full point annotation** `/data/label[/{id}]`; Swissmedic missing from enum | **Partial** `drugLabels.zip`: flattened annotations/gene rollup, not highlighted documents/full text | Local label discovery; live item for narrative and `highlightedLabelLink`; source document remains a linked/downloadable asset, not automatically queryable. |
| Pathway data and diagrams | `/site/pathway/{id}` plus BioPAX, GPML, TSV, PDF, image/Illustrator links | **Full point JSON; partial exact collection** `/data/pathway[/{id}]` | **Full monthly JSON core; partial formats**: 283 JSON/TSV pathways but only 142 BioPAX files; registry has other pathway packages | Local JSON/TSV graph. Use API `max` links or website pathway downloads for missing GPML/PDF/images/BioPAX. A link is an asset fallback, not normalized graph coverage. |
| Summarized relationships | connection/link tabs | **Full targeted query/report** `/data/connection`, `/report/connectedObjects`, `/report/pair` | **Full declared monthly relationship summary** `relationships.zip`, but evidence is aggregated and rows duplicated in reverse | Local canonical-pair/evidence index; API reports for live connected objects and typed pair evidence. Retrieve underlying annotation IDs for claim-level support. |
| Cross-references and vocabularies | Embedded throughout entity pages | **Full targeted** gene crossrefs/ontology and generic `/report/crossReference`; `/data/ontologyTerm` for seven resources | **Partial embedded mappings** in primary TSVs plus registry lookup exports (`atc`, `uniprot`, etc.) | Use local embedded/xref indexes first, live reports for target objects, and live ontologyTerm for enumerated resources. No complete ontology archive verified. |
| General data annotations/overviews | Website object pages render data annotations | **Targeted only** `/data/dataAnnotation?targets.accessionId=&type=` | **Unknown/not explicit**: legacy `annotations.zip` may overlap but was not established as the same family | Live exact-target API is the only verified machine fallback. Do not map legacy archives without schema/provenance validation. |
| Counts/statistics | `/statistics` | **Full live report** `/report/stats` | **Not present** as an authoritative snapshot; archive row counts differ | Live/cached stats for display only. Compute snapshot counts from imported rows and label the two timestamps/semantics separately. |
| Infobutton result | ClinPGx Infobutton service | **Full service** GET/POST `/infobutton`, HTML response | **Not present** | Live service only; return sanitized HTML/link, not a domain-record substitute. |
| Raw attachments, PDFs, diagrams, XLSX and submissions | Numerous page-specific download links | **Not in OpenAPI**; current site uses `/download/file/...`, `/download/submission/...`, `/download/pathway/...` | **Partial registry** plus per-page assets; not all assets are registry-listed | Allowlisted asset retrieval with provenance and MIME/size checks. Assets are not queryable data until a specific parser has been validated. |

Accordingly, the MCP must implement a per-family fallback policy rather than a universal “offline mode.” Offline answers can be comprehensive for the canonical bulk cores (primary entities, summary/variant annotations, relationship summaries, and pathway JSON), but VIP narratives, live variant frequencies, full literature, ontology enumeration, general data annotations, and many guideline/label/pathway assets require live or manually linked sources. If live access is disabled, those tools should return `coverage_status=partial|unavailable` rather than silently answer from a non-equivalent file.

## Measured speed: API versus bulk downloads

Matched live API objects used CYP2C19 (`PA124`), clopidogrel (`PA449053`), rs4244285 (`PA166154053`), summary annotation `655386913`, and CPIC guideline `PA166104948`.

| API `view=max` object | Bytes | Fresh-process median (n=3) | Persistent HTTP/2 median (n=3) |
|---|---:|---:|---:|
| Gene | 10,040 | 0.571 s | 0.510 s |
| Chemical | 6,325 | 0.520 s | 0.344 s |
| Variant | 4,199 | 0.506 s | 0.374 s |
| Summary annotation | 130,283 | 1.139 s | 0.890 s |
| Guideline annotation | 20,303 | 0.437 s | 0.353 s |

The persistent median includes the first connection-establishing request for the gene; only two later gene observations had `num_connects=0`, so no separate n=2 “warm median” is claimed.

| Whole archive | Compressed bytes | Redirected-download median (n=3) |
|---|---:|---:|
| `genes.zip` | 2,905,541 | 0.541 s |
| `chemicals.zip` | 814,225 | 0.490 s |
| `drugs.zip` | 678,392 | 0.428 s |
| `summaryAnnotations.zip` | 1,236,737 | 0.531 s |
| `guidelineAnnotations.json.zip` | 861,200 | 0.581 s |

One warm whole-archive transfer therefore took roughly the same wall time as one live object call in this environment. This does not make an archive query-equivalent: archives are monthly snapshots and flatten/omit some API fields. It does show why bulk ingestion is dramatically more sustainable than API crawling.

The separate local benchmark imported 15 archives (444,508 retained rows/JSON documents) into a 347 MB SQLite+FTS database in 5.20 seconds. Indexed gene/chemical/variant lookups were 0.004–0.006 ms median, FTS was 0.24 ms, a summary-pair query was 2.66 ms, and an intentionally unindexed relationship-pair query was 53 ms. These figures are environment-specific, but the orders-of-magnitude difference supports local-first read paths after a monthly build.

## Recommended storage model

### Artifact and provenance layer

`artifact_snapshot`:

- stable logical name and registry path
- contract tier: `canonical_page`, `approved_registry`, `experimental_or_legacy`, `related_project`
- acquisition URL and final S3 URL
- registry `lastModified`/`size`
- HTTP `Last-Modified`, `ETag`, `x-amz-version-id`
- embedded `CREATED_...` date and raw license text/hash
- SHA-256, acquired-at timestamp, parser/schema version, validation status
- replacement relationship to the previous snapshot

Every normalized row should carry `artifact_snapshot_id`, archive member path, row number or JSON accession ID, and the raw source value for lossy/multivalue fields. This enables citations, debugging, and re-import.

### Normalized domain layer

- `entity`: ClinPGx ID, kind (`gene`, `chemical`, `disease`, `variant`, `allele/haplotype`, `literature`, `pathway`), preferred label.
- `entity_alias`, `cross_reference`, `ontology_term`, `entity_ontology_term`.
- Typed extensions for genomic coordinates/builds, chemical structures/vocabularies, literature metadata, and allele HGVS/SV/AMP data.
- `summary_annotation`, `summary_allele_text`, `summary_evidence`, `summary_history`.
- `variant_annotation` with a subtype discriminator plus subtype-specific fields; `study_parameter` keyed to variant annotation.
- `guideline_annotation` and `drug_label_annotation`, retaining original JSON/HTML plus normalized source, flags, entity joins, citations, and history.
- `pathway`, `pathway_node`, `pathway_interaction`, typed input/output/controller edges, and original JSON/BioPAX/TSV asset links.
- `relationship_evidence` keyed by canonical unordered entity pair, association state, PK/PD evidence, evidence types, and PMID joins; retain original directed export row separately.
- `occurrence`, `publication`, `variant_frequency` cache, and `sync_warning`/`validation_issue`.

Use FTS over names, aliases, annotation sentences/summaries, guideline text, label text, and pathway descriptions. Add exact indexes for PA IDs, gene symbols, rsIDs/fingerprints, PMID, RxNorm, ATC, PubChem, evidence level, guideline/label source, and unordered relationship pairs.

Do not treat semicolon/comma-delimited cells uniformly. Their meanings differ by file: relationship evidence is comma-delimited and PMIDs semicolon-delimited; summary drugs/phenotypes use semicolons; pathway PMIDs/entities use commas; several quoted alias fields use CSV-style quoting inside TSV. Parse each artifact with a schema-specific adapter.

## Complete MCP exposure strategy

Expose a compact high-level interface backed by typed internal clients for every documented operation, rather than forcing models to compose raw URLs.

### Local-first tools

- `clinpgx_search_entities(kind, query, identifiers?, limit?, cursor?)` — local exact/alias/xref/FTS search with MCP-level pagination.
- `clinpgx_get_entity(kind, id, detail=summary|full, freshness=local|live|auto)` — local normalized record; optional live `base/max` overlay.
- `clinpgx_query_annotations(kind, gene?, chemical?, variant?, phenotype?, source?, evidence_level?, limit?, cursor?)` — summary, variant, guideline, and label annotations with stable local pagination.
- `clinpgx_get_annotation(kind, id, detail, freshness)` — preserves recommendation HTML, evidence, citations, history, and source timestamp.
- `clinpgx_get_relationships(entity_id, other_kind?, association?, evidence_type?, limit?, cursor?)` — canonical pair semantics plus raw evidence provenance.
- `clinpgx_search_pathways(...)` / `clinpgx_get_pathway(...)` — normalized interactions plus links/resources for JSON, TSV, BioPAX, GPML, PDF, and images.
- `clinpgx_search_literature(...)` / `clinpgx_get_literature(...)`.
- `clinpgx_query_alleles(gene?, allele?, hgvs?, amp_level?)` — use validated allele artifacts and clearly disclose the incomplete CPIC guideline mapping.

### Live/report tools

- `clinpgx_get_variant_frequency(fingerprint, freshness=live)` → `/report/variantFrequency`.
- `clinpgx_get_connected_objects(id, type)` and `clinpgx_get_pair_evidence(id1,id2,result_type,view)` → the two graph reports.
- `clinpgx_get_cross_references(type, accession_id, resource?, view?)`.
- `clinpgx_resolve_pmid(pmid)`.
- `clinpgx_get_stats(freshness=live|cached)` with a warning that counts are informational, not snapshot checksums.
- `clinpgx_infobutton(...)` returning sanitized HTML or a link.

The internal live client should cover all 34 documented operations and route each through a hand-written response adapter. A low-level generic arbitrary-path tool should **not** be exposed; it weakens schema guarantees and could accidentally expand scope if future write endpoints appear.

### Artifact/resource tools

- `clinpgx_list_exports(tier?, category?, format?, updated_after?)` from the registry snapshot, with license, size, date, status, and defects.
- `clinpgx_get_export_metadata(name)`.
- `clinpgx_sync_exports(names?|canonical=true, force=false)` as an administrative/local mutation, not a default read tool.
- `clinpgx_open_source_asset(name, member?, byte_range?)` with a strict registry allowlist, archive-member path traversal protection, decompression/file-size ceilings, MIME validation, and no arbitrary URL fetch.
- MCP resources such as `clinpgx://snapshot/current`, `clinpgx://export/{name}`, `clinpgx://entity/{kind}/{id}`, and `clinpgx://annotation/{kind}/{id}` for browsable/citable records.

Every response should include a `provenance` object: plane (`download`/`api`), artifact/API URL, snapshot/response timestamp, source `lastModified` or live retrieval time, license class, parser version, and warnings. Clinical content responses should always state that data may lag source guidelines and is not a substitute for clinical judgment.

## Synchronization and validation policy

1. Poll the small registry no more than daily, preferably after the monthly publication window. Cache it for at least 24 hours.
2. Compare logical name, lastModified, size, and stored ETag/version ID. Download only changed canonical/approved artifacts.
3. Stream to a temporary file; calculate SHA-256; verify ZIP central directory, safe member names, declared/compressed/uncompressed sizes, license, creation marker, and required members.
4. Run schema/header checks and semantic invariants. Unknown columns are an additive schema change; missing/renamed required columns quarantine the candidate snapshot.
5. Import into a new database generation, build indexes/FTS, run counts and representative known-ID checks, then atomically switch the current pointer. Keep the previous generation for rollback.
6. Record cross-source discrepancies as warnings, not automatic failure, because timestamps and inclusion semantics differ.
7. Use live API overlays only on demand or for cache refreshes. Cache immutable-ish object responses by `(route,id,view,accept)` with a configurable TTL; negative-cache 404 briefly; never cache 429/5xx as valid data.

Minimum semantic checks should include: PA-ID shape, uniqueness within artifact, foreign-key resolvability where intended, valid evidence levels, known enum drift capture, no impossible ZIP/PDF MIME mismatch, relationship reverse-pair expectations, and sentinel comparisons for CYP2C19/clopidogrel/rs4244285. Explicitly add regression checks for `genes.tsv Is VIP`, the haplotype README, empty CPIC guideline arrays, and source-enum drift.

## Open questions to ClinPGx

Send these to `api@clinpgx.org` before calling the MCP production-grade:

1. Is there an intended supported way to enumerate all objects or paginate collection queries? Are wildcard/partial matching forms supported?
2. Are `/v1/data/file/data/` and `/v1/download/file/...` public contracts, and may clients rely on registry fields, ETags, and stable logical filenames?
3. What exactly does guideline `source=pro` include, and how should RNPGx, CPNDS, AIOM, SEFF/SEOM, CFF, CERSI-PGx, AusNZ, AHA, and ACR be queried? How should Swissmedic labels be queried?
4. What is a working example for `/data/vip/{id}` and what identifier type is expected?
5. Why do OpenAPI success schemas omit live JSend envelopes and misdeclare some collection results? Is a corrected specification planned?
6. Why do same-day stats/API/archive counts differ, and which inclusion/retirement rules apply to each?
7. Is `genes.tsv Is VIP=Yes` for all rows a known export bug?
8. Are the broken haplotype README, empty `guidelines` arrays in `cpic.alleles.json`, and header-only `cpic.alleles.csv` known issues? Which allele/function export is recommended for production use?
9. Which registry-only and legacy artifacts are supported, current, and licensed for redistribution? Are the stale `dosingGuidelines*`/VIP files superseded by named canonical files?
10. Is there an update feed, version manifest, changelog, or notification mechanism more reliable than polling on/after the 6th?
11. Is there an uptime target and a preferred bulk-download/user-agent/contact-registration practice for public mirrors?

## Remaining limitations

- Measurements are one client/location on one day and are not an SLA or load test.
- The audit did not exceed the published rate limit, test 429 headers, test every parameter combination, or exhaustively diff every OpenAPI property against every runtime object.
- Only canonical archives plus selected allele/CPIC files were unpacked. XLSX, BED, OWL, GPML, PDF, and legacy registry artifacts still require dedicated schema/security profiling before normalized import.
- The API and downloads changed during the observation date: registry timestamps and live stats show that a generation job was active. Some count discrepancies may be transient.
- External related-project licenses were not individually researched; they must remain disabled for redistribution until verified.

AI disclosure: This audit was produced with AI-assisted research and read-only HTTP/file inspection. Claims above are tied to the cited primary sources or the checked-in probe fixtures. No clinical recommendation was generated.
