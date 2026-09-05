# ClinPGx website/download coverage audit

**Audit date:** 2026-09-05  
**Scope:** public production website routes, frontend-discovered API calls,
download registry, and fallbacks in the documented REST API

## Executive finding

The ClinPGx website exposes a substantially larger public read surface than the
34 operations in `https://api.clinpgx.org/openapi.json`. The production bundle
calls many undocumented `site/*` endpoints and links to binary/document
downloads. The documented API is a good fallback for ordinary entity metadata,
annotations, relationships, reports, and pathway JSON, but it is not a complete
substitute for allele/haplotype tables, composite prescribing tables, the
website's testing/VIP views, rendered label documents, or pathway graphics.

The download registry is the authoritative discovery list for these gaps. On
this audit date it returned 120 public entries (81,911,941 bytes) from:

```text
GET https://api.clinpgx.org/v1/data/file/data/?view=min
```

The registry snapshot is preserved in
[`clinpgx-download-registry-2026-09-05.json`](./clinpgx-download-registry-2026-09-05.json).
It is a catalog, not proof that every member has been downloaded, parsed, or
made available by an MCP tool. The implementation requirement is therefore
strict: every registry entry must be either (a) acquired, checksum-verified,
and retrievable locally, or (b) assigned a tested working fallback with an
explicit field/coverage limitation. No entry may disappear merely because it
is difficult to parse.

## Evidence and method

### Captured primary artifacts

