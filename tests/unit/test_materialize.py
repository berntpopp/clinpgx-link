"""Authenticated immutable installation and offline rollback integration tests."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.unit.test_release_licenses import licenses_value
from tests.unit.test_source_manifest import _canonical, manifest_value


@dataclass(frozen=True)
class FixtureRelease:
    release_input: object
    artifact_digest: str
    tag: str
    runtime_digest: str | None = None


def _private(path: Path) -> Path:
    path.mkdir(mode=0o700)
    return path


def _database(
    path: Path,
    *,
    tag: str,
    snapshot: str,
    archive: bytes,
    member: bytes,
    parser_status: str = "indexed",
) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA application_id=1129072728;
        PRAGMA user_version=1;
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE dataset(dataset_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,
          byte_count INTEGER NOT NULL,license_id TEXT NOT NULL,record_count INTEGER NOT NULL);
        CREATE TABLE source_archive(dataset_id TEXT PRIMARY KEY,byte_count INTEGER NOT NULL,
          sha256 TEXT NOT NULL,raw BLOB NOT NULL);
        CREATE TABLE source_member(dataset_id TEXT,path TEXT,byte_count INTEGER NOT NULL,
          compressed_bytes INTEGER NOT NULL,sha256 TEXT NOT NULL,raw BLOB NOT NULL,
          parser_status TEXT NOT NULL,PRIMARY KEY(dataset_id,path));
        CREATE TABLE record(record_pk INTEGER PRIMARY KEY,dataset_id TEXT NOT NULL);
        CREATE TABLE membership(record_pk INTEGER NOT NULL REFERENCES record(record_pk));
        """
    )
    archive_sha = hashlib.sha256(archive).hexdigest()
    member_sha = hashlib.sha256(member).hexdigest()
    connection.executemany(
        "INSERT INTO metadata VALUES (?,?)", [("release_tag", tag), ("snapshot_id", snapshot)]
    )
    connection.execute(
        "INSERT INTO dataset VALUES (?,?,?,?,?)",
        ("data/genes.zip", archive_sha, len(archive), "clinpgx-cc-by-sa-4.0", 1),
    )
    connection.execute(
        "INSERT INTO source_archive VALUES (?,?,?,?)",
        ("data/genes.zip", len(archive), archive_sha, archive),
    )
    connection.execute(
        "INSERT INTO source_member VALUES (?,?,?,?,?,?,?)",
        ("data/genes.zip", "genes.tsv", len(member), 7, member_sha, member, parser_status),
    )
    connection.execute("INSERT INTO record VALUES (1,'data/genes.zip')")
    connection.execute("INSERT INTO membership VALUES (1)")
    connection.commit()
    connection.close()
    path.chmod(0o444)


