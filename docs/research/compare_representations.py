"""Compare real export/API representations and warm JSON-cache costs."""

import csv
import hashlib
import io
import json
import sqlite3
import statistics
import sys
import time
import zipfile
from pathlib import Path


def main() -> None:
    archive_dir, api_dir, db_path, output = map(Path, sys.argv[1:])
    cases = {
        "gene": ("genes.zip", "genes.tsv", "PharmGKB Accession Id", "PA124", "gene_cyp2c19_max"),
        "chemical": ("chemicals.zip", "chemicals.tsv", "PharmGKB Accession Id", "PA449053", "chemical_clopidogrel_max"),
        "variant": ("variants.zip", "variants.tsv", "Variant ID", "PA166154053", "variant_rs4244285_max"),
        "summary": ("summaryAnnotations.zip", "summary_annotations.tsv", "Summary Annotation ID", "655386913", "summary_clopidogrel_cyp2c19_max"),
    }
    report = {"comparisons": {}, "notes": ["Cache costs measure JSON decode only, not HTTP, MCP, shaping or source-freshness checks."]}
    csv.field_size_limit(8 * 1024 * 1024)
    for name, (filename, member, key, identity, probe) in cases.items():
        with zipfile.ZipFile(archive_dir / filename) as archive:
            source_date = next(archive.read(n).decode().strip() for n in archive.namelist() if n.startswith("CREATED_"))
            with archive.open(member) as stream:
                row = next(row for row in csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig"), delimiter="\t") if row[key] == identity)
        raw = (api_dir / f"clinpgx_probe_{probe}_r1.json").read_bytes()
        api = json.loads(raw)["data"]
        timings = []
        for _ in range(100):
            began = time.perf_counter()
            json.loads(raw)
            timings.append((time.perf_counter() - began) * 1000)
        report["comparisons"][name] = {
            "record_id": identity, "api_sha256": hashlib.sha256(raw).hexdigest(),
            "api_bytes": len(raw), "export_row_bytes": len(json.dumps(row).encode()),
            "api_keys": sorted(api), "export_columns": sorted(row),
            "warm_api_json_decode_median_ms": statistics.median(timings),
            "api_identity": api.get("id"), "api_name": api.get("name"),
            "export_created_marker": source_date,
            "api_probe_date": "2026-09-05",
        }
    with zipfile.ZipFile(archive_dir / "guidelineAnnotations.json.zip") as archive:
        export = json.loads(archive.read("PA166104948.json"))
    raw = (api_dir / "clinpgx_probe_guideline_clopidogrel_cyp2c19_max_r1.json").read_bytes()
    api = json.loads(raw)["data"]
    guideline = export["guideline"]
    report["guideline"] = {
        "record_id": "PA166104948", "export_wrapper_keys": sorted(export),
        "api_only_keys": sorted(api.keys() - guideline.keys()),
        "export_only_keys": sorted(guideline.keys() - api.keys()),
        "equal_common_keys": sorted(k for k in api.keys() & guideline.keys() if api[k] == guideline[k]),
        "different_common_keys": sorted(k for k in api.keys() & guideline.keys() if api[k] != guideline[k]),
        "export_version": guideline.get("version"), "api_version": api.get("version"),
        "export_citation_count": len(export["citations"]),
    }
    # Isolate the index experiment. A rerun must not time CREATE INDEX IF NOT EXISTS.
    experiment_path = output.with_suffix(".sqlite")
    if experiment_path.exists():
        raise SystemExit("Use a new output path; index experiment database already exists.")
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db = sqlite3.connect(experiment_path)
    source.backup(db)
    source.close()
    db.execute("DROP INDEX IF EXISTS experiment_pair")
    db.commit()
    started = time.perf_counter()
    db.execute("CREATE INDEX experiment_pair ON records(json_extract(payload,'$.Entity1_id'),json_extract(payload,'$.Entity2_id')) WHERE dataset='relationships.zip'")
    db.commit()
    report["relationship_index_build_ms"] = (time.perf_counter() - started) * 1000
    query = "SELECT identity,payload FROM records WHERE dataset='relationships.zip' AND json_extract(payload,'$.Entity1_id')=? AND json_extract(payload,'$.Entity2_id')=?"
    timings = []
    for _ in range(100):
        started = time.perf_counter()
        rows = [(identity, json.loads(payload)) for identity, payload in db.execute(query, ("PA124", "PA449053"))]
        timings.append((time.perf_counter() - started) * 1000)
    report["relationship_indexed_query"] = {"median_ms": statistics.median(timings), "rows": len(rows),
                                              "query_plan": db.execute("EXPLAIN QUERY PLAN " + query, ("PA124", "PA449053")).fetchall()}
    report["indexed_db_bytes"] = experiment_path.stat().st_size
    db.close()
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