- [Production HTML](https://www.clinpgx.org/downloads), captured at
  `/tmp/clinpgx_downloads.html`, references
  `https://www.clinpgx.org/assets/main-D-rWJZJl.js`.
- The downloaded frontend bundle is `/tmp/clinpgx_main.js`, 1,925,085 bytes,
  SHA-256 `372a6f95845729bfe79916b2eb028c8d104fbe35ababbf72a113d3d4639f061c`.
  Its route and API literals are the evidence for website-only calls below.
- The captured OpenAPI document is `/tmp/clinpgx_openapi.json`, SHA-256
  `d5945ffec00d99df4b10c8df605ff95ca1e1f360279541580c67097e93f06cc6`.
  It declares 34 operations; the normalized inventory is
  [`clinpgx-openapi-operations-2026-09-05.json`](./clinpgx-openapi-operations-2026-09-05.json).
- Prior read-only probes, response samples, download redirects, and artifact
  inspections are recorded in
  [`clinpgx-sources.md`](./clinpgx-sources.md),
  [`clinpgx-api-probe-fixtures-2026-09-05.json`](./clinpgx-api-probe-fixtures-2026-09-05.json),
  [`download-index-results.json`](./download-index-results.json), and
  [`representation-comparison.json`](./representation-comparison.json).

The initial bundle/registry inventory made no additional public requests while
the other research work was being coordinated. A later bounded follow-up used
the approved read-only budget, one request at a time and at most one request per
second. “Verified” below means verified by one of those probes or by a retained
local artifact; “bundle evidence” means the frontend calls the route, not that
this pass necessarily fetched every route.

### Bounded live endpoint probes

The following exact production URLs were fetched on 2026-09-05. Responses were
public, unauthenticated, and content-typed unless noted. Response sizes are
included to make a future fixture or regression check unambiguous.

| URL | Result | Machine-readable evidence |
| --- | --- | --- |
| `https://api.clinpgx.org/v1/site/gene/PA128` | 200 JSON, 13,627 bytes | `data` has `counts`, `frequencySources`, `gene`, `isPediatric`, `pedSummary`, `targets`; `gene.symbol=CYP2D6`. |
| `https://api.clinpgx.org/v1/site/gene/PA128/haplotypes` | 200 JSON, 29,476 bytes | `data.haplotypes` has 218 rows; `data.cpicS3File` is a current allele-definition XLSX URL; first row is `CYP2D6*1`, `functionTerm=Normal function`. |
| `https://api.clinpgx.org/v1/site/haplotype/PA165816576` | 200 JSON, 379,243 bytes | `data` has `alleleFile`, `alleleFunctionSource`, `counts`, `frequency`, `haplotype`, `isCpic`, `isPharmVar`, and `isPharmVarAllele`. |
| `https://api.clinpgx.org/v1/site/allele/PA166375082` | 200 JSON, 11,132 bytes | `data` has `allele`, `counts`, `frequencies`, and `variant`; the allele includes GRCh38/GRCh37 HGVS definitions and 23 frequency rows. |
| `https://api.clinpgx.org/v1/site/alleleFunction/PA128` | 200 `text/plain`, 199,718 bytes | Body is nevertheless a JSON list of 192 allele/haplotype function rows. An adapter must parse the body as JSON while preserving the misleading media type. |
| `https://api.clinpgx.org/v1/site/alleleFrequency` | 200 JSON, 2,207 bytes | List of 19 gene choices, including `TPMT` (`PA356`) and `CYP2D6` (`PA128`). |
| `https://api.clinpgx.org/v1/site/alleleFrequency/PA356` | 200 JSON, 285,296 bytes | 849 TPMT rows with allele, source, population, frequency, observed/total alleles, parent gene, and `rare`. |
| `https://api.clinpgx.org/v1/site/haplotypeFrequency/_download/PA356` | 200 TSV, 29,481 bytes | `Content-Disposition: ClinPGx-TPMT_All_Frequency.tsv`; columns are `Source`, `Population`, `Allele`, `Alleles Observed`, `Alleles Total`, `Frequency`. |
| `https://api.clinpgx.org/v1/site/haplotypeFrequency/_download/PA128` | 204 No Content | A valid no-data result for CYP2D6; do not convert it to an empty successful table without recording the 204. |
| `https://api.clinpgx.org/v1/site/vip/PA166170264` | 200 JSON, 3,856 bytes | `data` has `gene`, `vipId`, `vipTier=Tier 1`, and `vipSummary` for CYP2D6. |
| `https://api.clinpgx.org/v1/site/vips` | 200 JSON, 43,004 bytes | List of 69 VIP summaries with gene, gene ID, PMID, tier, summary, and VIP ID. |
| `https://api.clinpgx.org/v1/site/guideline/cpic/gene/CYP2C19?view=list` | 200 JSON, 597 bytes | Five CPIC Guideline objects, including CYP2C19/clopidogrel and CYP2C19/proton-pump-inhibitor entries. |
| `https://api.clinpgx.org/v1/site/tab/prescribingInfo/Gene/PA124?view=most` | 200 JSON, 561,835 bytes | Composite `dosingGuidelines`, `fdaPgxAssociations`, and `prescribingLabels` sections for CYP2C19. |
| `https://api.clinpgx.org/v1/site/guidelinesByDrugs` | 200 JSON, 132,128 bytes | 210 drug rows with CPIC/DPWG/other sources, genes, recommendation flags, and update dates. |
| `https://api.clinpgx.org/v1/site/prescribingTree` | 200 JSON, 59,100 bytes | 13 top-level ATC-like tree nodes; leaves retain chemical IDs and guideline/label/PGx-association flags. |
| `https://api.clinpgx.org/v1/site/labelAnnotation/PA166114907` | 200 JSON, 5,539 bytes | FDA bosutinib label object; includes `labelDocumentAvailable=true`, `highlightedLabelLink=Bosutinib_2025_01_15_FDA.pdf`, literature, and pediatric fields. |
| `https://api.clinpgx.org/v1/site/labelsByDrug` | 200 JSON, 749,922 bytes | 534 grouped drug-label rows with drug IDs, biomarker flags, source labels, and genes. |
| `https://api.clinpgx.org/v1/site/geneDrugAnnotation` | 200 JSON, 30 bytes | Current response is `data=[]`; this proves route availability, not absence of relationships in the database. |
| `https://api.clinpgx.org/v1/site/pathway/PA166163705` | 200 JSON, 138,444 bytes | Composite pathway object has `pathway`, `components`, `interactions`, `literature`, `counts`, and `relatedPathways`. |
| `https://api.clinpgx.org/v1/site/pathwayCategories` | 200 JSON, 1,827 bytes | Ten category rows; first reports 69 anticancer-agent pathways. |
| `https://api.clinpgx.org/v1/download/pathway/PA166163705?format=.tsv` | 200 TSV, 4,223 bytes | Attachment `PA166163705.tsv`, with From/To, reaction/control, cell, PMID, gene/drug/disease, and summary columns. |
| `https://api.clinpgx.org/v1/download/file/attachment/Bosutinib_2025_01_15_FDA.pdf` | 303 to S3, then 200 `application/pdf`, 1,244,260 bytes | Final `https://s3.pgkb.org/attachment/Bosutinib_2025_01_15_FDA.pdf` begins `%PDF-1.6`; this is a verified document fallback. |
| `https://s3.pgkb.org/pathway/PA166163705.png?versionId=yIzRb_Pbz0s03URgLlhHx9c7u1EzIIKp` | 200 `image/png`, 126,035 bytes | Final body has PNG signature; pathway JSON also supplied this versioned image URL. |

One control client using the Python `urllib` user agent received Cloudflare 1010
(`browser_signature_banned`) for `api.clinpgx.org`; curl with the production
probe client succeeded. The MCP must report transport/access failures rather
than treating them as missing data, and should retain the successful URL and
media-type evidence above as the machine fallback contract.

### Download URL contract

For a registry path `data/X`, the website constructs the public URL:

```text
https://api.clinpgx.org/v1/download/file/data/X
```

Prior probes followed the HTTP 303 to
`https://s3.pgkb.org/data/X` and retained response metadata. The API download
redirect and the registry listing are verified. Only the 15 archives listed in
the local import experiment were unpacked and indexed; the other registry rows
remain catalogued but not yet content-verified. A source implementation must
retain the original archive, `CREATED_*.txt`, `LICENSE.txt`, redirect metadata,
ETag/version information, and a local SHA-256.

## Website-only coverage matrix

“OpenAPI fallback” means a documented route can return some representation of
the information. It does not mean that it reproduces the website response,
HTML, table layout, binary asset, or all fields.

| Website feature | Frontend-discovered endpoint(s) | Registry/download evidence | OpenAPI fallback | Coverage status and limitation |
| --- | --- | --- | --- | --- |
| Gene allele/haplotype page and named-allele table | `GET https://api.clinpgx.org/v1/site/gene/{id}`; `GET https://api.clinpgx.org/v1/site/gene/{id}/haplotypes`; page routes `/gene/{id}`, `/haplotype/{id}`, `/allele/{id}` | `clinpgxHaplotypes.zip` (2026-09-05, 19,207 bytes); `haplotypes.zip` (2026-04-05, 571,302 bytes); `pharmcat.zip` (2026-09-05, 2,010,280 bytes); `cpic.alleles.json` (2026-09-05, 564,830 bytes); `cpic.alleles.csv` (26 bytes) | No allele/haplotype entity operation. `GET /data/gene/{id}` supplies gene metadata only. | **Verified site fallback; partial archive.** `/site/gene/PA128/haplotypes` returned 218 CYP2D6 rows and a current CPIC S3 XLSX URL. The compact archive has 625 star and 760 named definition rows. All 49 legacy per-gene XLSX files are structurally corrupt. PharmCAT adds versioned structured translations/functions for its supported genes, but is not a complete website mirror. The CPIC JSON has empty tested `guidelines` arrays and the CSV is header-only. |
| Allele function table | `GET https://api.clinpgx.org/v1/site/alleleFunction`; `GET https://api.clinpgx.org/v1/site/alleleFunction/{geneId}`; page `/alleleFunction` | `clinpgxHaplotypes.zip` includes HGVS but no function; `pharmcat.zip` has 1,294 named-allele function rows and 112,868 explicit diplotype rows for 20 genes; all 49 `haplotypes.zip` workbooks are corrupt | No documented allele-function operation. `GET /data/ontologyTerm?resource=alleleFunction` returns vocabulary terms, not allele rows. | **Verified site fallback; partial archive.** `/site/alleleFunction/PA128` returned 192 JSON rows despite `text/plain`; adapters must preserve the body and media-type quirk. Query PharmCAT with its explicit version/scope for offline fallback and quarantine the legacy XLSX archive. |
| Allele/haplotype frequencies | `GET https://api.clinpgx.org/v1/site/alleleFrequency`; `GET https://api.clinpgx.org/v1/site/alleleFrequency/{id}`; `GET https://api.clinpgx.org/v1/site/haplotypeFrequency/_download/{id}` with optional `source=` | `pharmgkb_haplotype_frequencies_UKBB.zip` (2026-09-05, 29,629 bytes); `pharmgkb_haplotype_frequencies_AllOfUs.zip` (2024-08-27, 58,421 bytes) | `GET /report/variantFrequency?fp={fingerprint}` is verified for variant frequencies, not haplotype-frequency pages. | **Verified site fallback.** TPMT (`PA356`) returned 849 JSON rows and a 29,481-byte TSV; CYP2D6 (`PA128`) correctly returned 204 No Content. Population/source/date are mandatory provenance fields. |
| CPIC guideline gene page, allele/function/frequency/diplotype tables | `GET https://api.clinpgx.org/v1/site/guideline/cpic/gene/{symbol}?view=list`; routes `/cpic/guidelines`, `/cpic/pairs`, `/cpic/resources` | `cpic.drug.mapping.zip` (2026-09-05, 2,617,846 bytes); `cpic.alleles.*`; `guidelineAnnotations.json.zip` (2026-08-05, 861,200 bytes); stale `dosingGuidelines.json.zip` and `.extended` (2021-10-05) | `GET /data/guidelineAnnotation` and item route return guideline annotations; `GET /data/summaryAnnotation`/`label` return related evidence. | **Verified site fallback; archive partial.** CYP2C19 returned five current CPIC guideline objects. Composite CPIC tables and nuances in the website/guideline presentation are not equivalent to the documented API. Stale dosing exports must be dated and labelled legacy. |
| Phenotype/diplotype prescribing table | `GET https://api.clinpgx.org/v1/site/tab/prescribingInfo/{objCls}/{id}?view=most`; `GET https://api.clinpgx.org/v1/site/guidelinesByDrugs`; `GET https://api.clinpgx.org/v1/site/prescribingTree` | `cpic.drug.mapping.zip`; `phenotypes.zip` (2026-08-05, 187,372 bytes); `guidelineAnnotations.json.zip`; legacy dosing guideline archives | `GET /data/guidelineAnnotation`, `/data/label`, `/data/summaryAnnotation` with relationship filters | **Verified site fallback; interpretation restricted.** CYP2C19 `PA124` returned all three composite sections (561,835 bytes); the global endpoints returned 210 drug rows and 13 tree roots. Never synthesize a dose or treatment action from a partial fallback. |
| PharmCAT pages/results/resources | External links `https://pharmcat.clinpgx.org/`, `/methods/`, and `/results?genotypeSelection=...`; website download link to PharmCAT artifacts | `pharmcat.zip` (2026-09-05, 2,010,280 bytes); `pharmcat-shc.zip` (2024-07-20, 1,813,082 bytes) | None in ClinPGx OpenAPI. | **Verified archive fallback; external UI separate.** Current `pharmcat.zip` provides 22-gene translations, 20-gene function/phenotype mappings, and 356 packaged prescribing-guidance entries; it is not a replacement for the external result service or interactive rendering. Record each artifact's own license: `pharmcat.zip` embeds CC BY-SA 4.0, while software distributions may use different terms. |
| VIP gene/variant content | Page routes `/vip`, `/vip/{id}`, `/gene/{id}`, `/variant/{id}`; `GET https://api.clinpgx.org/v1/site/vips`; `GET https://api.clinpgx.org/v1/site/vip/{id}`; `site/gene/{id}` and `site/variant/{id}` include VIP sections | `vip.zip` (2018-08-05, 256,078 bytes); `vip.variants.zip` (2018-08-05, 2,260 bytes); `genes.zip` has an `Is VIP` column | `GET /data/vip/{id}` is documented; `GET /data/gene/{id}` exposes VIP fields in rich objects. | **Verified site fallback; stale archive warning.** `/site/vip/PA166170264` returned the CYP2D6 Tier 1 gene summary and `/site/vips` returned 69 VIP rows. A prior probe passing that gene `vipId` to `/data/vip/{id}` returned HTTP 400 (`'vip' is an invalid ObjectType enum value`). Do not treat 2018 VIP archives as current VIP truth. |
| Drug-target/substrate/enzyme relationships | `GET https://api.clinpgx.org/v1/site/geneDrugAnnotation`; `GET https://api.clinpgx.org/v1/site/{interactionType}Interaction/{literatureId}`; `GET https://api.clinpgx.org/v1/site/connections/tab/{objCls}/{id}`; gene page `https://api.clinpgx.org/v1/site/gene/{id}` | `relationships.zip` (2026-08-05, 2,370,578 bytes); pathway JSON/TSV/BioPAX contain interaction context | `GET /data/connection`; `GET /report/pair/...`; `GET /report/connectedObjects/...` | **Partial.** `/site/geneDrugAnnotation` was reachable but returned `data=[]` in this snapshot. Generic connections and relationship exports are usable fallbacks, but no documented operation promises the website's typed substrate/enzyme/drug-target table. Preserve association/PK/PD facets and avoid implying direction from a reverse relationship row. |
| Drug-label list, label annotation, FDA PGx association view | `GET https://api.clinpgx.org/v1/site/labelsByDrug`; `GET https://api.clinpgx.org/v1/site/labelAnnotation/{id}`; `GET https://api.clinpgx.org/v1/site/tab/labelAnnotations/{objCls}/{id}`; `GET https://api.clinpgx.org/v1/site/fdaPgxAssociation`; page `/fdaPgxAssociations` | `drugLabels.zip` (2026-08-05, 59,489 bytes); registry also contains historical submission workbooks | `GET /data/label` and `/data/label/{id}` with source/gene/chemical filters; `GET /data/connection` for links | **Verified site fallback; archive partial.** `/site/labelsByDrug` returned 534 grouped rows, `/site/labelAnnotation/PA166114907` returned structured FDA label data, and `/site/fdaPgxAssociation` returned 124 associations plus three table types and a 2022-10-26 content date. |
| Highlighted drug-label PDF/document | Bundle creates `GET https://api.clinpgx.org/v1/download/file/attachment/{path}` links; page text identifies a highlighted label PDF | No dedicated highlighted-label bulk archive identified in the registry snapshot | None documented. | **Verified attachment fallback.** `https://api.clinpgx.org/v1/download/file/attachment/Bosutinib_2025_01_15_FDA.pdf` redirected to S3 and returned a 1,244,260-byte `application/pdf`; preserve redirect and document provenance. |
| Pathway data and downloadable graphics/documents | `GET https://api.clinpgx.org/v1/site/pathway/{id}`; `GET https://api.clinpgx.org/v1/site/pathwayCategories`; `/pathway/{id}/downloads`; `GET https://api.clinpgx.org/v1/download/pathway/{id}?format=.tsv`; `GET https://api.clinpgx.org/v1/download/file/{biopax|gpml}`; API-provided `pdfLink`, `illustratorLink`, `imageLink` | `pathways.json.zip` (2026-08-05, 1,929,507 bytes); `pathways-tsv.zip` (204,070 bytes); `pathways-biopax.zip` (634,025 bytes); `pathway-illustrator.zip` (2026-08-24, 414,302 bytes); `pathways-overview.zip` (2026-08-05, 703,834 bytes); `pathvisio.zip` (not imported in the prior experiment) | `GET /data/pathway/{id}` is verified for rich JSON metadata; `GET /report/pair/.../pathway` can find connecting pathways. | **Verified data/graphics sample; archive still partial.** `/site/pathway/PA166163705` returned composite JSON, `/download/pathway/PA166163705?format=.tsv` returned 4,223 bytes, and its versioned S3 PNG returned 126,035 bytes. Other binary links (PDF/Illustrator/GPML/BioPAX) require MIME/size/content verification. |
| Publications and literature occurrence views | `GET https://api.clinpgx.org/v1/site/publications/_download`; `GET https://api.clinpgx.org/v1/site/literature/{id}`; `GET https://api.clinpgx.org/v1/site/tab/literature/{objCls}/{id}`; `GET https://api.clinpgx.org/v1/site/pmid/{pmid}` | `publications.tsv` (2026-08-05, 102,107 bytes); `occurrences.zip` (2026-08-05, 2,904,200 bytes); annotated PMID archives | `GET /data/literature`, `/data/literature/{id}`, `/report/literatureId/{pmid}` | **Verified download/partial tabs.** `/site/publications/_download` returned a 112,735-byte TSV; core literature metadata and PMID mapping have API/archive fallbacks. Website-specific joined occurrence/literature tabs still need a tested `site` fallback. |
| AMP PGx testing table | `GET https://api.clinpgx.org/v1/site/testing?view=most&sources=AMP`; `GET https://api.clinpgx.org/v1/site/testing/_tsv`; page `/page/ampAllelesToTest` | `clinpgx.annotations.bed` (2026-09-05, 10,713,150 bytes) is related genome annotation data, not proven equivalent to the AMP table | No documented testing-table operation. | **Verified site fallback.** `/site/testing?view=most&sources=AMP` returned seven sections and `/site/testing/_tsv` returned a 2,625-byte `ClinPGx_AMP_alleles.tsv`; preserve its AMP tier/PMID/allele columns and do not substitute the BED. |
| Website search, browse, counts, and composite tabs | `site/ftSearch`, `site/autocomplete`, `site/browse/{kind}`, `site/browse/counts/{kind}`, `site/tab/*`, `site/linksTab/*`, `site/dataSources` | Canonical entity/annotation/relationship archives provide many underlying rows | Entity and annotation query/report operations cover selected primitives | **Partial.** Implement MCP search over the local index and cite the underlying artifact; do not claim byte-for-byte website search parity. |

## Verified fallback map

The following are the fallbacks that have actual local evidence rather than
being assumptions:

| Data family | Working fallback evidence | What it does not prove |
| --- | --- | --- |
| Genes, chemicals, variants, phenotypes | `genes.zip`, `chemicals.zip`, `drugs.zip`, `variants.zip`, `phenotypes.zip`; representative API objects `GET /data/gene/{id}`, `/data/chemical/{id}`, `/data/variant/{id}`, `/data/disease/{id}` | Export rows are projections. For CYP2C19, the API object was 10,040 bytes and the export row 1,028 bytes; API-only rich fields are not in the row. |
| Summary/variant/guideline/label annotations | `summaryAnnotations.zip`, `variantAnnotations.zip`, `guidelineAnnotations.json.zip`, `drugLabels.zip`; documented `/data/summaryAnnotation`, `/data/variantAnnotation`, `/data/guidelineAnnotation`, `/data/label` | The website's composite `site/*` response shape, HTML rendering, and attachments are not reproduced automatically. |
| Relationships and graph reports | `relationships.zip`; `/data/connection`, `/report/pair`, `/report/connectedObjects` | Relationship rows are emitted in both directions and do not by themselves imply biological direction. |
| Variant frequencies | `/report/variantFrequency?fp=rs9923231` returned 22 rows in the prior probe | This is a variant-fingerprint report, not a haplotype-population frequency table. |
| Pathway structured data | `pathways.json.zip`, `pathways-tsv.zip`, `pathways-biopax.zip`; `/data/pathway/{id}` | Binary diagrams and per-record PDF/Illustrator URLs still need safe retrieval tests. |
| Download discovery | `/v1/data/file/data/?view=min` returned 120 rows; `/v1/download/file/data/{name}` redirected to S3 | Registry presence and redirect success do not prove every archive has valid ZIP members or a complete schema. |

The directly tested website routes close several of the former “website-only”
gaps without pretending that one endpoint is a substitute for every archive:

| Website-only family | Tested working machine fallback | Required caveat |
| --- | --- | --- |
| Allele/haplotype and function | `/site/gene/PA128/haplotypes`, `/site/haplotype/PA165816576`, `/site/allele/PA166375082`, `/site/alleleFunction/PA128` | Keep IDs, HGVS assembly, function vocabulary, and the `text/plain` media-type quirk. |
| Frequencies | `/site/alleleFrequency/PA356`, `/site/haplotypeFrequency/_download/PA356`; `/site/haplotypeFrequency/_download/PA128` is an observed 204 | Frequency rows are source/population-specific; 204 means no current rows for that gene, not an API error. |
| CPIC and prescribing | `/site/guideline/cpic/gene/CYP2C19?view=list`, `/site/tab/prescribingInfo/Gene/PA124?view=most`, `/site/guidelinesByDrugs`, `/site/prescribingTree` | Retrieve and cite source records; do not infer patient treatment or dose. |
| VIP | `/site/vips`, `/site/vip/PA166170264` | The 2018 VIP archives and documented `/data/vip/{id}` behavior are not interchangeable with current site output. |
| Labels and FDA associations | `/site/labelsByDrug`, `/site/labelAnnotation/PA166114907`, `/site/fdaPgxAssociation`, plus the tested highlighted-label PDF redirect | Preserve label version/content date and PDF provenance; HTML is not a label document. |
| Pathways and graphics | `/site/pathway/PA166163705`, `/download/pathway/PA166163705?format=.tsv`, versioned S3 PNG | Other PDF/AI/GPML/BioPAX links remain per-record assets to validate individually. |
| AMP testing and publications | `/site/testing/_tsv`, `/site/publications/_download` | These are current website projections; retain the raw TSV and do not substitute the BED for AMP rows. |

## Required implementation disposition

The MCP data-release manifest should include all 120 registry entries, grouped
into explicit tiers:

1. `canonical_download`: page-listed current archives downloaded, validated,
   indexed, and exposed with artifact/member citations.
2. `approved_registry`: registry-only but publicly retrievable artifacts whose
   schema/license and source provenance have been checked.
3. `api_fallback`: website/API information served through a documented API
   route, with the exact field omissions and freshness semantics recorded.
4. `external_or_legacy`: PharmCAT, related-project files, historical exports,
   stale VIP/dosing files, and artifacts with unresolved license or schema
   issues. They remain discoverable with warnings but must not masquerade as
   current canonical data.
5. `unresolved`: no verified download or API fallback. The MCP response must
   say which public website feature is unavailable and include the canonical
   website URL; it must not return an empty successful result.

For every entry, store: logical filename, registry path, acquisition URL,
redirect/final URL, registry `lastModified` and size, HTTP metadata, SHA-256,
embedded creation/license markers, parser version, validation state, and a
fallback reference if it is not indexed. Unknown columns and unparsed binary
members are retained, not discarded.

## Unknowns and explicit gaps

- The captured `main` bundle contains code-split chunk names (for example
  `Haplotype-*.js`, `DiplotypeFunction-*.js`, and `GuidelineAnnotation-*.js`),
  but those chunks were not all captured. Their endpoints may add coverage not
  visible in this inventory.
- `https://api.cpicpgx.org/v1` appears in the bundle as a separate API base,
  but no OpenAPI specification or compatibility probe is part of this audit.
  It must not be silently treated as equivalent to `api.clinpgx.org`.
- External PharmCAT pages and downloads have their own service and licensing
  boundaries. A ClinPGx archive does not guarantee external result-service
  availability.
- Individual attachment URLs (`download/file/attachment/*`), PDF/Illustrator
  links, and website `site/*` responses need content-type, authorization,
  size-limit, and provenance tests before they can be an MCP fallback.
- Registry timestamps are heterogeneous: genes/chemicals and some allele files
  were generated on 2026-09-05, while guideline, label, pathway, and summary
  artifacts were generated on 2026-08-05. Never expose one global snapshot date.
- The prior audit observed count differences between API stats and exports and
  known defects in VIP and CPIC allele exports. These are data-quality warnings,
  not permission to silently prefer one representation.

## Acceptance checks for complete coverage

Before claiming website/API coverage, run a manifest-driven check that:

1. Enumerates every row in `clinpgx-download-registry-2026-09-05.json`.
2. Confirms each row has either a valid acquired artifact and parser status or a
   tested documented fallback URL and field-level limitation.
3. Fails if a registry row is omitted, silently skipped, or mapped to a generic
   “not found” result without distinguishing unavailable coverage.
4. Checks representative records for allele/function/frequency, phenotype and
   diplotype tables, CPIC and PharmCAT assets, VIP, drug labels, pathway data,
   and binary/document links.
5. Records the artifact/API source, retrieval time, checksum/version, and
   license class in every MCP response.
6. Keeps unsupported clinical interpretation out of the response: source text
   may be retrieved and cited, but the MCP must not turn it into a treatment,
   dose, or patient-management recommendation.

This matrix is a coverage boundary, not a claim that all website routes are
already implemented. The honest result for an unverified family is a cited,
structured limitation until a download or fallback is tested.