def _release(
    tmp_path: Path,
    marker: str,
    previous: str | None = None,
    *,
    byte_count_delta: int = 0,
    consumed: bool = True,
    indexed: bool = True,
    parser_status: str = "indexed",
) -> FixtureRelease:
    from clinpgx_link.releases.bundle import BundleLimits, pack_bundle
    from clinpgx_link.releases.identity import release_identity
    from clinpgx_link.releases.licenses import canonical_bytes as rights_bytes
    from clinpgx_link.releases.licenses import parse_licenses
    from clinpgx_link.releases.manifest import DataReleaseManifest
    from clinpgx_link.releases.materialize import ReleaseInput
    from clinpgx_link.releases.schema import RecordCounts, build_database_schema, canonical_bytes

    release_dir = _private(tmp_path / f"release-{marker}")
    source_dir = _private(release_dir / "source")
    rights = parse_licenses(_canonical(licenses_value()))
    rights_raw = rights_bytes(rights)
    archive = f"archive-{marker}".encode()
    member = f"member-{marker}".encode()
    source = manifest_value()
    item = source["artifacts"][0]  # type: ignore[index]
    item["sha256"] = hashlib.sha256(archive).hexdigest()  # type: ignore[index]
    item["byte_count"] = len(archive) + byte_count_delta  # type: ignore[index]
    item["registry_size"] = len(archive) + 999  # type: ignore[index]
    item["imported_counts"] = {"rows": 1, "documents": 0}  # type: ignore[index]
    item["members"][0].update(  # type: ignore[index]
        sha256=hashlib.sha256(member).hexdigest(),
        uncompressed_size=len(member),
        compressed_size=7,
        consumed=consumed,
        indexed=indexed,
    )
    identity = release_identity(
        profile="core",
        sources=[("data/genes.zip", item["sha256"])],  # type: ignore[index]
        transformation_sha256="d" * 64,
        schema_version="1.0.0",
        licenses_sha256=hashlib.sha256(rights_raw).hexdigest(),
    )
    source["source_set_identity"] = identity.source_set_identity
    source_raw = _canonical(source)
    snapshot = "sha256:" + hashlib.sha256((marker + "-snapshot").encode()).hexdigest()
    _database(
        source_dir / "clinpgx.sqlite",
        tag=identity.tag,
        snapshot=snapshot,
        archive=archive,
        member=member,
        parser_status=parser_status,
    )
    counts = RecordCounts(dataset=1, record=1, source_archive=1, source_member=1, membership=1)
    (source_dir / "schema.json").write_bytes(
        canonical_bytes(build_database_schema("1.0.0", counts))
    )
    (source_dir / "source-manifest.json").write_bytes(source_raw)
    (source_dir / "licenses.json").write_bytes(rights_raw)
    for path in source_dir.iterdir():
        path.chmod(0o444)
    artifact = release_dir / "clinpgx-core.tar.zst"
    receipt = pack_bundle(source_dir, artifact, source_date_epoch=1, limits=BundleLimits())
    manifest = DataReleaseManifest.model_validate(
        {
            "schema_version": 1,
            "dataset": {
                "name": "clinpgx-core",
                "release": identity.tag,
                "source": {
                    "identifier": "clinpgx-core-source-set-v1",
                    "url": source["registry"]["url"],  # type: ignore[index]
                    "retrieved_at": "2026-09-05T10:00:00Z",
                    "sha256": hashlib.sha256(source_raw).hexdigest(),
                    "etag": None,
                    "last_modified": None,
                },
            },
            "transformation": {"repository": "owner/repo", "revision": "e" * 40},
            "schema": {"minimum": "1.0.0", "maximum": "1.0.0", "actual": "1.0.0"},
            "record_counts": counts.model_dump(),
            "artifact": {
                "filename": artifact.name,
                "sha256": receipt.sha256,
                "compressed_size": receipt.compressed_size,
                "max_compressed_size": receipt.compressed_size,
                "expanded_tree_sha256": receipt.expanded_tree_sha256,
                "expanded_size": receipt.expanded_size,
                "max_expanded_size": receipt.expanded_size,
                "member_count": 4,
                "max_members": 4,
            },
            "license": {
                "name": "operator evidence",
                "url": "https://example.test/terms",
                "redistribution_allowed": False,
                "reviewed_at": "2026-09-05T10:00:00Z",
                "reviewer": "Fixture",
            },
            "previous_known_good_digest": previous or f"sha256:{receipt.sha256}",
            "application_compatibility": {"minimum": "1.0.0", "maximum": "2.0.0"},
            "disclaimer": "Synthetic test-only release; never production evidence.",
        }
    )
    manifest_raw = json.dumps(manifest.model_dump(mode="json", by_alias=True), indent=1).encode()
    return FixtureRelease(
        ReleaseInput(manifest_raw, hashlib.sha256(manifest_raw).hexdigest(), artifact),
        receipt.sha256,
        identity.tag,
    )


def _kwargs(data_root: Path) -> dict[str, object]:
    from clinpgx_link.releases.bundle import BundleLimits
    from clinpgx_link.releases.manifest import CompatibilityRange

    return {
        "data_root": data_root,
        "application_version": "1.0.0",
        "supported_schema": CompatibilityRange(minimum="1.0.0", maximum="1.0.0"),
        "limits": BundleLimits(
            max_compressed_bytes=1024 * 1024,
            max_expanded_bytes=1024 * 1024,
            max_member_bytes=1024 * 1024,
        ),
        "lock_timeout_seconds": 1.0,
    }


