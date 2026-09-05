"""Throwaway source-selection experiment, not the production ingestion design.

Run with Python 3.12+: benchmark_downloads.py ARCHIVE_DIR OUTPUT_DIR.
Uses actual ZIPs, preserves complete tabular/JSON records and records timings.
"""

import csv
import hashlib
import io
import json
import platform
import sqlite3
import statistics
import sys
import time
import zipfile
from pathlib import Path


def main() -> None:
    csv.field_size_limit(8 * 1024 * 1024)
    archive_dir, output_dir = map(Path, sys.argv[1:])
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "experiment.sqlite"
    if db_path.exists():
        raise SystemExit("Use a new output directory; experiment database already exists.")
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE records (dataset TEXT, member TEXT, ordinal INT, identity TEXT, label TEXT, payload TEXT)")
    db.execute("CREATE VIRTUAL TABLE search USING fts5(body, content='')")
    report = {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
              "method": "Disk SQLite, all TSV/CSV rows and full JSON documents; no network in this phase",
              "archives": [], "queries": {}}
    start = time.perf_counter()
    for path in sorted(archive_dir.glob("*.zip")):
        began = time.perf_counter()
        entry = {"file": path.name, "compressed_bytes": path.stat().st_size,
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "members": []}
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                info = {"name": member.filename, "uncompressed_bytes": member.file_size}
                if member.filename.startswith("CREATED_"):
                    entry["archive_created_marker"] = archive.read(member).decode().strip()
                if member.filename.endswith((".tsv", ".csv")):
                    with archive.open(member) as stream:
                        reader = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline=""),
                                                delimiter="\t" if member.filename.endswith(".tsv") else ",")
                        info["columns"] = reader.fieldnames
                        count = 0
                        largest_field = 0
                        for count, row in enumerate(reader, 1):
                            largest_field = max(largest_field, *(len(str(v)) for v in row.values()))
                            identity = next((row[key] for key in (
                                "PharmGKB Accession Id", "Variant ID", "Summary Annotation ID",
                                "Variant Annotation ID", "Entity1_id") if key in row), str(count))
                            label = next((row[key] for key in ("Symbol", "Variant Name", "Name") if key in row), "")
                            payload = json.dumps(row, ensure_ascii=False)
                            cur = db.execute("INSERT INTO records VALUES (?,?,?,?,?,?)",
                                             (path.name, member.filename, count, identity, label, payload))
                            db.execute("INSERT INTO search(rowid,body) VALUES (?,?)", (cur.lastrowid, " ".join(str(v) for v in row.values())))
                        info.update(indexed_records=count, representation="tabular", largest_field_chars=largest_field)
                elif member.filename.endswith(".json"):
                    raw = archive.read(member)
                    value = json.loads(raw)
                    # Preserve a whole JSON document; do not pretend its nested arrays are TSV rows.
                    identity = Path(member.filename).stem
                    payload = json.dumps(value, ensure_ascii=False)
                    cur = db.execute("INSERT INTO records VALUES (?,?,?,?,?,?)",
                                     (path.name, member.filename, 1, identity, "", payload))
                    db.execute("INSERT INTO search(rowid,body) VALUES (?,?)", (cur.lastrowid, payload))
                    info.update(indexed_records=1, representation="json_document",
                                root_keys=list(value) if isinstance(value, dict) else None)
                else:
                    info.update(indexed_records=0, representation="source_artifact")
                entry["members"].append(info)
        db.commit()
        entry["parse_insert_fts_ms"] = round((time.perf_counter() - began) * 1000, 3)
        report["archives"].append(entry)
    began = time.perf_counter()
    db.execute("CREATE INDEX lookup ON records(dataset,member,label)")
    db.execute("CREATE INDEX identity ON records(dataset,identity)")
    db.commit()
    report["secondary_index_ms"] = round((time.perf_counter() - began) * 1000, 3)
    report["total_build_ms"] = round((time.perf_counter() - start) * 1000, 3)
    report["db_bytes"] = db_path.stat().st_size
    report["indexed_records"] = db.execute("SELECT count(*) FROM records").fetchone()[0]
    cases = {
        "gene_CYP2C19": ("SELECT identity,payload FROM records WHERE dataset=? AND member=? AND label=?",
                          ("genes.zip", "genes.tsv", "CYP2C19")),
        "chemical_clopidogrel": ("SELECT identity,payload FROM records WHERE dataset=? AND member=? AND label=?",
                                ("chemicals.zip", "chemicals.tsv", "clopidogrel")),
        "variant_rs4244285": ("SELECT identity,payload FROM records WHERE dataset=? AND member=? AND label=?",
                              ("variants.zip", "variants.tsv", "rs4244285")),
        "summary_CYP2C19_clopidogrel": (
            "SELECT identity,payload FROM records WHERE dataset='summaryAnnotations.zip' AND member='summary_annotations.tsv' "
            "AND instr(';'||json_extract(payload,'$.Gene')||';',';CYP2C19;')>0 "
            "AND instr(';'||json_extract(payload,'$.\"Drug(s)\"')||';',';clopidogrel;')>0", ()),
        "fts_CYP2C19_clopidogrel": (
            "SELECT r.identity,r.payload FROM search JOIN records r ON r.rowid=search.rowid WHERE search MATCH ? LIMIT 20",
            ('"CYP2C19" AND "clopidogrel"',)),
        "relationships_CYP2C19_clopidogrel": (
            "SELECT identity,payload FROM records WHERE dataset='relationships.zip' "
            "AND json_extract(payload,'$.Entity1_id')='PA124' AND json_extract(payload,'$.Entity2_id')='PA449053'", ()),
    }
    for name, (sql, args) in cases.items():
        times = []
        for _ in range(101):
            began = time.perf_counter()
            rows = db.execute(sql, args).fetchall()
            parsed = [(identity, json.loads(payload)) for identity, payload in rows]
            times.append((time.perf_counter() - began) * 1000)
        report["queries"][name] = {
            "first_query_ms": round(times[0], 4),
            "warm_median_ms": round(statistics.median(times[1:]), 4),
            "warm_p95_ms": round(sorted(times[1:])[94], 4),
            "repeats": 100, "rows": len(rows), "ids": [r[0] for r in rows],
            "payload_bytes": sum(len(r[1].encode()) for r in rows),
            "sample": parsed[:1], "sql": sql, "parameters": args,
        }
    db.close()
    (output_dir / "download-index-results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    summary = {**report, "archives": [{
        **{k: v for k, v in entry.items() if k != "members"},
        "member_count": len(entry["members"]),
        "indexed_records": sum(m["indexed_records"] for m in entry["members"]),
        "indexed_members": sum(m["indexed_records"] > 0 for m in entry["members"]),
        "largest_field_chars": max(m.get("largest_field_chars", 0) for m in entry["members"]),
    } for entry in report["archives"]]}
    (output_dir / "download-index-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "archives"}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
