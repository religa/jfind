"""Local candidate collection: the cheap `find` predicates that never touch the API."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pathspec

# Content snippet cap. jev's `state` limit is 32k tokens; 8 KiB of text stays far below it.
HEAD_BYTES = 8 * 1024

ErrorHandler = Callable[[OSError], None]


@dataclass(slots=True)
class Candidate:
    path: Path
    rel: str
    is_dir: bool
    size: int
    mtime: float
    head: str | None = None

    def to_state(self) -> dict:
        state = {
            "path": self.rel,
            "name": self.path.name,
            "type": "directory" if self.is_dir else "file",
            "ext": self.path.suffix.lstrip(".") or None,
            "size_bytes": self.size,
            "modified": datetime.fromtimestamp(self.mtime, tz=UTC).isoformat(timespec="seconds"),
        }
        if self.head is not None:
            state["head"] = self.head
        return state


@dataclass(slots=True)
class Filters:
    name: str | None = None
    kind: str | None = None  # "f" | "d" | None
    maxdepth: int | None = None
    newer_than: float | None = None
    content: bool = False
    hidden: bool = False
    respect_gitignore: bool = True


class IgnoreRules:
    """Git-style ignore rules: every .gitignore from the repo root down to the walked directory.

    Each spec applies to paths relative to the directory its .gitignore lives in, which is how
    git scopes them. Nested .gitignore files are added as the walk discovers them.
    """

    def __init__(self) -> None:
        self._specs: list[tuple[Path, pathspec.PathSpec]] = []

    @classmethod
    def for_root(cls, root: Path) -> IgnoreRules:
        rules = cls()
        # Collect ancestors up to and including the repo root (the directory holding .git). If no
        # repo is found, only the root's own .gitignore applies.
        chain = [root]
        if not (root / ".git").exists():  # root is not itself a repo: look upwards
            for parent in root.parents:
                chain.append(parent)
                if (parent / ".git").exists():
                    break
            else:
                chain = [root]
        for directory in reversed(chain):  # outermost first
            rules.add(directory)
        return rules

    def add(self, directory: Path) -> None:
        gi = directory / ".gitignore"
        if not gi.is_file():
            return
        try:
            lines = gi.read_text(errors="replace").splitlines()
        except OSError:
            return
        self._specs.append((directory, pathspec.PathSpec.from_lines("gitignore", lines)))

    def ignored(self, path: Path, is_dir: bool) -> bool:
        for base, spec in self._specs:
            try:
                rel = path.relative_to(base).as_posix()
            except ValueError:
                continue
            if spec.match_file(rel + "/" if is_dir else rel):
                return True
        return False


def read_head(path: Path) -> str | None:
    """First HEAD_BYTES of a text file, or None when the file looks binary/unreadable."""
    try:
        with path.open("rb") as fh:
            chunk = fh.read(HEAD_BYTES)
    except OSError:
        return None
    if b"\x00" in chunk:
        return None
    return chunk.decode("utf-8", errors="replace")


def _passes(name: str, is_dir: bool, filters: Filters) -> bool:
    """The -type and -name predicates."""
    if (filters.kind == "f" and is_dir) or (filters.kind == "d" and not is_dir):
        return False
    return not filters.name or fnmatch.fnmatchcase(name, filters.name)


def _candidate(p: Path, rel: str, is_dir: bool, filters: Filters, on_error: ErrorHandler | None) -> Candidate | None:
    try:
        st = p.stat()
    except OSError as e:
        if on_error:
            on_error(e)
        return None
    if filters.newer_than is not None and st.st_mtime <= filters.newer_than:
        return None
    head = read_head(p) if filters.content and not is_dir else None
    return Candidate(p, rel, is_dir, st.st_size, st.st_mtime, head)


def _prune(
    cur: Path, dirnames: list[str], filenames: list[str], filters: Filters, rules: IgnoreRules | None
) -> list[tuple[str, bool]]:
    """Drop hidden/ignored names and return `cur`'s (name, is_dir) entries, directories first.

    `dirnames` is trimmed in place so os.walk never descends into a pruned tree.
    """
    dirnames[:] = sorted(
        d
        for d in dirnames
        if (filters.hidden or not d.startswith(".")) and not (rules and rules.ignored(cur / d, True))
    )
    files = sorted(f for f in filenames if filters.hidden or not f.startswith("."))
    return [(d, True) for d in dirnames] + [(f, False) for f in files]


def _walk(given: Path, filters: Filters, on_error: ErrorHandler | None) -> Iterator[Candidate]:
    root = given.resolve()
    rules = IgnoreRules.for_root(root) if filters.respect_gitignore else None
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
        cur = Path(dirpath)
        depth = len(cur.parts) - base_depth
        if rules and depth > 0:
            rules.add(cur)  # nested .gitignore scopes to this subtree
        entries = _prune(cur, dirnames, filenames, filters, rules)
        # Entries of `cur` sit at depth+1; list them only within -maxdepth and stop descending at the limit.
        if filters.maxdepth is not None:
            if depth >= filters.maxdepth:
                dirnames[:] = []
                continue
            if depth + 1 >= filters.maxdepth:
                dirnames[:] = []
        for name, is_dir in entries:
            p = cur / name
            # Like find -P: never follow symlinks. Their targets are reachable by their real
            # path if they are in the tree, and --content must not read outside it.
            if p.is_symlink() or not _passes(name, is_dir, filters):
                continue
            if not is_dir and rules and rules.ignored(p, False):
                continue
            # os.path.join, not Path: keeps the root verbatim (Path would turn "./src" into "src").
            rel = os.path.join(str(given), str(p.relative_to(root)))  # noqa: PTH118
            if c := _candidate(p, rel, is_dir, filters, on_error):
                yield c


def collect(roots: list[Path], filters: Filters, on_error: ErrorHandler | None = None) -> list[Candidate]:
    """Walk `roots` and return every entry passing the local filters.

    Output paths are prefixed with the root exactly as given, like find(1). A root that is a file
    is returned as a single candidate. `on_error` receives OSErrors from unreadable directories.
    """
    out: list[Candidate] = []
    for given in roots:
        if given.is_file():
            if _passes(given.name, False, filters) and (c := _candidate(given, str(given), False, filters, on_error)):
                out.append(c)
        else:
            out.extend(_walk(given, filters, on_error))
    return out
