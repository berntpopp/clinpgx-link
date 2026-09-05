# Task 3 critical re-review

Date: 2026-09-05
Reviewer: independent gpt-5.6-sol (`/root/fleet_research`)

## Scope and inputs

This is a read-only re-review of the two critical findings in
`docs/reviews/task3-review-round1.md` against:

- `041fb8bc7e4267a9fd1f57d14820e734f056d0d5` (`builder.py` SHA-256
  `ededa9688d5cf1f82bf4ab946ec839fc27c3bf8a30ae24f08c1a1d6184dd4d18`,
  `spreadsheets.py` SHA-256
  `caeb5aa64e7d8e668e593d9554f33b3283386f63f65c533101d73b7d28eaef52`);
- `7009f5e04eb0772e01737d18c739cebb1c3d9dbd` (`spreadsheets.py` SHA-256
  `8bde6c2f17cc047b46d3de8950879633f5c27c3669baef02c34973509a16cc77`,
  spreadsheet tests SHA-256
  `04843b0f55d3e1d55f0aa49fba54ed72c97d0e203113cfc4dc9c0c4e0b3caaa1`).

Commit `2e6c9cc148f8aaec47a52d9e85c4984a194a0d45` appeared concurrently during
the review and replaces the worksheet byte regex with Expat. I also checked that
revision (`spreadsheets.py` SHA-256
`943c2bed9d8cca33779a89c92545511c2885a234593803a580bdefe682c3e5ba`) so the
verdict below is not already obsolete.

The optional `.understand-anything/knowledge-graph.json` was absent, so analysis used
the frozen diffs, direct call paths, and focused in-memory probes. No visualization
overlay was written because this task authorizes only this report.

## Verdict

**NOT APPROVED for the Task 3 critical gate.** Critical finding 1 is addressed, but
critical finding 2 remains exploitable. The UTF-8-only admission and structural parser
close the demonstrated encoding and Unicode-namespace cases only for files that the
preflight already guesses are worksheets. Openpyxl identifies worksheets from OPC
relationships, not from that filename guess.

## Per-finding assessment

### 1. Partial rows, quarantine, and the global row limit: ADDRESSED

`041fb8b` gives each member a savepoint and rolls it back on quarantine or any
exception (`builder.py:461-476`). The normalized-row overflow is classified as
`resource_limit` before the over-limit row is inserted (`builder.py:357-371`), and
resource-limit failures cannot enter the legacy quarantine path (`builder.py:373-379`).
The quarantine exception is also restricted to the named legacy haplotype dataset.

The two focused rollback/global-limit regressions passed independently. I found no
remaining route by which a quarantined member can retain normalized rows or by which
successive members can reset the global count.

### 2. Nested OOXML preflight before materialization: NOT ADDRESSED

The code still decides what to validate from ZIP member spelling:

- only names ending in `.xml` receive UTF-8, NUL, declaration, DTD and entity checks
  (`spreadsheets.py:285-290`);
- only names beginning exactly with lowercase `xl/worksheets/` receive sheet, cell,
  dimension and merge checks (`spreadsheets.py:291-297`).

OOXML worksheet identity instead comes from `xl/_rels/workbook.xml.rels`. Openpyxl
follows those relationships and accepts worksheet parts with other valid package
names. Focused packages derived from a normal openpyxl workbook produced:

| Relationship target | Probe limit | Result |
|---|---:|---|
| `xl/foo.xml` | `max_cells=1` | Openpyxl loaded a 2-column, 1-data-row sheet |
| `xl/Worksheets/sheet1.xml` | `max_cells=1` | Openpyxl loaded a 2-column, 1-data-row sheet |
| `xl/worksheets/sheet1.dat`, UTF-16 XML | `max_cells=1` | Openpyxl loaded a 2-column, 1-data-row sheet |

The final case bypasses even the new encoding/DTD scan. The same relocation can hide
an oversized sparse coordinate or merge until openpyxl materializes it. Package byte
and part-count limits remain useful, but they do not enforce the promised worksheet
cell/merge bounds.

At `7009f5e`, a valid non-ASCII namespace prefix was a second independent bypass of
the ASCII byte regex: openpyxl recognized the expanded SpreadsheetML name while the
preflight saw no cell. The focused regression reached the monkeypatched
`load_workbook` rather than raising the expected bound error. Concurrent `2e6c9cc`
correctly replaces that regex with a namespace-aware Expat start-element handler
(`spreadsheets.py:178-258`) and closes this specific route. It does not change worksheet
part discovery, so the three relationship-target probes still bypass preflight.

Within XML that actually reaches `_canonical_xml_bytes`, I found no valid UTF-8 lexical
form that bypasses its literal DOCTYPE/entity rejection. That does not mitigate a
worksheet part which never reaches the function.

## Required correction and tests

Resolve and validate the actual worksheet targets from the bounded OPC relationship
graph before calling openpyxl. An intentionally narrower alternative is to fail closed
unless every workbook worksheet relationship resolves one-to-one to a unique,
canonical `xl/worksheets/*.xml` member and every such member is scanned. In either
case, apply encoding/DTD and structural bounds to the resolved parts, and reconcile
the resolved sheet count with the workbook.

Add pre-openpyxl regressions for:

- a worksheet relocated to another XML path;
- case-variant worksheet directories;
- a worksheet with a non-XML suffix, including UTF-16 content;
- oversized dimensions and merges through each accepted target form;
- a Unicode namespace prefix (covered by `2e6c9cc`) and ordinary/default namespace
  forms, ensuring namespace handling cannot skip or double-count real worksheet
  elements.

Focused repository tests during this review produced nine passes and one failure: all
seven tests frozen in `7009f5e` plus both builder rollback tests passed; the subsequently
added Unicode-namespace regression failed against `7009f5e` by reaching openpyxl. The
reported 171 real-workbook compatibility run was not independently repeated because a
security-boundary bypass already keeps the gate closed.