def test_pack_install_a_b_and_offline_rollback_restores_exact_identities(tmp_path: Path) -> None:
    from clinpgx_link.releases.materialize import ReleaseInput, install_release, rollback_release

    data_root = _private(tmp_path / "data")
    first = _release(tmp_path, "a")
    installed_a = install_release(first.release_input, **_kwargs(data_root))  # type: ignore[arg-type]
    second = _release(tmp_path, "b", f"sha256:{first.artifact_digest}")
    installed_b = install_release(
        second.release_input,
        previous=ReleaseInput(
            first.release_input.manifest_bytes, first.release_input.expected_manifest_sha256, None
        ),
        **_kwargs(data_root),
    )  # type: ignore[union-attr,arg-type]
    assert os.readlink(data_root / "current") == f"versions/{second.artifact_digest}"
    rolled = rollback_release(
        ReleaseInput(
            first.release_input.manifest_bytes, first.release_input.expected_manifest_sha256, None
        ),
        **_kwargs(data_root),
    )  # type: ignore[union-attr,arg-type]
    assert os.readlink(data_root / "current") == f"versions/{first.artifact_digest}"
    assert rolled.runtime_identity_sha256 == installed_a.runtime_identity_sha256
    assert rolled.expanded_tree_sha256 == installed_a.expanded_tree_sha256
    assert installed_b.runtime_identity_sha256 != installed_a.runtime_identity_sha256


def test_bootstrap_refuses_nonempty_root_and_manifest_digest_is_independent(tmp_path: Path) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.materialize import install_release

    data_root = _private(tmp_path / "data")
    (data_root / "caller-file").write_text("keep")
    release = _release(tmp_path, "a")
    with pytest.raises(DataValidationError):
        install_release(release.release_input, **_kwargs(data_root))  # type: ignore[arg-type]
    assert (data_root / "caller-file").read_text() == "keep"

    bad = release.release_input.__class__(
        release.release_input.manifest_bytes, "0" * 64, release.release_input.artifact_path
    )  # type: ignore[union-attr]
    with pytest.raises(DataValidationError):
        install_release(bad, **_kwargs(_private(tmp_path / "other")))


def test_clean_install_b_stages_direct_a_and_skipped_upgrade_needs_only_b(tmp_path: Path) -> None:
    from clinpgx_link.releases.materialize import ReleaseInput, install_release

    first = _release(tmp_path, "a")
    second = _release(tmp_path, "b", f"sha256:{first.artifact_digest}")
    retained_a = ReleaseInput(
        first.release_input.manifest_bytes,  # type: ignore[union-attr]
        first.release_input.expected_manifest_sha256,  # type: ignore[union-attr]
        first.release_input.artifact_path,  # type: ignore[union-attr]
    )
    clean = _private(tmp_path / "clean")
    install_release(second.release_input, previous=retained_a, **_kwargs(clean))  # type: ignore[arg-type]
    assert {path.name for path in (clean / "versions").iterdir()} == {
        first.artifact_digest,
        second.artifact_digest,
    }

    third = _release(tmp_path, "c", f"sha256:{second.artifact_digest}")
    skipped = _private(tmp_path / "skipped")
    install_release(first.release_input, **_kwargs(skipped))  # type: ignore[arg-type]
    install_release(third.release_input, previous=second.release_input, **_kwargs(skipped))  # type: ignore[arg-type]
    assert os.readlink(skipped / "current") == f"versions/{third.artifact_digest}"


@pytest.mark.parametrize(
    "release_kwargs",
    [
        {"byte_count_delta": 1},
        {"consumed": False},
        {"indexed": False},
        {"parser_status": "preserved"},
    ],
)
def test_semantic_provenance_mismatch_fails_without_selection(
    tmp_path: Path, release_kwargs: dict[str, object]
) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.materialize import install_release

    release = _release(tmp_path, "bad", **release_kwargs)  # type: ignore[arg-type]
    data_root = _private(tmp_path / "data")
    with pytest.raises(DataValidationError):
        install_release(release.release_input, **_kwargs(data_root))  # type: ignore[arg-type]
    assert not (data_root / "current").exists()


