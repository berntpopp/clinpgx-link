"""Deterministic USTAR/zstd release bundles and bounded verified staging.

Expanded-tree identity and bounded admission are adapted from GeneFoundry Router
``release/data_materialization.py`` at 6568a0ad7d68925440aba550a2a678282ea5bb6b,
MIT, Copyright 2026 Bernt Popp. No runtime router dependency is introduced.
"""

from __future__ import annotations

import hashlib
import os
import tarfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, cast

import zstandard

from clinpgx_link.exceptions import DataValidationError
from clinpgx_link.releases import bundle_io
from clinpgx_link.releases.manifest import ArtifactIdentity

_MIB = 1024 * 1024
_GIB = 1024 * _MIB
_CHUNK = 64 * 1024
_BLOCK = 512
_USTAR_MAX = 0o77777777777
_INVENTORY = (
    "clinpgx.sqlite",
    "licenses.json",
    "schema.json",
    "source-manifest.json",
)


@dataclass(frozen=True)
class BundleLimits:
    max_compressed_bytes: int = 256 * _MIB
    max_expanded_bytes: int = 2 * _GIB
    max_member_bytes: int = 2 * _GIB
    max_members: int = field(default=4, init=False)

    def __post_init__(self) -> None:
        for value in (
            self.max_compressed_bytes,
            self.max_expanded_bytes,
            self.max_member_bytes,
        ):
            if type(value) is not int or value <= 0:
                raise DataValidationError("Bundle limits must be positive integers")


@dataclass(frozen=True)
class BundleFile:
    path: str
    size_bytes: int
    sha256: str
    mode: int


@dataclass(frozen=True)
class ExpandedTreeIdentity:
    expanded_tree_sha256: str
    expanded_size: int
    member_count: int
    files: tuple[BundleFile, ...]


@dataclass(frozen=True)
class BundleReceipt:
    sha256: str
    compressed_size: int
    expanded_tree_sha256: str
    expanded_size: int
    member_count: int
    files: tuple[BundleFile, ...]


class _HashingReader:
    def __init__(self, stream: BinaryIO, maximum: int) -> None:
        self.stream = stream
        self.maximum = maximum
        self.digest = hashlib.sha256()
        self.total = 0

    def read(self, size: int = -1) -> bytes:
        requested = _CHUNK if size < 0 else min(size, _CHUNK)
        raw = self.stream.read(requested)
        self.total += len(raw)
        if self.total > self.maximum:
            raise DataValidationError("Bundle member exceeds its byte limit")
        self.digest.update(raw)
        return raw


class _BoundedWriter:
    def __init__(self, stream: BinaryIO, maximum: int) -> None:
        self.stream = stream
        self.maximum = maximum
        self.digest = hashlib.sha256()
        self.total = 0

    def write(self, raw: bytes) -> int:
        self.total += len(raw)
        if self.total > self.maximum:
            raise DataValidationError("Compressed bundle exceeds its byte limit")
        written = self.stream.write(raw)
        if written != len(raw):
            raise OSError("short bundle write")
        self.digest.update(raw)
        return written

    def flush(self) -> None:
        self.stream.flush()

    def writable(self) -> bool:
        return True


class _DecodedReader:
    def __init__(self, stream: BinaryIO, maximum: int) -> None:
        self.stream = stream
        self.maximum = maximum
        self.total = 0

    def read(self, size: int) -> bytes:
        raw = self.stream.read(min(size, _CHUNK))
        self.total += len(raw)
        if self.total > self.maximum:
            raise DataValidationError("Decoded bundle exceeds its byte limit")
        return raw

    def exact(self, size: int) -> bytes:
        output = bytearray()
        while len(output) < size:
            chunk = self.read(size - len(output))
            if not chunk:
                raise DataValidationError("Bundle tar stream is truncated")
            output.extend(chunk)
        return bytes(output)


def _tree(files: list[BundleFile]) -> ExpandedTreeIdentity:
    ordered = tuple(sorted(files, key=lambda item: item.path))
    digest = hashlib.sha256()
    for item in ordered:
        digest.update(f"{item.path}\0{item.mode:04o}\0{item.size_bytes}\0{item.sha256}\n".encode())
    return ExpandedTreeIdentity(
        expanded_tree_sha256=digest.hexdigest(),
        expanded_size=sum(item.size_bytes for item in ordered),
        member_count=len(ordered),
        files=ordered,
    )


