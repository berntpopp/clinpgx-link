# ClinPGx source-selection experiment and decision

Measured 2026-09-05 on the development workstation. These are source-access and
prototype-index measurements, not production MCP end-to-end benchmarks.

## Actual tests

- Downloaded 15 real current-core ZIP archives and parsed their actual TSV/CSV/JSON
  members into a disk-backed SQLite/FTS5 experimental index.
- Timed five maximal API objects three times with new connections and three times
  using a reused HTTP/2 connection. No application response cache was used.
- Timed five whole archive downloads three times through the published download
  redirects to `https://s3.pgkb.org/data/`.
- Queried actual matching gene, chemical and variant identifiers and annotation
  records locally; compared export/API fields and one complete guideline object.
- Tried unfiltered API gene enumeration and wildcard/prefix criteria, to test
  whether the API could cheaply supply a replacement bulk snapshot.

Raw network timings: [clinpgx-network-timings.tsv](clinpgx-network-timings.tsv).
Local timings and archive hashes: [download-index-results.json](download-index-results.json).
Field comparison and cache timings: [representation-comparison.json](representation-comparison.json).
Isolated repeat with per-source dates:
[representation-comparison-isolated.json](representation-comparison-isolated.json).
Scripts: [benchmark_downloads.py](benchmark_downloads.py),
[compare_representations.py](compare_representations.py).

The committed local timing JSON is the summary projection of the full per-member
report: each archive's `members` array is replaced by member count, indexed member
and record counts, and maximum field size. The benchmark now emits that projection
directly as `download-index-summary.json`; other measurements are unchanged. The
reported run used the 15 archive filenames listed in that JSON. Additional archives
downloaded later are not part of this result. The raw report and database are at
`/tmp/clinpgx-index-experiment-20260905-v2/`; the earlier directory without `-v2`
contains the failed partial import and is not evidence for any reported result.

## Network results

Median seconds, three transfers per cell. Downloads include redirects and full
body transfer; API calls use `view=max` and transfer a single identified record.
These represent different workloads, not interchangeable payloads.

| Resource | API, new connection | API, HTTP/2 session | Whole export download |
|---|---:|---:|---:|
| CYP2C19 / genes | 0.571 | 0.510 | 0.541 |
| clopidogrel / chemicals | 0.520 | 0.344 | 0.490 |
| rs4244285 / variants | 0.506 | 0.374 | Not repeatedly timed |
| Annotation 655386913 / summary annotations | 1.139 | 0.890 | 0.531 |
| Guideline PA166104948 / guidelines JSON | 0.437 | 0.353 | 0.581 |

The HTTP/2 gene series includes initial connection establishment on its first
sample; all later transfers reuse the connection. The TSV retains `new_connections`
so the distinction is auditable. This small sample describes this host and time,
not a latency SLA or a statistically powered population estimate. Server/CDN cache
state is uncontrolled; repeated download timings are not uncached-origin timings.

The full genes ZIP was 2,905,541 bytes and contained 25,041 genes. Building a bulk
gene snapshot by one API call per known accession would require at least 3.48 hours
at the published 2 requests/second limit, before retries. This is a conditional
lower bound for that strategy, not an exhaustive impossibility claim for all API
query combinations. The tested unfiltered `/data/gene?view=min` returned HTTP 400
`Missing criteria`; tested `symbol=*` and `accessionId=PA` did not enumerate genes.
The documented gene collection declares accessionId, symbol and view only.

## Local indexing and queries

The first prototype run failed on a real chemicals TSV field exceeding Python's
131,072-character CSV default. Raising the experiment's explicit field ceiling to
8 MiB allowed the complete run; the largest measured field was 148,743 characters.
This is a production ingestion regression case, not a reason to silently skip rows.

- Initial parse/insertion/FTS/secondary-index build: **5.201 seconds**.
- Indexed units: **444,508 tabular rows and complete JSON documents**, not unique
  biomedical entities. The exports overlap and JSON documents contain nested data.
- SQLite size: **347,369,472 bytes** (~331 MiB), including preserved JSON and FTS.
- Original archives, non-tabular members and README files are additional storage.
- Each query was run once and then 100 warm repetitions; results were fetched and
  JSON-decoded in the timing. OS cache was not flushed, so “first query” is not a
  cold-machine measurement. A production MCP adds shaping, fencing and transport.

