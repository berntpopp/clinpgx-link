# ClinPGx website gap audit

**Probe date:** 2026-09-05  
**Scope:** public-reference retrieval only; no patient computation, PharmCAT UI,
or clinical recommendation evaluation.

This is a bounded follow-up to [`website-coverage.md`](website-coverage.md) and
the frozen route registry [`website-operations.json`](website-operations.json).
It does not change either file. The exact additional captures and machine
readable operation records are in
[`website-operations-additional-2026-09-05.json`](website-operations-additional-2026-09-05.json).

## Method and route provenance

The production bundle at `/tmp/clinpgx_main.js` (hash-bound by the earlier
website audit) lists the Vite code-split chunks and the API base URLs. Relevant
chunks were fetched from the already observed `www.clinpgx.org/assets/` URLs to
`/tmp/clinpgx_gap_*.js`. The chunks and main bundle expose the following route
families: `site/guideline`, `site/guidelineAnnotation`,
`site/variant/{id}/haplotypes`, `site/connections/tab`, `site/linksTab`,
`site/tab/{clinicalAnnotations|labelAnnotations|literature|pathways}`,
`site/{drugDrug|drugGene}Interaction`, `site/pathways`, and
`site/publications/clinpgx`. The bundle also explicitly sets
`https://api.cpicpgx.org/v1` as the CPIC API base.

All probes were public `GET` requests, sequential, with at least 1.15 seconds
between requests. Response bytes, media types, status, SHA-256, and assertions
are recorded in the additional registry. No credentials or write methods were
used.

## Findings

### Code-split guideline, allele, and diplotype data

The website guideline route is a working composite fallback, not merely an
HTML page:

`GET https://api.clinpgx.org/v1/site/guideline/PA166251454?view=base` returned
200 JSON (83,503 bytes; capture
`/tmp/clinpgx_gap_site_guideline.bin`). It contains 5 guideline annotations,
16 `fileArtifacts`, 3 flowcharts, 3 genes, 14 chemicals, literature,
publications, and supplements. The `fileArtifacts` list explicitly identifies
CPIC allele-definition, allele-function-reference, diplotype-phenotype,
frequency, and gene-phenotype resources, with their public
`files.cpicpgx.org` URLs. Thus the code-split tables are discoverable from a
tested site operation even when the linked spreadsheet is not indexed.

`GET .../site/guidelineAnnotation/PA166104948` returned 200 JSON (26,097
bytes; `/tmp/clinpgx_gap_site_guideline_annotation.bin`) with guideline,
CPIC-guideline, clinical-annotation count, and one GSI option set.

`GET .../site/variant/PA166154053/haplotypes` returned 200 JSON (208 bytes;
`/tmp/clinpgx_gap_site_variant_haplotypes.bin`) resolving the variant to the
stored haplotype `PA165980635`, name `*2`. This is a tested alias/join fallback
for allele-to-haplotype membership.

The explicit CPIC API base is also usable. `GET
https://api.cpicpgx.org/v1/` returned a PostgREST Swagger document (200,
182,612 bytes, 40 paths; `/tmp/clinpgx_gap_cpic_api.bin`). Bounded `?limit=1`
GET samples succeeded for `guideline`, `allele_definition`, `diplotype`,
`recommendation_view`, `pair_view`, `allele_guideline_view`,
`recommendation_alleles_view`, and `gene_result_lookup`. Their exact records,
fields, and hashes are in the additional registry. The OpenAPI document
advertises row filters, `select`, ordering, ranges, offsets, limits, and count
preferences; the probe used only `limit=1`.

**Coverage decision:** guideline/allele/diplotype public-reference data have a
working tested site composite and a separate CPIC API fallback. Do not claim
that one sample enumerates every table or every allowed filter.

### Joined literature and resource tabs

For the concrete gene `PA128` (`CYP2D6`), all four public tab families returned
JSON successfully:

| Family | Result | Evidence capture |
| --- | --- | --- |
| `site/connections/tab/gene/PA128` | 100 chemical and 100 disease rows; gene array empty | `/tmp/clinpgx_gap_site_connections_gene.bin` |
| `site/linksTab/gene/PA128` | Link groups including CTD, Ensembl, GO, HGNC, NCBI Gene, OMIM, and PharmVar Gene | `/tmp/clinpgx_gap_site_links_gene.bin` |
| `site/tab/clinicalAnnotations/gene/PA128` | 122 Clinical Annotation rows | `/tmp/clinpgx_gap_site_clinical_tab_gene.bin` |
| `site/tab/labelAnnotations/gene/PA128` | 204 Label Annotation rows | `/tmp/clinpgx_gap_site_label_tab_gene.bin` |
| `site/tab/literature/gene/PA128` | 2,188 Literature rows | `/tmp/clinpgx_gap_site_literature_tab_gene.bin` |
| `site/tab/pathways/gene/PA128` | 80 Pathway rows | `/tmp/clinpgx_gap_site_pathways_tab_gene.bin` |

The connections response is capped at 100 per category in this sample and has
no total field in the observed envelope; it must not be represented as a
complete relationship count. The literature and annotation tabs are useful
joined fallbacks, but their row counts are snapshot measurements, not stable
answer keys.

`GET .../site/publications/clinpgx` also returned 200 JSON (409,606 bytes;
`/tmp/clinpgx_gap_site_publications_clinpgx.bin`) with category groups, including
CPIC, PharmCAT, PharmVar, guideline, pathway, and VIP publications. The
category counts are recorded in the registry.

### Typed interaction tables

The bundle confirms the routes
`site/drugDrugInteraction/{literatureId}` and
`site/drugGeneInteraction/{literatureId}`. Requests for the captured literature
IDs `7144344` and `15174788` returned 200 envelopes with
`data.interactions=[]` (47 bytes, hash
`3d76565e6e81298ab6c92e735d9ff47322b4ddca138d80cbe54d265b046bd4ff`). This
proves route availability and a truthful empty result for those IDs, but does
not provide a non-empty typed interaction example.

**Decision:** typed interactions remain unresolved as a non-empty public
reference example. The tested connections tab, relationship archives, and
generic record/report operations are fallbacks for related entities, but must
not be silently relabeled as the typed interaction table. An MCP response
should preserve the empty result and its limitation.

### Pathway data and downloadable assets

The existing verified pathway JSON/TSV/PNG captures remain valid. This probe
closed two additional binary asset gaps using links returned by
`/site/pathway/PA166163705`:

- `GET https://api.clinpgx.org/v1/download/file/pathway/PA166163705.pdf?versionId=...`
  returned a 303 followed by an S3 PDF (200, `application/pdf`, 113,145 bytes;
  `/tmp/clinpgx_gap_pathway_pdf.bin`, SHA recorded in the registry).
- The analogous `.ai` URL returned a 303 followed by an Illustrator asset
  (200, `application/illustrator`, 264,728 bytes;
  `/tmp/clinpgx_gap_pathway_ai.bin`).

The pathway JSON's `biopaxLink` and `gpmlLink` values are relative
`submission/PS...` paths. Direct requests to the corresponding
`https://api.clinpgx.org/v1/submission/...` URLs returned 404 JSON (70 bytes).
The download catalog lists `pathways-biopax.zip` (142 BioPAX files), while
`pathways.json.zip` and `pathways-tsv.zip` provide indexed structured fallbacks;
the per-record BioPAX/GPML links therefore remain unresolved in this bounded
probe. The exact 404 captures and fallback statements are in the registry.

The public pathway list route also works:
`GET .../site/pathways` returned 285 Pathway rows (3,212,743 bytes;
`/tmp/clinpgx_gap_site_pathways.bin`). This supports discovery of IDs and
metadata, not automatic retrieval of every linked binary asset.

## Remaining boundaries

The following conclusions are supported by the captures above:

1. Site guideline pages and the CPIC PostgREST API provide working fallbacks
   for public guideline, allele, diplotype, recommendation, and joined-table
   retrieval. The MCP should expose the source boundary and the original URL.
2. Joined literature, clinical-annotation, label, pathway, links, and
   connection tabs are callable for a concrete object. Their observed limits
   and empty arrays must be preserved rather than inflated into totals.
3. PDF and Illustrator pathway assets are now verified through the API redirect
   route. PNG, TSV, and pathway JSON were verified in the prior registry.
4. Non-empty typed drug-drug/drug-gene interaction data and per-record GPML or
   BioPAX URL access remain unresolved. Archives or generic relationship
   records are fallbacks only when labelled as such.

This audit does not authorize clinical interpretation. It establishes public
data retrieval, source identity, response shape, and limitations for future MCP
coverage tests.