def test_existing_generation_is_reverified_and_identical_reinstall_converges(
    tmp_path: Path,
) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.materialize import install_release

    release = _release(tmp_path, "a")
    data_root = _private(tmp_path / "data")
    first = install_release(release.release_input, **_kwargs(data_root))  # type: ignore[arg-type]
    assert install_release(release.release_input, **_kwargs(data_root)) == first  # type: ignore[arg-type]
    retained = data_root / "versions" / release.artifact_digest / "source-manifest.json"
    retained.chmod(0o600)
    retained.write_bytes(retained.read_bytes() + b" ")
    retained.chmod(0o444)
    with pytest.raises(DataValidationError):
        install_release(release.release_input, **_kwargs(data_root))  # type: ignore[arg-type]


def _retained(release: FixtureRelease):
    from clinpgx_link.releases.materialize import ReleaseInput

    return ReleaseInput(
        release.release_input.manifest_bytes,  # type: ignore[union-attr]
        release.release_input.expected_manifest_sha256,  # type: ignore[union-attr]
        None,
    )


def test_stage_never_selects_and_predecessor_and_compatibility_fail_closed(
    tmp_path: Path,
) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.manifest import CompatibilityRange
    from clinpgx_link.releases.materialize import install_release, stage_release

    first = _release(tmp_path, "a")
    data_root = _private(tmp_path / "data")
    stage_release(first.release_input, **_kwargs(data_root))  # type: ignore[arg-type]
    assert not (data_root / "current").exists()
    second = _release(tmp_path, "b", f"sha256:{first.artifact_digest}")
    wrong = _release(tmp_path, "wrong")
    with pytest.raises(DataValidationError):
        install_release(second.release_input, previous=wrong.release_input, **_kwargs(data_root))  # type: ignore[arg-type]
    incompatible = _kwargs(data_root)
    incompatible["supported_schema"] = CompatibilityRange(minimum="2.0.0", maximum="2.0.0")
    with pytest.raises(DataValidationError):
        install_release(first.release_input, **incompatible)  # type: ignore[arg-type]


@pytest.mark.parametrize("mutation", ["extra", "sidecar", "sqlite", "source", "rights", "link"])
def test_tampered_retained_generation_cannot_rollback(tmp_path: Path, mutation: str) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.materialize import install_release, rollback_release

    first = _release(tmp_path, "a")
    second = _release(tmp_path, "b", f"sha256:{first.artifact_digest}")
    data_root = _private(tmp_path / "data")
    install_release(first.release_input, **_kwargs(data_root))  # type: ignore[arg-type]
    install_release(second.release_input, previous=_retained(first), **_kwargs(data_root))  # type: ignore[arg-type]
    generation = data_root / "versions" / first.artifact_digest
    if mutation == "extra":
        target = generation / "extra"
        target.write_bytes(b"x")
        target.chmod(0o444)
    elif mutation == "sqlite":
        target = generation / "clinpgx.sqlite"
        target.chmod(0o600)
        with sqlite3.connect(target) as connection:
            connection.execute("UPDATE metadata SET value='tampered' WHERE key='snapshot_id'")
        target.chmod(0o444)
    elif mutation == "link":
        target = generation / "licenses.json"
        target.unlink()
        target.symlink_to("source-manifest.json")
    else:
        names = {
            "sidecar": "materialization.json",
            "source": "source-manifest.json",
            "rights": "licenses.json",
        }
        target = generation / names[mutation]
        target.chmod(0o600)
        target.write_bytes(target.read_bytes() + b" ")
        target.chmod(0o444)
    with pytest.raises(DataValidationError):
        rollback_release(_retained(first), **_kwargs(data_root))
    assert os.readlink(data_root / "current") == f"versions/{second.artifact_digest}"