| Local operation | First query, ms | Warm median, ms | Returned units |
|---|---:|---:|---:|
| CYP2C19 exact symbol | 0.085 | 0.005 | 1 gene row |
| clopidogrel exact name | 0.013 | 0.006 | 1 chemical row |
| rs4244285 exact name | 0.013 | 0.004 | 1 variant row |
| CYP2C19 + clopidogrel summary filters | 2.962 | 2.661 | 6 annotation rows |
| Full-text conjunction, first page | 0.522 | 0.240 | 20 rows/documents |
| Gene/drug relationship, without dedicated index | 54.384 | 53.414 | 1 relationship row |

Adding one SQLite expression index for relationship endpoints took **101 ms** and
reduced that relationship query to **0.0054 ms** median. The query plan confirms
that SQLite used the new index. The database then occupied 351,010,816 bytes.
This demonstrates that fast specific joins need a few explicit indexes; simply
storing all JSON and assuming every query will be fast is insufficient.
An independent repeat on a copied database recreated the index in 97 ms and
measured 0.0056 ms median query time. The revised script refuses an existing
experiment destination and never mutates the source database or times an index
creation no-op. These index-build timings exclude the database copy itself.

In-memory decoding of already-fetched API JSON is also cheap: decoding these bytes
in the initial experiment took 0.010–0.364 ms. No cache lookup, TTL, or complete
cache-serving path was benchmarked. Downloads therefore win on broad coverage and cold
access to previously unseen records; their advantage is not that cached API
objects cannot be served quickly.

## Data equivalence and maintenance findings

1. **Exports are not uniformly full API records.** CYP2C19's export row was 1,028
   JSON-serialized bytes versus a 10,040-byte API envelope. The API exposes VIP
   narrative, history, allele-related metadata and structured references. The
   tested summary's main TSV row was 546 bytes versus a 130,283-byte API envelope;
   related export tables carry additional evidence and allele text, requiring joins.
   Byte differences are representation measurements, not field-loss percentages.
2. **Some exports are excellent rich mirrors.** For CPIC guideline PA166104948,
   all 22 common keys in the exported `guideline` object equalled the API object,
   with no missing/extra keys and matching version 49. This proves this record's
   equality only; broader coverage and freshness need separate verification.
3. **Snapshots differ within one download session.** Genes/chemicals were generated
   September 5, while tested guidelines/summary annotations were generated August 5.
   One global “data downloaded today” flag would misrepresent freshness.
4. **Export semantics need validation.** All 25,041 gene rows have `Is VIP=Yes`,
   but A1BG's API record has `vipTier=Undefined` and no VIP identifier/summary.
   Preserve the raw export, flag the anomaly, and use verified API VIP metadata for
   normalized VIP answers. Do not claim every gene is a curated VIP.
5. **Source counts differ.** The observed API statistics report 25,047 genes, versus
   25,041 rows in the downloaded archive. Neither source can be silently presented
   as a complete copy of the other.
6. **Simple parsers need explicit format limits.** Large TSV fields, JSON wrapper
   objects and multiple evidence tables are real maintenance costs. Store original
   fields plus a small normalized index rather than a large brittle fixed schema.
7. **The API also needs maintenance.** It warns that response schemas may change;
   criteria are required at runtime even when not marked required in OpenAPI.
   Bounded response sizes, no-result status mapping, cache freshness and a shared
   request scheduler remain necessary for an API-only implementation.

## Decision

Use **downloads for broad discovery and local indexed search**, plus a **bounded,
cached API client for rich current details, reports and export gaps**. The measured
download/setup costs for the covered core prototype are small enough to justify
the local index. Only five archive downloads were repeatedly timed, and the
15-archive index is not a complete download-surface import. Comprehensive
access still requires the coverage matrix and tested fallbacks. API-only is useful
as a zero-bootstrap operating mode, but is not the primary bulk-access strategy.
Downloads-only has not been shown to support the required rich/current coverage
without a complete mapping of the different representations.

Keep the two source representations explicit. Refresh each artifact atomically,
retain its hash and publication date, preserve unknown export fields, and record
source anomalies. Use API cache provenance and TTLs; do not silently enrich an
old export and call the mixture a single coherent snapshot. A selected source's
failure must produce an explicit limitation or error.

This design has more code than API-only, but avoids a large API crawl, reduces
routine upstream traffic, and isolates format changes behind a small acquisition
and indexing boundary. It follows the fleet's Python/FastMCP/SQLite patterns.
The implementation and local-agent benchmarks still need to prove the complete
MCP behavior, output budgets and usability; this experiment does not claim that
work is finished.