def _validate_root_inventory(root_fd: int) -> None:
    try:
        names = tuple(sorted(os.listdir(root_fd)))
    except OSError as exc:
        raise DataValidationError("Bundle source inventory cannot be read") from exc
    if names != _INVENTORY:
        raise DataValidationError("Bundle source inventory is not exact")


def _hash_source_tree(root_fd: int, limits: BundleLimits) -> ExpandedTreeIdentity:
    _validate_root_inventory(root_fd)
    files: list[BundleFile] = []
    expanded = 0
    for name in _INVENTORY:
        descriptor, before = bundle_io.open_regular(
            root_fd, name, required_mode=0o444, maximum_size=limits.max_member_bytes
        )
        try:
            digest = hashlib.sha256()
            total = 0
            while raw := os.read(descriptor, _CHUNK):
                total += len(raw)
                expanded += len(raw)
                if total > limits.max_member_bytes or expanded > limits.max_expanded_bytes:
                    raise DataValidationError("Expanded bundle exceeds its byte limit")
                digest.update(raw)
            bundle_io.revalidate_regular(descriptor, root_fd, name, before)
            files.append(BundleFile(name, total, digest.hexdigest(), 0o444))
        finally:
            os.close(descriptor)
    return _tree(files)


def expanded_tree_identity(root: Path, *, limits: BundleLimits) -> ExpandedTreeIdentity:
    """Return the fleet-compatible identity of one exact admitted source tree."""
    root_fd = bundle_io.open_private_directory(root)
    try:
        identity = _hash_source_tree(root_fd, limits)
        bundle_io.revalidate_directory(root_fd, root)
        return identity
    finally:
        os.close(root_fd)


def _validate_epoch(value: int) -> None:
    if type(value) is not int or value < 0 or value > _USTAR_MAX:
        raise DataValidationError("SOURCE_DATE_EPOCH is not representable in USTAR")


def _destination_parent(destination: Path) -> tuple[int, str]:
    if not isinstance(destination, Path) or not destination.is_absolute() or not destination.name:
        raise DataValidationError("Bundle destination must be an absolute file path")
    parent_fd = bundle_io.open_private_directory(destination.parent)
    if bundle_io.entry_exists(parent_fd, destination.name):
        os.close(parent_fd)
        raise DataValidationError("Bundle destination already exists")
    return parent_fd, destination.name


def _pack_members(
    root_fd: int,
    output: _BoundedWriter,
    *,
    epoch: int,
    limits: BundleLimits,
) -> ExpandedTreeIdentity:
    _validate_root_inventory(root_fd)
    files: list[BundleFile] = []
    compressor = zstandard.ZstdCompressor(
        level=9,
        threads=0,
        write_checksum=True,
        write_content_size=False,
        write_dict_id=False,
    )
    with compressor.stream_writer(cast(BinaryIO, output), closefd=False) as compressed:
        with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.USTAR_FORMAT) as archive:
            expanded = 0
            for name in _INVENTORY:
                descriptor, before = bundle_io.open_regular(
                    root_fd, name, required_mode=0o444, maximum_size=limits.max_member_bytes
                )
                try:
                    with os.fdopen(os.dup(descriptor), "rb") as source:
                        reader = _HashingReader(source, limits.max_member_bytes)
                        info = tarfile.TarInfo(name)
                        info.size = before.st_size
                        info.mode = 0o444
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        info.mtime = epoch
                        archive.addfile(info, reader)
                    if reader.total != before.st_size:
                        raise DataValidationError("Bundle member size changed during packing")
                    expanded += reader.total
                    if expanded > limits.max_expanded_bytes:
                        raise DataValidationError("Expanded bundle exceeds its byte limit")
                    bundle_io.revalidate_regular(descriptor, root_fd, name, before)
                    files.append(BundleFile(name, reader.total, reader.digest.hexdigest(), 0o444))
                finally:
                    os.close(descriptor)
    _validate_root_inventory(root_fd)
    return _tree(files)