def test_tampered_compressed_artifact_is_never_deleted(tmp_path: Path) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.materialize import install_release

    release = _release(tmp_path, "a")
    artifact = release.release_input.artifact_path  # type: ignore[union-attr]
    before = artifact.read_bytes()
    artifact.write_bytes(before + b"tampered")
    with pytest.raises(DataValidationError):
        install_release(release.release_input, **_kwargs(_private(tmp_path / "data")))  # type: ignore[arg-type]
    assert artifact.read_bytes() == before + b"tampered"


def test_same_tag_with_different_artifact_is_an_explicit_collision(tmp_path: Path) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases.materialize import ReleaseInput, install_release, stage_release

    first = _release(tmp_path, "a")
    second = _release(tmp_path, "b", f"sha256:{first.artifact_digest}")
    data_root = _private(tmp_path / "data")
    install_release(first.release_input, **_kwargs(data_root))  # type: ignore[arg-type]
    value = json.loads(second.release_input.manifest_bytes)  # type: ignore[union-attr]
    value["dataset"]["release"] = first.tag
    raw = json.dumps(value, indent=1).encode()
    collision = ReleaseInput(
        raw,
        hashlib.sha256(raw).hexdigest(),
        second.release_input.artifact_path,  # type: ignore[union-attr]
    )
    with pytest.raises(DataValidationError) as caught:
        stage_release(collision, **_kwargs(data_root))
    assert caught.value.subtype == "release_collision"


def test_interrupted_candidate_stage_preserves_current_and_removes_only_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases import materialize

    first = _release(tmp_path, "a")
    second = _release(tmp_path, "b", f"sha256:{first.artifact_digest}")
    data_root = _private(tmp_path / "data")
    materialize.install_release(first.release_input, **_kwargs(data_root))  # type: ignore[arg-type]
    real_validate = materialize.validate_semantics

    def interrupt(path: Path, manifest):  # type: ignore[no-untyped-def]
        if path.name.endswith(".partial"):
            raise DataValidationError("synthetic interrupted validation")
        return real_validate(path, manifest)

    monkeypatch.setattr(materialize, "validate_semantics", interrupt)
    with pytest.raises(DataValidationError, match="interrupted"):
        materialize.install_release(
            second.release_input,
            previous=_retained(first),
            **_kwargs(data_root),  # type: ignore[arg-type]
        )
    assert os.readlink(data_root / "current") == f"versions/{first.artifact_digest}"
    assert not any(path.name.endswith(".partial") for path in (data_root / "versions").iterdir())


def test_concurrent_identical_bootstrap_installs_converge(tmp_path: Path) -> None:
    from clinpgx_link.releases.materialize import install_release

    release = _release(tmp_path, "a")
    data_root = _private(tmp_path / "data")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(install_release, release.release_input, **_kwargs(data_root))  # type: ignore[arg-type]
            for _ in range(2)
        ]
    receipts = [future.result() for future in futures]
    assert receipts[0] == receipts[1]
    assert [path.name for path in (data_root / "versions").iterdir()] == [release.artifact_digest]


def test_failed_selection_commit_restores_old_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from clinpgx_link.exceptions import DataValidationError
    from clinpgx_link.releases import materialize

    first = _release(tmp_path, "a")
    second = _release(tmp_path, "b", f"sha256:{first.artifact_digest}")
    data_root = _private(tmp_path / "data")
    materialize.install_release(first.release_input, **_kwargs(data_root))  # type: ignore[arg-type]
    real_fsync = materialize.os.fsync
    failed = False

    def fail_root_once(descriptor: int) -> None:
        nonlocal failed
        if not failed and os.readlink(f"/proc/self/fd/{descriptor}") == str(data_root):
            failed = True
            raise OSError("synthetic commit failure")
        real_fsync(descriptor)

    monkeypatch.setattr(materialize.os, "fsync", fail_root_once)
    with pytest.raises(DataValidationError, match="prior selection restored"):
        materialize.install_release(
            second.release_input,
            previous=_retained(first),
            **_kwargs(data_root),  # type: ignore[arg-type]
        )
    assert os.readlink(data_root / "current") == f"versions/{first.artifact_digest}"
