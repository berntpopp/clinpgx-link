# ClinPGx auxiliary allele, PharmCAT, mapping, and frequency formats

Research date: 2026-09-05 (Europe/Berlin). This note records read-only inspection of the actual published archives, not just their download-page descriptions. It is a design input; no production importer was implemented.

## Outcome

Five of the six inspected artifacts contain usable structured data. They support local queries for current basic allele definitions, CPIC drug identifiers, PharmCAT allele translation/function/phenotype rules, PharmCAT prescribing guidance, and two biobank frequency datasets. They do **not** collectively reproduce every ClinPGx website table or every documented API object.

The legacy [`haplotypes.zip`](https://s3.pgkb.org/data/haplotypes.zip) is an important exception: its outer ZIP is sound, but all 49 embedded XLSX workbooks are structurally corrupt. It must be quarantined as an opaque source asset. It cannot be the fallback for allele-function data. The current [`clinpgxHaplotypes.zip`](https://s3.pgkb.org/data/clinpgxHaplotypes.zip), [`pharmcat.zip`](https://s3.pgkb.org/data/pharmcat.zip), and verified `/site/alleleFunction/{geneId}` routes are the usable structured alternatives, with different scopes.

## Method and immutable source identity

The public S3 objects below were fetched directly from the paths returned by the ClinPGx download registry. No authenticated or mutation endpoints were used. The outer archives were checked with Python `zipfile.testzip()`, path-traversal and symlink checks, and SHA-256. TSV files were parsed with Python 3.12.9 `csv` using tab delimiters and strict row-width checks. JSON was parsed with the standard library. XLSX files were opened from archive bytes with `openpyxl` 3.1.5 and inspected for sheets, dimensions, formulas, merged cells, tables, and hidden dimensions.

All six outer ZIPs passed CRC testing; none contained absolute paths, `..` traversal, or symlinks.

| Registry object | Registry last modified | Bytes compressed / uncompressed | SHA-256 | Embedded release marker and license |
| --- | ---: | ---: | --- | --- |
| [`haplotypes.zip`](https://s3.pgkb.org/data/haplotypes.zip) | 2026-04-05 01:02:02 -07:00 | 571,302 / 1,743,862 | `c21506ec00c9962f025b00a81c52e258a9c0a7f2ea13e4d6c39c04b20e8164b1` | `Created on 04/05/2026 at 01:01:55 PDT`; embedded CC BY-SA 4.0 |
| [`clinpgxHaplotypes.zip`](https://s3.pgkb.org/data/clinpgxHaplotypes.zip) | 2026-09-05 00:37:38 -07:00 | 19,207 / 78,531 | `3f0cea6d5de126c751a6c11e389ec165d17ce4107911e7e48e8c96b89bbe52de`; `Created on 09/05/2026 at 00:37:36 PDT`; embedded CC BY-SA 4.0 |
| [`cpic.drug.mapping.zip`](https://s3.pgkb.org/data/cpic.drug.mapping.zip) | 2026-09-05 00:31:40 -07:00 | 2,617,846 / 2,729,457 | `39a7c2993455ccacabbbb0e7d0de6eca3798c3789f04978109554a58325f3a02`; `Created on 09/05/2026 at 00:31:37 PDT`; embedded CC BY-SA 4.0 |
| [`pharmcat.zip`](https://s3.pgkb.org/data/pharmcat.zip) | 2026-09-05 00:32:08 -07:00 | 2,010,280 / 49,611,807 | `f437451a9cb75c80b51ba29819eb7e2e0be44a8e973e1fdb10928b7a5969607e`; `Created on 09/05/2026 at 00:31:40 PDT`; embedded CC BY-SA 4.0 |
| [`pharmgkb_haplotype_frequencies_AllOfUs.zip`](https://s3.pgkb.org/data/pharmgkb_haplotype_frequencies_AllOfUs.zip) | 2024-08-27 11:16:06 -07:00 | 58,421 / 138,171 | `6c9004a5cf04d1c7381c276e7779564cc059a276690d2d0b82177f5e9c7b2eee`; root directory `AllOfUs_Frequencies_v7`; no embedded `CREATED` or license file |
| [`pharmgkb_haplotype_frequencies_UKBB.zip`](https://s3.pgkb.org/data/pharmgkb_haplotype_frequencies_UKBB.zip) | 2026-09-05 00:37:37 -07:00 | 29,629 / 156,544 | `52a913fdd764c046b53495cf65b1d86358fb87bbbfa71f93137010cde83c3116`; `Created on 09/05/2026 at 00:37:36 PDT`; embedded CC BY-SA 4.0 |

The release timestamp must be attached to each source asset, not assigned globally. The All of Us archive demonstrates why: it remains a 2024 registry object and explicitly describes the v7 cohort, while several neighboring exports were generated on 2026-09-05. For All of Us, license provenance should be recorded as unresolved from the archive rather than silently inheriting another archive's embedded license.

## Legacy `haplotypes.zip`: all inner workbooks are unusable

The exact outer members are `LICENSE.txt`, `CREATED_2026-04-05.txt`, and these 49 XLSX files:

```text
UGT2B15_03_15_13.xlsx
UGT1A7_03_17_14.xlsx
UGT1A6_03_17_14.xlsx
SLC6A4_04_14_14.xlsx
NAT1_3_12_2014.xlsx
HLA-A.xlsx
CYP1A1_07_01_15.xlsx
HLA-C.xlsx
HLA-DQB1.xlsx
CACNA1S_Haplotypes.xlsx
HLA-DRB1.xlsx
HLA-DRB3.xlsx
HLA-DRB5.xlsx
HLA-DPB1.xlsx
PS216403-1183944905.xlsx
ABCG2_haplotypes.xlsx
UGT1A3_11_01_16_publication.xlsx
VKORC1_Haplotypes.xlsx
F5_Haplotypes.xlsx
IFNL3_Haplotypes-PS216649-1451510270.xlsx
UGT1A1_Haplotypes.xlsx
CYP3A5.xlsx
UGT1A9_07_16_14.xlsx
GSTM1-hsPA166048674-1296601127.xlsx
UGT1A4-hsPA166117587-1184026901.xlsx
UGT1A8_03_17_14.xlsx
GSTT1_Haplotypes.Dec2014.xlsx
CYP3A7_Haplotypes.xlsx
G6PD_Haplotypes-PS216417-1451985841.xlsx
RYR1_Haplotypes.xlsx
HLA-B_Haplotypes.xlsx
HLA-DQA1_Haplotypes.xlsx
DPYD.xlsx
CYP2A6.xlsx
CYP2C8.xlsx
CYP2B6.xlsx
CYP2D6.xlsx
CYP2C9.xlsx
CYP1A2.xlsx
NAT2.xlsx
CFTR_Haplotypes.xlsx
SLCO1B1.xlsx
MT-RNR1_Haplotypes.xlsx
CYP4F2.xlsx
CYP3A4.xlsx
F2_Haplotype.xlsx
NUDT15_Haplotypes.xlsx
TPMT_Haplotypes.xlsx
CYP2C19.xlsx
```

Every inner file begins with the normal ZIP local-header signature `PK 03 04`, but every one fails both Python `zipfile` and `openpyxl`:

| Test result | Count |
| --- | ---: |
| `Bad magic number for central directory` | 31 |
| `Bad offset for central directory` | 18 |
| Successfully opened XLSX | 0 |

The workbook byte streams contain 369,460 occurrences of `EF BF BD`, the UTF-8 encoding of the Unicode replacement character. Each workbook contains at least 1,573 such sequences (median 5,444; maximum 52,253). This is evidence that invalid/undecodable source bytes were replaced before publication; it is not merely an XLSX library incompatibility. The missing original bytes cannot be reconstructed from `U+FFFD`, so attempts to re-encode or repair these workbooks would fabricate data.

Consequences:

- Workbook sheet names, table schemas, formulas, cached formula results, and merged headers cannot be determined from this archive.
- Store the outer archive hash, member metadata, and validation errors. Do not expose rows from it and do not label it a working function-table fallback.
- A future release with a different hash must be validated again; do not permanently blacklist the filename.

## Current `clinpgxHaplotypes.zip`: basic definitions, not functions

Exact members are:

```text
LICENSE.txt
CREATED_2026-09-05.txt
README.pdf
clinpgxHaplotypes_star_alleles.tsv
clinpgxHaplotypes_named_alleles.tsv
```

`README.pdf` is not a PDF. It is a 95-byte JSON error object:

```json
{"status":"fail","data":{"errors":[{"message":"No page with key: downloadHgvsHaplotypeHelp"}]}}
```

Both TSVs have the exact schema:

```text
Accession ID | Gene | Allele Name | HGVS | Structural Variation | AMP Level
```

| File | Rows | Genes | HGVS nonempty | Structural Variation nonempty | AMP Level nonempty |
| --- | ---: | ---: | ---: | ---: | ---: |
| `clinpgxHaplotypes_star_alleles.tsv` | 625 | 10 | 571 | 54 | 58 |
| `clinpgxHaplotypes_named_alleles.tsv` | 760 | 9 | 760 | 0 | 13 |

The star-allele gene counts are CYP2D6 218, CYP2C9 94, NAT2 66, CYP2B6 49, CYP2C19 49, TPMT 49, SLCO1B1 47, CYP4F2 23, NUDT15 22, and CYP3A5 8. The named-allele counts are RYR1 346, G6PD 187, CFTR 104, DPYD 83, MT-RNR1 24, UGT1A1 10, and ABCG2, CACNA1S, and VKORC1 2 each.

`Accession ID` and `(Gene, Allele Name)` are unique within each table. AMP values are `AMP Tier 1` or `AMP Tier 2`: the star table has 27 and 31 respectively; the named table has 7 and 6. A representative row is:

```text
PA165818750 | CYP2B6 | *1 | NG_007929.1:g.= | <empty> | <empty>
```

Import these with a specialized tabular adapter. Preserve the raw row and empty strings, then project accessions, gene, allele, HGVS, structural variation, and AMP level. HGVS and structural variation are alternative description modes in the star table; an importer must not require both. This archive contains no allele-function or phenotype column and therefore cannot answer function/phenotype questions by itself.

## `cpic.drug.mapping.zip`: uniform, simple XLSX tables

The archive has 173 members: `LICENSE.txt`, `CREATED_2026-09-05.txt`, and 171 files named `drug.mapping.<drug_slug>.xlsx`. The 171 drug labels and PharmGKB accessions are each unique. One compound filename uses a triple-underscore separator, `drug.mapping.sulfamethoxazole___trimethoprim.xlsx`, while its cell value is `sulfamethoxazole / trimethoprim`; the cell value, not the filename slug, must be treated as the published label.

All 171 workbooks opened successfully and have identical physical structure:

- exactly one visible worksheet, named `Sheet1`;
- exactly 5 rows by 4 columns;
- no formulas, merged ranges, Excel table objects, hidden rows, hidden columns, or hidden sheets;
- exact header `Drug or Ingredient | Source | Code Type | Code`;
- four source rows in order: RxNorm/RxCUI, DrugBank/Accession Number, ATC/ATC Code, and PharmGKB/PharmGKB Accession ID.

Representative `drug.mapping.clopidogrel.xlsx` values are:

```text
Drug or Ingredient | Source   | Code Type             | Code
clopidogrel         | RxNorm   | RxCUI                 | 32968
clopidogrel         | DrugBank | Accession Number      | DB00758
clopidogrel         | ATC      | ATC Code              | B01AC04
clopidogrel         | PharmGKB | PharmGKB Accession ID | PA449053
```

Missing code cells occur for DrugBank in 20 workbooks, ATC in 7, and RxNorm in 6; PharmGKB is complete. Thirty-six code cells contain comma-separated multiple identifiers, principally ATC codes. The normalized projection should therefore be one `drug_mapping` row per identifier, while preserving the original unsplit cell and workbook row. Never treat a blank code as an empty-string identifier.

A generic loss-preserving XLSX reader is sufficient for this release, with a specialized validator enforcing the one-sheet schema and allowed source/code-type pairs. It should reject schema drift into quarantine rather than guess columns.

## `pharmcat.zip`: the strongest local function/phenotype fallback

Exact members are `LICENSE.txt`, `CREATED_2026-09-05.txt`, `allele_translations.json` (3,973,239 bytes), `phenotypes.json` (34,430,081 bytes), and `prescribing_guidance.json` (11,207,404 bytes). All three JSON files parse without error and carry version `2026-09-05-00-25`.

### `allele_translations.json`

This is an array of 22 gene documents: ABCG2, CACNA1S, CFTR, CYP2B6, CYP2C19, CYP2C9, CYP2D6, CYP3A4, CYP3A5, CYP4F2, DPYD, F2, F5, G6PD, IFNL3, NAT2, NUDT15, RYR1, SLCO1B1, TPMT, UGT1A1, and VKORC1.

Top-level fields are:

```text
chromosome, formatVersion, gene, genomeBuild, modificationDate,
namedAlleles, refSeqChromosomeId, refSeqGeneId, refSeqProteinId,
variants, version
```

All documents state `genomeBuild=GRCh38.p14`. Across the file there are 1,381 `namedAlleles` and 1,236 `variants`. Named-allele fields are `cpicAlleles[]`, `id`, `name`, `reference`, and `structuralVariant`. Variant fields are `chromosome`, `chromosomeHgvsName`, `cpicAlleles[]`, `cpicPosition`, `rsid`, and `sequenceLocationId`.

Representative ABCG2 objects are:

```json
{"cpicAlleles":["G"],"id":"PA166287823","name":"rs2231142 reference (G)","reference":true,"structuralVariant":false}
{"chromosome":"chr4","chromosomeHgvsName":"g.88131171G>T","cpicAlleles":["G","T"],"cpicPosition":88131171,"rsid":"rs2231142","sequenceLocationId":1451999247}
```

### `phenotypes.json`

This is an array of 20 gene documents: TPMT, CACNA1S, F5, CYP3A5, CYP2D6, UGT1A1, CYP2B6, MT-RNR1, CYP2C9, SLCO1B1, NAT2, CYP2C19, CYP3A4, DPYD, G6PD, F2, CFTR, RYR1, ABCG2, and NUDT15.

Each document contains `activityValues` (object), `diplotypeFunctions` (array), `diplotypes` (array), `gene`, `haplotypes` (object), `namedAlleles` (array), and `version`. Totals are:

| Structure | Entries | Semantics |
| --- | ---: | --- |
| `namedAlleles` | 1,294 | Allele name, function value, optional activity value, lookup key |
| `haplotypes` | 1,294 | Allele name to function/activity lookup value |
| `diplotypeFunctions` | 212 | Function combination rule to phenotype and optional activity score |
| `diplotypes` | 112,868 | Explicit diplotype, multiset-like allele key, result, phenotype, and optional activity score |
| `activityValues` | 342 | Allele name to activity value |

The current payload contains 20 distinct allele-function labels and 25 phenotype labels; these include the usual normal/decreased/no/increased/uncertain function and poor/intermediate/normal/rapid/ultrarapid metabolizer terms, plus gene- or disease-specific outcomes. A generic metabolizer enum would lose information and must not be used.

Representative CYP2C19 objects demonstrate the two distinct rule forms:

```json
{"name":"*1","functionValue":"Normal function","lookupKey":"Normal function"}
{"activityScore":"n/a","lookupKey":{"No function":1,"Decreased function":1},"name":"CYP2C19 one Decreased function allele and one No function allele","phenotype":"Likely Poor Metabolizer"}
{"diplotype":"*1/*2","diplotypekey":{"*1":1,"*2":1},"generesult":"Intermediate Metabolizer","lookupkey":"Intermediate Metabolizer","phenotype":"Intermediate Metabolizer"}
```

Use a specialized JSON adapter. Retain each complete per-gene JSON document, then project allele-function, activity-value, function-rule, and diplotype tables. Preserve maps such as `diplotypekey` and `lookupKey` as multiplicities, not concatenated strings. Stream or incrementally insert the 34.4 MB phenotype payload; do not materialize it in every MCP result.

### `prescribing_guidance.json`

The root is `{version, guidelines[]}`. There are 356 guideline entries: 58 CPIC, 67 DPWG, and 231 FDA. They contain 3,808 recommendations, 424 citations, and 356 nonempty ClinPGx URLs. Entry keys are `citations`, `guideline`, `recommendations`, and `url`; recommendations have:

```text
alternateDrugAvailable, classification, dosingInformation, id, implications,
lookupKey, moreText, name, objCls, otherPrescribingGuidance, population,
relatedChemicals, source, text, version
```

This is not equivalent to the monthly `guidelineAnnotations.json.zip`. In the inspected releases, that archive has 219 unique guideline IDs; only 124 overlap PharmCAT, 95 occur only in the monthly guideline archive, and 232 occur only in PharmCAT. PharmCAT intentionally includes FDA-oriented prescribing guidance and a selected CPIC/DPWG set. Both sources must remain separately queryable with provenance; neither may overwrite the other by ID-free title matching.

Store each complete entry as raw JSON and project guideline identity/source/version, gene and chemical relations, recommendation lookup keys, implication text, classification, population, citations, and URLs. Rich HTML/Markdown fields must be preserved as source content but should be rendered or sanitized only at presentation time.

## All of Us v7 frequency export

The archive contains 75 ZIP members, but only 31 primary TSV files. The rest are directories, `.DS_Store`, and `__MACOSX` resource-fork metadata plus `README.md`. Ignore macOS metadata for row import but retain it in the source manifest so the archive can be losslessly audited.

The README states that frequencies were estimated from All of Us whole-genome sequencing v7 (`n=245k`) with PharmCAT 2.4.0. This study/version provenance is distinct from the registry modification timestamp.

The primary members group exactly as follows:

- `activity_score/`: DPYD and CYP2C9 (2 files, 77 data rows).
- `allele/`: ABCG2, CACNA1S, CYP2C19, CYP2C9, CYP3A5, CYP4F2, DPYD, G6PD, NUDT15, RYR1, SLCO1B1, TPMT, UGT1A1, and VKORC1 (14 files, 2,240 rows).
- `phenotype/`: `2C_CLUSTER`, ABCG2, CACNA1S, CYP2C19, CYP2C9, CYP3A5, CYP4F2, DPYD, G6PD, NUDT15, RYR1, SLCO1B1, TPMT, UGT1A1, and VKORC1 (15 files, 413 rows).

All rows have the expected width and unique `(file, biogeographic_group, measure label)` keys. The exact schemas are:

```text
activity_score: biogeographic_group, activity_score, n_activity_score, frequencies, in_cpic, notes, n_subjects_genotyped
allele:         biogeographic_group, allele, n_haplotype, frequencies, in_cpic, notes, n_subjects_genotyped
phenotype:      biogeographic_group, phenotype, n_phenotype, frequencies, in_cpic, notes, n_subjects_genotyped
```

A representative allele row is:

```text
afr | *1 | 107385 | 0.9953377576746255 | <empty> | <empty> | 53944.0
```

All 2,730 current rows leave `in_cpic` and `notes` blank. Preserve those columns and blank/null state because future releases may populate them. `n_subjects_genotyped` must be stored as a decimal-capable value or source text: 52 G6PD allele rows contain half values such as `41521.5`, consistent with X-chromosome allele-denominator handling. Coercing it to integer would corrupt published data.

## UK Biobank frequency export

Exact primary members are 15 `<gene>_UKBB_frequencies.tsv` files for ABCG2, CACNA1S, CFTR, CYP2C19, CYP2C9, CYP3A5, CYP4F2, DPYD, G6PD, IFNL3, RYR1, SLCO1B1, TPMT, UGT1A1, and VKORC1; `NOTES_ON_FREQUENCIES.tsv`; `README.pdf`; `LICENSE.txt`; and `CREATED_2026-09-05.txt`.

The 15 frequency files have 3,554 rows, no row-width errors, and unique `(gene file, Population, Allele)` keys. Every file uses:

```text
Source | Population | Allele | Alleles Observed | Alleles Total | Frequency
```

Example:

```text
UKBB | African American/Afro-Caribbean | rs2231142 reference (G) | 3745 | 3852 | 0.972222
```

Published frequency equals observed/total within six-decimal rounding (maximum observed difference approximately `5.09e-7`). There are 2,417 explicit zero-observation/zero-frequency rows. Preserve those zeros: they are different from a missing row or an allele/population excluded by quality control.

`NOTES_ON_FREQUENCIES.tsv` has 14 rows with `Subject | Note`. The README explains that failed genetic positions were set missing, some allele estimates became impossible or skewed, and affected genes/alleles are named in this notes table. It also states that the dataset covers 200,044 UK Biobank participants using integrated whole-exome, microarray, and imputed data; PharmCAT version is at least 2.2.1. `Overall` and `Other` are explicitly not ClinPGx biogeographic groups.

Notes are data-quality records, not documentation decoration. Normalize them and associate them by the exact published `Subject` string while preserving the raw relation because subjects may name a gene or a gene-plus-allele. Never infer zero frequency for missing/QC-excluded combinations.

## Import and MCP exposure design

### Loss-preserving asset layer

Every release artifact should first enter an immutable `source_asset` layer with URL, registry timestamp, embedded creation timestamp, hash, byte size, media type, embedded license text or unresolved-license marker, member manifest, parser version, and validation status. Store the original archive outside the query database or in content-addressed object storage. A failed specialized parser must not make the source disappear from the catalog.

ZIP ingestion must reject unsafe paths and symlinks, enforce member and expanded-size limits, check CRC before parsing, and record ignored metadata members. Do not trust the filename extension: the broken `README.pdf` and corrupt inner XLSX files prove this is necessary.

For a generic workbook representation, retain workbook/member hash; worksheet name and state; cell coordinate, raw value, type, number format, hyperlink, and comment; merged ranges; hidden row/column metadata; table ranges; and named ranges. Load formulas once with `data_only=False` and, if formulas exist, a second time with `data_only=True` to capture any stored cached result. Do not evaluate formulas. `openpyxl` does not guarantee that cached values exist or are current, so formula text is authoritative and cached results require an explicit `present/stale-unknown` status. The current CPIC mapping workbooks contain zero formulas and zero merged headers; the corrupt haplotype workbooks provide no inspectable evidence about either.

For JSON, retain each source document unchanged and build versioned projections. Unknown keys should survive round-trip and trigger schema-drift telemetry rather than being silently dropped. For TSV, retain raw source row number and text fields alongside typed numeric projections.

### Specialized query projections

| Projection | Natural key and essential fields | Answers safely |
| --- | --- | --- |
| `allele_definition` | source release + accession; gene, allele name, HGVS, structural variation, AMP tier | “What basic definitions are published for this gene/allele?” |
| `pharmcat_translation` | PharmCAT version + gene + named allele/variant key; GRCh38 position, RefSeq IDs, rsID, CPIC alleles | “How does this release translate variants to named alleles?” |
| `pharmcat_allele_function` | version + gene + allele; function, activity value, lookup key | “What function/activity does PharmCAT assign?” |
| `pharmcat_diplotype` | version + gene + canonical allele multiset; diplotype, result, phenotype, score | “What phenotype does this exact supported diplotype map to?” |
| `drug_identifier_mapping` | release + PharmGKB drug accession + source + individual code | “What RxNorm/DrugBank/ATC/PharmGKB identifiers are mapped?” |
| `prescribing_guidance` | PharmCAT version + guideline/recommendation ID; source, population, lookup keys, implications, recommendation text, citations | “What source recommendation does PharmCAT package for this exact lookup?” |
| `biobank_frequency` | dataset release + measure type + gene/cluster + group + label; numerator, denominator, frequency, source row | “What did All of Us v7 or UKBB report?” |
| `frequency_warning` | dataset release + exact subject + note | “What quality warning applies to this estimate?” |

Recommended MCP surface:

- `catalog_data_releases` and `read_source_asset_metadata` expose provenance, freshness, licenses, validation state, member lists, and source URLs.
- `query_allele_definitions` returns current TSV definitions with source/version and clearly states that function is not in that table.
- `query_pharmcat_allele_functions`, `translate_pharmcat_alleles`, and `lookup_pharmcat_phenotype` query specialized PharmCAT projections and always include the PharmCAT version/genome build.
- `query_drug_identifier_mappings` expands multi-code cells but can return the original cell on request.
- `query_pharmcat_prescribing_guidance` searches the packaged guidance without presenting it as the complete ClinPGx guideline corpus.
- `query_biobank_frequencies` requires or returns dataset identity and measure type; it must never blend All of Us and UKBB into an unlabeled frequency.
- `read_auxiliary_source_asset` streams original valid assets under byte/range limits. The corrupt legacy archive remains downloadable only with a prominent validation failure and must not be parsed into apparent facts.

## Coverage gained and remaining gaps

| Data family | Working archive fallback | Coverage judgment |
| --- | --- | --- |
| Basic allele identifiers and HGVS/SV definitions | `clinpgxHaplotypes.zip` | Useful, current, and queryable for 1,385 rows across 19 gene sets; not a full allele-page mirror and no function/phenotype fields. |
| Allele translation and variant-to-allele definitions | PharmCAT `allele_translations.json` | Strong structured fallback for 22 PharmCAT-supported genes on GRCh38.p14; not evidence of every ClinPGx/PharmVar allele or older genome-build representation. |
| Allele function, activity, diplotype, phenotype | PharmCAT `phenotypes.json` | Strong usable fallback for 20 genes; semantics are PharmCAT's supported interpretation and version, not a complete canonical mirror of every website allele-function/frequency row. Live `/site/alleleFunction/{geneId}` remains the direct website fallback where available. |
| CPIC drug identifier mappings | `cpic.drug.mapping.zip` | Full coverage of the 171 workbooks in that release; CPIC resource subset, not all ClinPGx chemicals. |
| Prescribing guidance | PharmCAT `prescribing_guidance.json` plus monthly guideline archive/API | Complementary partial sets; their IDs demonstrably differ, so both are required. Clinical source, version, population, and exact text must remain attached. |
| All of Us and UKBB frequencies | Two frequency archives | Queryable dataset-specific frequency snapshots for limited genes/measures. They do not contain every frequency visible on all ClinPGx allele pages or other population studies. |
| Legacy per-gene haplotype XLSX | None from `haplotypes.zip` | Unusable in the tested release. Quarantine and fall back to current TSV, PharmCAT JSON, or a verified live `/site` route. |

The key completeness rule is therefore explicit routing, not a single “downloads are complete” flag. For each research answer, the MCP should report which release and semantic source answered it, what richer source remains available, and whether the result is full for that source's declared scope or only a fallback subset.