def _publish_file(parent_fd: int, temporary: str, destination: str) -> None:
    linked = False
    try:
        os.link(
            temporary,
            destination,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        linked = True
        os.fsync(parent_fd)
    except FileExistsError as exc:
        raise DataValidationError("Bundle destination already exists") from exc
    except OSError as exc:
        if linked:
            with suppress(OSError):
                os.unlink(destination, dir_fd=parent_fd)
            with suppress(OSError):
                os.fsync(parent_fd)
        raise DataValidationError("Bundle output cannot be published") from exc


def pack_bundle(
    source_root: Path,
    destination: Path,
    *,
    source_date_epoch: int,
    limits: BundleLimits,
) -> BundleReceipt:
    """Stream an exact source tree into deterministic USTAR and zstd bytes."""
    _validate_epoch(source_date_epoch)
    root_fd = bundle_io.open_private_directory(source_root)
    parent_fd = -1
    output_fd = -1
    temporary: str | None = None
    try:
        parent_fd, destination_name = _destination_parent(destination)
        output_fd, temporary = bundle_io.create_private_file(parent_fd, destination_name)
        with os.fdopen(os.dup(output_fd), "wb") as raw_output:
            writer = _BoundedWriter(raw_output, limits.max_compressed_bytes)
            tree = _pack_members(root_fd, writer, epoch=source_date_epoch, limits=limits)
            writer.flush()
        bundle_io.revalidate_directory(root_fd, source_root)
        os.fsync(output_fd)
        size = os.fstat(output_fd).st_size
        if size != writer.total:
            raise DataValidationError("Compressed bundle size changed during packing")
        _publish_file(parent_fd, temporary, destination_name)
        with suppress(OSError):
            os.unlink(temporary, dir_fd=parent_fd)
        temporary = None
        return BundleReceipt(
            sha256=writer.digest.hexdigest(),
            compressed_size=writer.total,
            expanded_tree_sha256=tree.expanded_tree_sha256,
            expanded_size=tree.expanded_size,
            member_count=tree.member_count,
            files=tree.files,
        )
    except DataValidationError:
        raise
    except (OSError, zstandard.ZstdError, tarfile.TarError) as exc:
        raise DataValidationError("Bundle could not be packed") from exc
    finally:
        if output_fd >= 0:
            with suppress(OSError):
                os.close(output_fd)
        if temporary is not None and parent_fd >= 0:
            with suppress(OSError):
                os.unlink(temporary, dir_fd=parent_fd)
        if parent_fd >= 0:
            with suppress(OSError):
                os.close(parent_fd)
        os.close(root_fd)


def _parse_octal(raw: bytes) -> int:
    value = raw.rstrip(b"\0 ").lstrip(b" ")
    if not value or any(character not in b"01234567" for character in value):
        raise DataValidationError("Bundle tar header is invalid")
    return int(value, 8)


def _header(block: bytes) -> tuple[str, int]:
    stored = _parse_octal(block[148:156])
    checksum = sum(block[:148]) + 8 * ord(" ") + sum(block[156:])
    if stored != checksum or block[257:263] != b"ustar\0" or block[263:265] != b"00":
        raise DataValidationError("Bundle tar header is invalid")
    if block[345:500].strip(b"\0") or block[157:257].strip(b"\0"):
        raise DataValidationError("Bundle tar extensions and links are forbidden")
    if block[156:157] not in {b"\0", b"0"}:
        raise DataValidationError("Bundle tar member is not a regular file")
    try:
        name = block[:100].split(b"\0", 1)[0].decode("utf-8")
    except UnicodeError as exc:
        raise DataValidationError("Bundle tar member name is invalid") from exc
    if name not in _INVENTORY:
        raise DataValidationError("Bundle tar inventory is not exact")
    if _parse_octal(block[100:108]) != 0o444:
        raise DataValidationError("Bundle tar member mode is invalid")
    if _parse_octal(block[108:116]) != 0 or _parse_octal(block[116:124]) != 0:
        raise DataValidationError("Bundle tar ownership is invalid")
    _parse_octal(block[136:148])
    return name, _parse_octal(block[124:136])


def _extract_tar(
    reader: _DecodedReader, staging_fd: int, limits: BundleLimits
) -> ExpandedTreeIdentity:
    files: list[BundleFile] = []
    seen: set[str] = set()
    expanded = 0
    zero_blocks = 0
    while zero_blocks < 2:
        block = reader.exact(_BLOCK)
        if block == b"\0" * _BLOCK:
            zero_blocks += 1
            continue
        if zero_blocks:
            raise DataValidationError("Bundle tar terminator is invalid")
        name, size = _header(block)
        if name in seen or len(seen) >= limits.max_members:
            raise DataValidationError("Bundle tar contains duplicate or excess members")
        if size > limits.max_member_bytes:
            raise DataValidationError("Bundle member exceeds its byte limit")
        seen.add(name)
        expanded += size
        if expanded > limits.max_expanded_bytes:
            raise DataValidationError("Expanded bundle exceeds its byte limit")
        descriptor = -1
        try:
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                0o600,
                dir_fd=staging_fd,
            )
            digest = hashlib.sha256()
            remaining = size
            while remaining:
                raw = reader.exact(min(remaining, _CHUNK))
                written = os.write(descriptor, raw)
                if written != len(raw):
                    raise OSError("short extracted write")
                digest.update(raw)
                remaining -= len(raw)
            padding = (-size) % _BLOCK
            if padding and reader.exact(padding) != b"\0" * padding:
                raise DataValidationError("Bundle tar member padding is invalid")
            os.fchmod(descriptor, 0o444)
            os.fsync(descriptor)
            files.append(BundleFile(name, size, digest.hexdigest(), 0o444))
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    while raw := reader.read(_CHUNK):
        if raw.strip(b"\0"):
            raise DataValidationError("Bundle tar has nonzero trailing content")
    if tuple(sorted(seen)) != _INVENTORY:
        raise DataValidationError("Bundle tar inventory is not exact")
    return _tree(files)


