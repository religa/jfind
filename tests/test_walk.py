import os
import time
from pathlib import Path

from jfind.walk import Filters, collect, read_head


def make_tree(root: Path) -> None:
    (root / "src" / "payments").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "build").mkdir()
    (root / ".hidden").mkdir()
    (root / "src" / "payments" / "charge.py").write_bytes(b"def charge(): ...\n")  # bytes: no CRLF on Windows
    (root / "src" / "auth.py").write_text("def login(): ...\n")
    (root / "tests" / "test_auth.py").write_text("def test_login(): ...\n")
    (root / "build" / "out.o").write_bytes(b"\x00\x01\x02")
    (root / ".hidden" / "secret.txt").write_text("x")
    (root / ".env").write_text("KEY=1")
    (root / "logo.png").write_bytes(b"\x89PNG\x00\x00")


def rels(cands) -> list[str]:
    return sorted(c.rel for c in cands)


def test_collect_skips_hidden_and_prefixes_root(tmp_path: Path):
    make_tree(tmp_path)
    got = rels(collect([tmp_path], Filters()))
    assert str(tmp_path / "src" / "payments" / "charge.py") in got
    assert not any(".hidden" in r or r.endswith(".env") for r in got)


def test_relative_root_is_kept_verbatim(tmp_path: Path, monkeypatch):
    make_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    got = rels(collect([Path("src")], Filters()))
    assert got == [
        os.path.join("src", "auth.py"),
        os.path.join("src", "payments"),
        os.path.join("src", "payments", "charge.py"),
    ]


def test_type_and_name_filters(tmp_path: Path):
    make_tree(tmp_path)
    files = collect([tmp_path], Filters(kind="f", name="*.py"))
    assert all(not c.is_dir and c.path.suffix == ".py" for c in files)
    assert len(files) == 3
    dirs = collect([tmp_path], Filters(kind="d"))
    assert all(c.is_dir for c in dirs)
    assert {c.path.name for c in dirs} == {"src", "payments", "tests", "build"}


def test_maxdepth(tmp_path: Path):
    make_tree(tmp_path)
    got = {c.path.name for c in collect([tmp_path], Filters(maxdepth=1))}
    assert "src" in got and "auth.py" not in got


def test_gitignore_prunes(tmp_path: Path):
    make_tree(tmp_path)
    (tmp_path / ".gitignore").write_text("build/\n*.png\n")
    got = {c.path.name for c in collect([tmp_path], Filters())}
    assert "build" not in got and "out.o" not in got and "logo.png" not in got
    got_all = {c.path.name for c in collect([tmp_path], Filters(respect_gitignore=False))}
    assert "out.o" in got_all and "logo.png" in got_all


def test_newer_than(tmp_path: Path):
    make_tree(tmp_path)
    cutoff = time.time() + 10
    assert collect([tmp_path], Filters(newer_than=cutoff)) == []
    assert collect([tmp_path], Filters(newer_than=0)) != []


def test_content_head_and_binary_detection(tmp_path: Path):
    make_tree(tmp_path)
    by_name = {c.path.name: c for c in collect([tmp_path], Filters(content=True))}
    assert by_name["charge.py"].head == "def charge(): ...\n"
    assert by_name["out.o"].head is None  # binary
    assert by_name["src"].head is None  # directory
    assert read_head(tmp_path / "does-not-exist") is None


def test_to_state_shape(tmp_path: Path):
    make_tree(tmp_path)
    c = next(c for c in collect([tmp_path], Filters(content=True)) if c.path.name == "auth.py")
    state = c.to_state()
    assert state["name"] == "auth.py" and state["ext"] == "py" and state["type"] == "file"
    assert state["size_bytes"] > 0 and state["modified"].endswith("+00:00")
    assert "head" in state
    assert "head" not in next(x for x in collect([tmp_path], Filters()) if x.path.name == "auth.py").to_state()


