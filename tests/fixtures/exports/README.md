# Export fixture provenance

`sourced/` contains compact, manually selected excerpts from public ClinPGx exports
downloaded on 2026-09-05. They preserve the original field names and representative
values but are deliberately reduced test fixtures, not complete upstream artifacts:

- `genes.tsv`: `genes.zip` → `genes.tsv`, rows for CYP2C19 and A1BG;
- `summary_*.tsv`: `summaryAnnotations.zip`, annotation 655384602 plus a second
  multivalue row and its evidence/allele children;
- `PA166363221.json`: `guidelineAnnotations.json.zip` → `PA166363221.json`, reduced
  while preserving the `{citations, guideline}` wrapper and representative nested
  identities;
- `pathways.json`: `pathways.json.zip` → `pathways.json`, reduced to two array-root
  records;
- `pharmcat_phenotypes.json`: `pharmcat.zip` → `phenotypes.json`, reduced to one gene
  and representative nested diplotype/function rows;
- `clinpgx_haplotypes.tsv`: reduced legacy-name fixture for the haplotype profile's
  six-column shape. Captured current-source evidence on 2026-09-05 records
  `/tmp/clinpgx_archives/clinpgxHaplotypes.zip` with SHA-256
  `3f0cea6d5de126c751a6c11e389ec165d17ce4107911e7e48e8c96b89bbe52de`; its
  `clinpgxHaplotypes_named_alleles.tsv` and
  `clinpgxHaplotypes_star_alleles.tsv` members both have the exact headers
  `Accession ID,Gene,Allele Name,HGVS,Structural Variation,AMP Level`.

The source archives reported creation/registry dates independently; tests must never
derive one global publication date from these excerpts. Upstream ClinPGx/PharmGKB
content carries CC BY-SA 4.0/data-usage obligations described in the project research
documents. These transformed excerpts require attribution and change indication.

`adversarial/` is project-created malformed input and must never be described as
ClinPGx source data. Large fields, hostile ZIP paths, symlink members, formula cells,
merged cells, and missing spreadsheet cells are generated in tests so their precise
boundary values remain obvious.