def _decoded_limit(limits: BundleLimits) -> int:
    return limits.max_expanded_bytes + limits.max_members * (2 * _BLOCK) + 10 * 1024


def _verify_artifact(
    artifact_path: Path, expected: ArtifactIdentity, limits: BundleLimits
) -> tuple[int, int, os.stat_result]:
    if artifact_path.name != expected.filename:
        raise DataValidationError("Bundle filename does not match reviewed identity")
    parent_fd = bundle_io.open_private_directory(artifact_path.parent)
    descriptor = -1
    try:
        descriptor, before = bundle_io.open_regular(
            parent_fd,
            artifact_path.name,
            maximum_size=min(limits.max_compressed_bytes, expected.max_compressed_size),
        )
        digest = hashlib.sha256()
        total = 0
        while raw := os.read(descriptor, _CHUNK):
            total += len(raw)
            if total > limits.max_compressed_bytes:
                raise DataValidationError("Compressed bundle exceeds its byte limit")
            digest.update(raw)
        if total != expected.compressed_size or digest.hexdigest() != expected.sha256:
            raise DataValidationError("Compressed bundle identity does not match reviewed identity")
        bundle_io.revalidate_regular(descriptor, parent_fd, artifact_path.name, before)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor, parent_fd, before
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)
        raise


def _match_expected(
    tree: ExpandedTreeIdentity, expected: ArtifactIdentity, limits: BundleLimits
) -> None:
    if (
        expected.member_count != limits.max_members
        or tree.member_count != expected.member_count
        or tree.expanded_size != expected.expanded_size
        or tree.expanded_size > expected.max_expanded_size
        or tree.expanded_tree_sha256 != expected.expanded_tree_sha256
    ):
        raise DataValidationError("Expanded bundle identity does not match reviewed identity")


