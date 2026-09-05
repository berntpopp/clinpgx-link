"""ClinPGx Link package metadata."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("clinpgx-link")
except PackageNotFoundError:  # pragma: no cover - source trees are installed by uv
    __version__ = "0+unknown"

__all__ = ["__version__"]
