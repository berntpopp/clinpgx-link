"""Descriptor-relative filesystem primitives for release bundle staging."""

from __future__ import annotations

import ctypes
import errno
import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path
from typing import Any

from clinpgx_link.exceptions import DataValidationError

_RENAME_NOREPLACE = 1
_libc = ctypes.CDLL(None, use_errno=True)
_renameat2: Any | None = getattr(_libc, "renameat2", None)
if _renameat2 is not None:
    _renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    _renameat2.restype = ctypes.c_int


def open_private_directory(path: Path) -> int:
    """Open an existing real, private, same-UID directory."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise DataValidationError("Bundle directory is not an absolute private path")
    try:
        before = path.lstat()
        if path.resolve(strict=True) != path:
            raise DataValidationError("Bundle directory is not a real private directory")
        if (
            not stat.S_ISDIR(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_mode & 0o022
        ):
            raise DataValidationError("Bundle directory is not a real private directory")
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            os.close(descriptor)
            raise DataValidationError("Bundle directory changed during admission")
        return descriptor
    except DataValidationError:
        raise
    except OSError as exc:
        raise DataValidationError("Bundle directory cannot be admitted") from exc


def entry_exists(directory_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DataValidationError("Bundle destination cannot be inspected") from exc
    return True


def revalidate_directory(descriptor: int, path: Path) -> None:
    try:
        opened = os.fstat(descriptor)
        named = path.lstat()
    except OSError as exc:
        raise DataValidationError("Bundle directory changed during streaming") from exc
    if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
        raise DataValidationError("Bundle directory changed during streaming")


def open_regular(
    directory_fd: int,
    name: str,
    *,
    required_mode: int | None = None,
    maximum_size: int | None = None,
) -> tuple[int, os.stat_result]:
    """Open and admit a regular single-link file without following links."""
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (required_mode is not None and stat.S_IMODE(info.st_mode) != required_mode)
            or (maximum_size is not None and info.st_size > maximum_size)
        ):
            raise DataValidationError("Bundle input is not an admitted regular file")
        return descriptor, info
    except DataValidationError:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        raise
    except OSError as exc:
        raise DataValidationError("Bundle input cannot be admitted") from exc


def revalidate_regular(
    descriptor: int, directory_fd: int, name: str, before: os.stat_result
) -> None:
    """Reject descriptor or directory-entry mutation across a streamed read."""
    try:
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise DataValidationError("Bundle input changed during streaming") from exc
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(opened, field) for field in fields) or any(
        getattr(opened, field) != getattr(named, field) for field in fields
    ):
        raise DataValidationError("Bundle input changed during streaming")


def create_private_file(directory_fd: int, stem: str) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    for _attempt in range(16):
        name = f".{stem}.{secrets.token_hex(12)}.partial"
        descriptor = -1
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("private output is not regular")
            os.fchmod(descriptor, 0o600)
            return descriptor, name
        except FileExistsError:
            continue
        except OSError as exc:
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)
            with suppress(OSError):
                os.unlink(name, dir_fd=directory_fd)
            raise DataValidationError("Private bundle output cannot be created") from exc
    raise DataValidationError("Private bundle output cannot be created")


def create_private_directory(directory_fd: int, stem: str) -> tuple[int, str]:
    for _attempt in range(16):
        name = f".{stem}.{secrets.token_hex(12)}.partial"
        descriptor = -1
        try:
            os.mkdir(name, 0o700, dir_fd=directory_fd)
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            return descriptor, name
        except FileExistsError:
            continue
        except OSError as exc:
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)
            with suppress(OSError):
                os.rmdir(name, dir_fd=directory_fd)
            raise DataValidationError("Private bundle staging cannot be created") from exc
    raise DataValidationError("Private bundle staging cannot be created")


def remove_directory(directory_fd: int, name: str) -> None:
    """Remove one owned flat staging directory and its regular entries."""
    try:
        child_fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            for entry in os.listdir(child_fd):
                os.unlink(entry, dir_fd=child_fd)
        finally:
            os.close(child_fd)
        os.rmdir(name, dir_fd=directory_fd)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DataValidationError("Owned bundle staging cannot be removed") from exc


def rename_noreplace(directory_fd: int, source: str, destination: str) -> None:
    """Atomically rename a directory without clobbering via Linux renameat2."""
    if _renameat2 is None:
        raise DataValidationError("Atomic no-clobber publication is unavailable")
    result = _renameat2(
        directory_fd,
        os.fsencode(source),
        directory_fd,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise DataValidationError("Bundle destination already exists")
    if error in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
        raise DataValidationError("Atomic no-clobber publication is unavailable")
    raise DataValidationError("Bundle staging cannot be published")


__all__ = [
    "create_private_directory",
    "create_private_file",
    "entry_exists",
    "open_private_directory",
    "open_regular",
    "remove_directory",
    "rename_noreplace",
    "revalidate_directory",
    "revalidate_regular",
]