def _validate_zstd_frame(descriptor: int, decoded_maximum: int) -> None:
    """Require one complete checksummed frame without retaining decoded content."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    header = os.read(descriptor, 18)
    try:
        parameters = zstandard.get_frame_parameters(header)
    except zstandard.ZstdError as exc:
        raise DataValidationError("Bundle is not a valid zstd frame") from exc
    if (
        not parameters.has_checksum
        or parameters.content_size != zstandard.CONTENTSIZE_UNKNOWN
        or parameters.dict_id != 0
        or parameters.window_size > decoded_maximum
    ):
        raise DataValidationError("Bundle zstd frame parameters are invalid")
    os.lseek(descriptor, 0, os.SEEK_SET)
    decoder = zstandard.ZstdDecompressor(
        max_window_size=min(decoded_maximum, (1 << 31) - 1)
    ).decompressobj(write_size=_CHUNK, read_across_frames=False)
    decoded = 0
    while raw := os.read(descriptor, 1):
        output = decoder.decompress(raw)
        decoded += len(output)
        if decoded > decoded_maximum or decoder.unused_data:
            raise DataValidationError("Bundle zstd frame exceeds its bounds")
    if not decoder.eof:
        raise DataValidationError("Bundle zstd frame is truncated")
    os.lseek(descriptor, 0, os.SEEK_SET)


def verify_and_extract_bundle(
    artifact_path: Path,
    destination: Path,
    expected: ArtifactIdentity,
    *,
    limits: BundleLimits,
) -> BundleReceipt:
    """Authenticate compressed bytes, strictly decode USTAR, then publish staging."""
    artifact_fd, artifact_parent_fd, artifact_before = _verify_artifact(
        artifact_path, expected, limits
    )
    destination_parent_fd = -1
    staging_fd = -1
    staging_name: str | None = None
    published = False
    try:
        destination_parent_fd, destination_name = _destination_parent(destination)
        staging_fd, staging_name = bundle_io.create_private_directory(
            destination_parent_fd, destination_name
        )
        decoded_maximum = _decoded_limit(limits)
        _validate_zstd_frame(artifact_fd, decoded_maximum)
        with os.fdopen(os.dup(artifact_fd), "rb") as compressed:
            decompressor = zstandard.ZstdDecompressor(
                max_window_size=min(decoded_maximum, (1 << 31) - 1)
            )
            with decompressor.stream_reader(
                compressed, read_across_frames=False, closefd=False
            ) as decoded:
                tree = _extract_tar(_DecodedReader(decoded, decoded_maximum), staging_fd, limits)
        bundle_io.revalidate_regular(
            artifact_fd, artifact_parent_fd, artifact_path.name, artifact_before
        )
        bundle_io.revalidate_directory(artifact_parent_fd, artifact_path.parent)
        _match_expected(tree, expected, limits)
        os.fsync(staging_fd)
        os.close(staging_fd)
        staging_fd = -1
        bundle_io.revalidate_directory(destination_parent_fd, destination.parent)
        bundle_io.rename_noreplace(destination_parent_fd, staging_name, destination_name)
        published = True
        os.fsync(destination_parent_fd)
        staging_name = None
        return BundleReceipt(
            sha256=expected.sha256,
            compressed_size=expected.compressed_size,
            expanded_tree_sha256=tree.expanded_tree_sha256,
            expanded_size=tree.expanded_size,
            member_count=tree.member_count,
            files=tree.files,
        )
    except DataValidationError:
        raise
    except (OSError, zstandard.ZstdError) as exc:
        raise DataValidationError("Bundle is not a valid bounded zstd/USTAR artifact") from exc
    finally:
        if staging_fd >= 0:
            with suppress(OSError):
                os.close(staging_fd)
        if staging_name is not None and destination_parent_fd >= 0:
            with suppress(DataValidationError):
                bundle_io.remove_directory(destination_parent_fd, staging_name)
        if published and staging_name is not None and destination_parent_fd >= 0:
            with suppress(DataValidationError):
                bundle_io.remove_directory(destination_parent_fd, destination.name)
        if destination_parent_fd >= 0:
            with suppress(OSError):
                os.close(destination_parent_fd)
        os.close(artifact_fd)
        os.close(artifact_parent_fd)


__all__ = [
    "BundleFile",
    "BundleLimits",
    "BundleReceipt",
    "ExpandedTreeIdentity",
    "expanded_tree_identity",
    "pack_bundle",
    "verify_and_extract_bundle",
]
