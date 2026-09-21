"""jfind — a simplified `find` whose match predicate is answered by TypeSafe.ai's jev model."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("jfind-cli")
except PackageNotFoundError:  # running from a source checkout without an install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
