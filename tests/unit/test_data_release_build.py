"""The reproducible public data-release builder, end to end on a synthetic core set."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.releases.acquisition import CORE_ARCHIVES
from clinpgx_link.releases.build_release import RELEASE_ASSETS, build_release
from clinpgx_link.releases.bundle import BundleLimits
from clinpgx_link.releases.licenses import parse_licenses
from clinpgx_link.releases.manifest import validate_manifest

FIXTURES = Path(__file__).parents[1] / "fixtures" / "exports" / "sourced"
NOTICE = "This work is licensed under CC BY-SA 4.0. Synthetic test notice.\n"
REVISION = "a" * 40
_PROFILED = {
    "genes.zip": {"genes.tsv": "genes.tsv"},
    "summaryAnnotations.zip": {
        name: name
        for name in (
            "summary_annotations.tsv",
            "summary_ann_alleles.tsv",
            "summary_ann_evidence.tsv",
        )
    },
    "guidelineAnnotations.json.zip": {"PA166363221.json": "PA166363221.json"},
    "pathways.json.zip": {"pathways.json": "pathways.json"},
    "relationships.zip": {"relationships.tsv": "relationships.tsv"},
}


def _zip(members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 5, 8, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, members[name])
    return output.getvalue()


def _members(file_name: str, *, notice: str) -> dict[str, bytes]:
    members = {
        "LICENSE.txt": notice.encode(),
        "CREATED_2026-11-05.txt": b"Created on 11/05/2026 at 00:26:48 PST.\n",
    }
    if file_name in _PROFILED:
        for member, fixture in _PROFILED[file_name].items():
            members[member] = (FIXTURES / fixture).read_bytes()
    elif file_name == "pathways-biopax.zip":
        members["pathway.owl"] = b"<rdf:RDF/>\n"
    else:
        stem = file_name.removesuffix(".zip")
        members[f"{stem}.tsv"] = f"Id\tName\nPA1\t{stem}\n".encode()
    return members


def _acquisition(root: Path, *, notice: str = NOTICE, overrides: dict | None = None) -> Path:
    sources = root / "sources"
    sources.mkdir(parents=True, mode=0o700)
    receipts = []
    for name in CORE_ARCHIVES:
        members = _members(name, notice=notice)
        members.update((overrides or {}).get(name, {}))
        raw = _zip(members)
        (sources / name).write_bytes(raw)
        receipts.append(
            {
                "file_name": name,
                "dataset_id": f"data/{name}",
                "registry_last_modified": "2026-11-05T00:31:19-08:00",
                "registry_size": len(raw),
                "acquisition_url": f"https://api.clinpgx.org/v1/download/file/data/{name}",
                "final_url": f"https://s3.pgkb.org/data/{name}",
                "retrieved_at": "2026-11-12T10:00:00Z",
                "http_etag": '"etag"',
                "http_last_modified": "Thu, 05 Nov 2026 08:26:27 GMT",
                "http_version_id": None,
                "http_content_length": len(raw),
                "byte_count": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "media_type": "application/zip",
            }
        )
    receipt = {
        "schema_version": 1,
        "registry": {
            "url": "https://api.clinpgx.org/v1/data/file/data/?view=min",
            "retrieved_at": "2026-11-12T10:00:00Z",
            "sha256": "b" * 64,
            "etag": None,
            "last_modified": "Thu, 12 Nov 2026 10:00:00 GMT",
        },
        "sources": receipts,
    }
    (root / "acquisition.json").write_text(json.dumps(receipt))
    return root


def _authorization(path: Path, **updates: str) -> Path:
    value = {
        "schema_version": 1,
        "reviewer": "Repository owner (test)",
        "reviewed_at": "2026-11-12T00:00:00Z",
        "quote": "approved",
        "reference": "synthetic test",
        "notice_sha256": hashlib.sha256(NOTICE.encode()).hexdigest(),
        "interpretation": "Public-mode authorization only.",
        "public_rationale": "Owner authorized public distribution of this bundle.",
        "operator_local_rationale": "Operator-local build and validation authorized.",
        "policy_obligation": "Differing notices are retained and remain unresolved.",
        "license_name": "ClinPGx source terms; original archive notice retained",
        "disclaimer": "Research use only; not clinical decision support.",
    }
    value.update(updates)
    path.write_text(json.dumps(value))
    return path


def _build(tmp_path: Path, name: str, **kwargs: object):
    return build_release(
        acquisition_dir=kwargs.pop("acquisition", None) or _acquisition(tmp_path / "acq"),
        authorization_path=kwargs.pop("authorization", None)
        or _authorization(tmp_path / "auth.json"),
        output_dir=tmp_path / name,
        revision=REVISION,
        application_version="0.1.9",
        **kwargs,
    )


def test_build_publishes_four_verified_reproducible_assets(tmp_path: Path) -> None:
    first = _build(tmp_path, "one")
    out = tmp_path / "one"
    assert sorted(p.name for p in out.iterdir() if p.name != ".work") == sorted(RELEASE_ASSETS)
    assert not (out / ".work").exists()
    sums = (out / "SHA256SUMS").read_text().splitlines()
    assert len(sums) == 3
    for line in sums:
        digest, name = line.split("  ")
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest
    manifest_raw = (out / "data-release-manifest.json").read_bytes()
    manifest = validate_manifest(
        manifest_raw, hashlib.sha256(manifest_raw).hexdigest(), public=True
    )
    assert manifest.dataset.release == first.release_tag
    assert manifest.license.reviewer == "Repository owner (test)"
    assert first.record_counts["source_archive"] == 15
    assert first.record_counts["record"] > 0
    report = json.loads((out / "validation-report.json").read_text())
    assert report["approval"]["reviewed_at"] == "2026-11-12T00:00:00Z"
    assert report["validation"]["record_profile_gate"] == "passed"
    for name in ("data-release-manifest.json", "validation-report.json", "SHA256SUMS"):
        assert str(tmp_path).encode() not in (out / name).read_bytes()

    second = build_release(
        acquisition_dir=tmp_path / "acq",
        authorization_path=tmp_path / "auth.json",
        output_dir=tmp_path / "two",
        revision=REVISION,
        application_version="0.1.9",
    )
    assert second.assets == first.assets


def test_licenses_carry_the_authorization_and_archive_notice(tmp_path: Path) -> None:
    import tarfile

    import zstandard

    _build(tmp_path, "out")
    with (tmp_path / "out" / "clinpgx-core.tar.zst").open("rb") as raw:
        reader = zstandard.ZstdDecompressor().stream_reader(raw)
        with tarfile.open(fileobj=reader, mode="r|") as archive:
            for member in archive:
                if member.name == "licenses.json":
                    rights = parse_licenses(archive.extractfile(member).read())
                    break
    record = rights.licenses[0]
    assert record.notice.text == NOTICE
    assert record.distribution.public.allowed
    assert record.distribution.public.reviewed_at == "2026-11-12T00:00:00Z"
    assert not record.needs_confirmation


def test_header_drift_on_a_profiled_member_fails_loudly(tmp_path: Path) -> None:
    drifted = (FIXTURES / "genes.tsv").read_bytes().replace(b"Symbol", b"Gene Symbol", 1)
    acquisition = _acquisition(tmp_path / "acq", overrides={"genes.zip": {"genes.tsv": drifted}})
    with pytest.raises(DataValidationError, match="header drift"):
        _build(tmp_path, "out", acquisition=acquisition)
    assert not any((tmp_path / "out" / name).exists() for name in RELEASE_ASSETS)


def test_size_ceiling_fails_instead_of_truncating(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="byte limit"):
        _build(tmp_path, "out", limits=BundleLimits(max_compressed_bytes=4096))
    assert not any((tmp_path / "out" / name).exists() for name in RELEASE_ASSETS)


def test_changed_upstream_notice_requires_a_new_rights_review(tmp_path: Path) -> None:
    acquisition = _acquisition(tmp_path / "acq", notice="Now licensed CC BY-NC-SA 4.0.\n")
    with pytest.raises(DataValidationError, match="new rights review"):
        _build(tmp_path, "out", acquisition=acquisition)


def test_credential_shaped_values_never_enter_the_release(tmp_path: Path) -> None:
    authorization = _authorization(
        tmp_path / "auth.json", reference="ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    )
    with pytest.raises(DataValidationError, match="credential"):
        _build(tmp_path, "out", authorization=authorization)


def test_tampered_archive_bytes_are_rejected_before_building(tmp_path: Path) -> None:
    acquisition = _acquisition(tmp_path / "acq")
    (acquisition / "sources" / "genes.zip").write_bytes(b"PK tampered")
    with pytest.raises(DataValidationError, match="bytes changed"):
        _build(tmp_path, "out", acquisition=acquisition)


def test_short_revision_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError, match="40-character"):
        build_release(
            acquisition_dir=_acquisition(tmp_path / "acq"),
            authorization_path=_authorization(tmp_path / "auth.json"),
            output_dir=tmp_path / "out",
            revision="abc123",
        )


def test_committed_authorization_is_valid() -> None:
    from clinpgx_link.releases.authorization import parse_authorization

    path = Path(__file__).parents[2] / "release" / "data-authorization.json"
    authorization = parse_authorization(path.read_bytes())
    assert authorization.reviewed_at.startswith("2026-11-12")


class _Stream(httpx.AsyncByteStream):
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._raw


def test_acquire_core_freezes_a_receipt_that_rebuilds_offline(tmp_path: Path) -> None:
    import asyncio

    from clinpgx_link.config import Settings
    from clinpgx_link.releases.acquisition import REGISTRY_URL, acquire_core, load_acquisition

    archives = {name: _zip(_members(name, notice=NOTICE)) for name in CORE_ARCHIVES}
    registry = {
        "data": [
            {
                "fileName": name,
                "path": f"data/{name}",
                "size": len(raw),
                "lastModified": "2026-11-05T00:31:19-08:00",
            }
            for name, raw in archives.items()
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == REGISTRY_URL:
            return httpx.Response(200, json=registry, headers={"last-modified": "x"})
        name = request.url.path.rsplit("/", 1)[1]
        raw = archives[name]
        headers = {"content-type": "application/zip", "content-length": str(len(raw))}
        return httpx.Response(200, stream=_Stream(raw), headers=headers, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    settings = Settings(_env_file=None)
    receipt = asyncio.run(acquire_core(tmp_path / "acq", settings, client=client))
    assert [s.file_name for s in receipt.sources] == list(CORE_ARCHIVES)
    assert load_acquisition(tmp_path / "acq") == receipt
    with pytest.raises(DataValidationError, match="must be empty"):
        asyncio.run(acquire_core(tmp_path / "acq", settings, client=client))