def test_gitignore_from_repo_root_applies_to_subdir_root(tmp_path: Path):
    """`jfind src` inside a repo must honour the repo's top-level .gitignore."""
    make_tree(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    (tmp_path / "src" / "__pycache__").mkdir()
    (tmp_path / "src" / "__pycache__" / "auth.cpython-313.pyc").write_bytes(b"\x00")
    (tmp_path / "src" / "stray.pyc").write_bytes(b"\x00")
    got = {c.path.name for c in collect([tmp_path / "src"], Filters())}
    assert "__pycache__" not in got and "stray.pyc" not in got and "auth.py" in got


def test_nested_gitignore_is_scoped(tmp_path: Path):
    make_tree(tmp_path)
    (tmp_path / "src" / ".gitignore").write_text("auth.py\n")
    (tmp_path / "tests" / "auth.py").write_text("")  # same name, outside the nested ignore's scope
    got = {c.rel for c in collect([tmp_path], Filters())}
    assert str(tmp_path / "src" / "auth.py") not in got
    assert str(tmp_path / "tests" / "auth.py") in got


def test_hidden_flag(tmp_path: Path):
    make_tree(tmp_path)
    got = {c.path.name for c in collect([tmp_path], Filters(hidden=True))}
    assert ".hidden" in got and "secret.txt" in got and ".env" in got


def test_file_root(tmp_path: Path):
    make_tree(tmp_path)
    f = tmp_path / "src" / "auth.py"
    got = collect([f], Filters())
    assert [c.rel for c in got] == [str(f)] and not got[0].is_dir
    assert collect([f], Filters(kind="d")) == []


def test_name_is_case_sensitive(tmp_path: Path):
    make_tree(tmp_path)
    assert collect([tmp_path], Filters(name="*.PY")) == []


def test_unreadable_dir_reports_error(tmp_path: Path, monkeypatch):
    """Simulated via scandir rather than chmod, which is a no-op on Windows and for root."""
    make_tree(tmp_path)
    locked = tmp_path / "locked"
    locked.mkdir()
    real_scandir = os.scandir

    def denied(path=".", *a, **k):
        if Path(path) == locked:
            raise PermissionError(13, "Permission denied", str(locked))
        return real_scandir(path, *a, **k)

    monkeypatch.setattr(os, "scandir", denied)
    errors: list[OSError] = []
    got = {c.path.name for c in collect([tmp_path], Filters(), on_error=errors.append)}
    assert "locked" in got  # the entry itself is listed…
    assert len(errors) == 1 and errors[0].filename == str(locked)  # …but descending into it is reported


def test_non_ascii_names(tmp_path: Path):
    make_tree(tmp_path)
    (tmp_path / "zażółć.py").write_text("x")
    got = {c.path.name for c in collect([tmp_path], Filters(name="*.py"))}
    assert "zażółć.py" in got


def test_root_that_is_a_repo_ignores_ancestor_gitignore(tmp_path: Path):
    """Git stops at the nearest .git; an outer repo's rules must not leak into an inner one."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("*.py\n")
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    (proj / "app.py").write_text("x")
    assert {c.path.name for c in collect([proj], Filters())} == {"app.py"}


def test_file_root_honours_name_filter(tmp_path: Path):
    make_tree(tmp_path)
    f = tmp_path / "src" / "auth.py"
    assert collect([f], Filters(name="*.py")) != []
    assert collect([f], Filters(name="*.md")) == []


def test_symlinks_are_not_followed(tmp_path: Path):
    """Like find -P: a symlink is neither a file nor a directory, and its target is never read.

    With --content, following one could ship data from outside the tree to the API.
    """
    make_tree(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("secret")
    (tmp_path / "leak.txt").symlink_to(outside)
    (tmp_path / "srclink").symlink_to(tmp_path / "src")
    (tmp_path / "dangling").symlink_to(tmp_path / "nope")
    got = {c.path.name for c in collect([tmp_path], Filters(content=True))}
    assert "leak.txt" not in got and "srclink" not in got and "dangling" not in got
    assert "auth.py" in got
