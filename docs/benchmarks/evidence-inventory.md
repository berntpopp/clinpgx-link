# Benchmark evidence audit

Audit date: 2026-09-05. Input: `tests/eval/cases.json` (18 cases, 24
`source_refs`, 16 unique paths). Relative paths below were resolved from the
repository root. No network access or archive extraction was used.

## Result

All 16 referenced paths exist in the current workspace. All five declared
`sha256` values match the measured file digest. The remaining references do
not declare a digest in `cases.json`; their measured digests are recorded for
future pinning. The `/tmp` evidence is machine-local and is not repository
content, so a clean runner must provision those files or use an equivalent
fixture-seed/live fallback before running the corresponding cases.

## Referenced files

| Cases | Kind(s) | Path (resolved) | Bytes | SHA-256 | Declared SHA |
|---|---|---|---:|---|---|
| PGX-01,05,06,09,12 | fixture, fixture_seed | `/tmp/cpic_guidelines_min.json` | 13,061 | `0e2f4b5113fa4b83f1da88b96e271c3fec354f43d385223577eed3b5fe1cb3a3` | not declared |
| PGX-02 | fixture | `/tmp/summary_loe3_min.json` | 4,903,830 | `e09c5db505498d2fd19bb61cf30fb14b4d22b7b259c0b20f47d9aa286efbb3f8` | not declared |
| PGX-03,10 | fixture, fixture_seed | `/tmp/persist_variant_3.json` | 4,199 | `633aa88b03fb81cc4b5929430bc6a90826070f7e07bb9c8f01a818c4398b4884` | not declared |
| PGX-04,06,16 | archive_member | `/tmp/clinpgx_archives/pharmcat.zip` | 2,010,280 | `f437451a9cb75c80b51ba29819eb7e2e0be44a8e973e1fdb10928b7a5969607e` | not declared |
| PGX-07,11 | contract | `docs/superpowers/specs/2026-09-05-source-access-contract.md` | 14,735 | `98722a6292715da09ace1d5b54cc72cf41cb2148796334c48aa55363f841e63e` | not declared |
| PGX-08 | fixture | `/tmp/gene_base.json` | 3,849 | `a52f818abe2812e6da91ad8a00a57cb437112a80700e8f1aeb8fca2fa0f5ada7` | not declared |
| PGX-08 | fixture | `/tmp/gene_max.json` | 11,141 | `ffa87b25f17206bd9773fe68867ef1dbc5feb3605f31cb82b3ed29a802cb9a37` | not declared |
| PGX-13 | website_capture | `/tmp/clinpgx_site_allele_function.text` | 199,718 | `ef1174122231f6f230a367730d1f8b276c09f7fc1d3a149c1421af95102da7e6` | **match** |
| PGX-14 | website_capture | `/tmp/clinpgx_site_allele_frequency_PA356.json` | 285,296 | `d745754108749b4c3c4a9ead408b8765906d72fea51eaa0e92b8fa2d08fc71e2` | **match** |
| PGX-14 | website_capture | `/tmp/clinpgx_site_haplotype_frequency_PA356.tsv` | 29,481 | `8e9c093c26322022cc38e9e2d48e5b2598ce830c22c961c3ad5e13d75bd3baa9` | **match** |
| PGX-15 | website_capture | `/tmp/site_label_annotation_curl.bin` | 5,539 | `c5a706b100867595f337f746a7d9da0bc656da94cf835cae67ff387e211db0d4` | **match** |
| PGX-15 | asset_capture | `/tmp/site_label_pdf_final.bin` | 1,244,260 | `c6cdcf7e8305308f56b3f85bc85c52d0d86cf219dd166a51a96472583904c158` | **match** |
| PGX-16 | research_note | `docs/research/auxiliary-formats.md` | 25,275 | `2903884324d161e7efbc103a39ab392a5aa4f4a128dd87cbcdd5ee57c3f57848` | not declared |
| PGX-17 | fixture | `/tmp/persist_summary_3.json` | 130,283 | `3defd6747ae8a618d18b30773a8a415474aba59361d7e08e2299266b4cf6d960` | not declared |
| PGX-18 | index_measurement | `/tmp/clinpgx-index-experiment-20260905-v2/download-index-results.json` | 276,920 | `3b6a024a61ad70ac70b51f25d51454a0561bc9b9701e9c170da674216a5f99f5` | not declared |
| PGX-18 | research_note | `docs/research/api-vs-download-decision.md` | 10,032 | `ceb38ed765407adad200c0cc4c352361fa5322b50beecd35a833c3eded3ee499` | not declared |

## Required archive members

The ZIP central directory was read in place; no member was extracted or
decompressed. Required members are present:

| Cases | Archive member | Compressed bytes | Uncompressed bytes | CRC-32 |
|---|---|---:|---:|---|
| PGX-04 | `phenotypes.json` | 1,043,820 | 34,430,081 | `57226984` |
| PGX-06 | `allele_translations.json` | 63,941 | 3,973,239 | `0823dcbb` |
| PGX-16 | `prescribing_guidance.json` | 901,102 | 11,207,404 | `c3c4cbca` |

## Counts

- Missing referenced paths: **0** (24 references checked; 16 unique paths).
- Missing required archive members: **0** (3 checked).
- Declared SHA-256 mismatches: **0** (5 declared, 5 matching).
- References without a declared SHA-256: **19**.
