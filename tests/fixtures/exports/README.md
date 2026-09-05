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
- `clinpgx_haplotypes.tsv`: `clinpgxHaplotypes.zip` → `clinpgx_haplotypes.tsv`, reduced
  to two representative rows with the actual six-column header.

The source archives reported creation/registry dates independently; tests must never
derive one global publication date from these excerpts. Upstream ClinPGx/PharmGKB
content carries CC BY-SA 4.0/data-usage obligations described in the project research
documents. These transformed excerpts require attribution and change indication.

`adversarial/` is project-created malformed input and must never be described as
ClinPGx source data. Large fields, hostile ZIP paths, symlink members, formula cells,
merged cells, and missing spreadsheet cells are generated in tests so their precise
boundary values remain obvious.
