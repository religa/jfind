"""CLI tests. The TypeSafe client is replaced with a stub so no network calls are made."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from jfind import __version__, matcher
from jfind.cli import main


class FakeClient:
    """Answers 0.95 for anything whose name contains 'pay', 0.05 otherwise; classifies by extension."""

    calls: ClassVar[list[dict]] = []

    def __init__(self, model=None):
        self.model = model

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def system_one(self, state, questions):
        FakeClient.calls.append(state)
        p = 0.95 if "pay" in state["name"] else 0.05
        answers = {"match": SimpleNamespace(noul=p)}
        choices = {"kind": SimpleNamespace(choice="source" if state["ext"] == "py" else "other")}
        return SimpleNamespace(
            nouls=answers,
            choices=choices if "kind" in questions else {},
            usage=SimpleNamespace(input_tokens=100, output_tokens=0),
        )


@pytest.fixture
def tree(tmp_path: Path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "payments.py").write_text("def pay(): ...\n")
    (tmp_path / "src" / "auth.py").write_text("def login(): ...\n")
    (tmp_path / "pay.md").write_text("# pay\n")
    monkeypatch.setattr(matcher, "AsyncTypeSafeClient", FakeClient)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    FakeClient.calls.clear()
    return tmp_path


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_like_is_required():
    with pytest.raises(SystemExit) as e:
        main([])
    assert e.value.code == 2


def test_dry_run_makes_no_calls(tree, capsys):
    assert main([str(tree), "--like", "x", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("4 candidates") and "$" in out
    assert FakeClient.calls == []


def test_matches_sorted_and_exit_zero(tree, capsys):
    assert main([str(tree), "--like", "payments", "--explain"]) == 0
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert len(lines) == 2 and all(line.startswith("0.95  ") for line in lines)
    assert {os.path.basename(line.split("  ")[1]) for line in lines} == {"payments.py", "pay.md"}
    assert "2/4 matched" in err and "400 input tokens" in err


def test_no_match_exit_one(tree, capsys):
    assert main([str(tree), "--like", "payments", "--threshold", "0.99"]) == 1
    assert capsys.readouterr().out == ""


def test_kind_and_print0(tree, capsys):
    assert main([str(tree), "-type", "f", "-name", "*.py", "--like", "pay", "--kind", "--explain", "-print0"]) == 0
    out = capsys.readouterr().out
    assert out.endswith("\0") and "[source]" in out and out.count("\0") == 1


def test_local_filters_limit_api_calls(tree):
    main([str(tree), "-name", "*.py", "--like", "pay"])
    assert {s["name"] for s in FakeClient.calls} == {"payments.py", "auth.py"}


def test_missing_api_key(tree, monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setattr("jfind.cli.load_dotenv", lambda *a, **k: None)
    assert main([str(tree), "--like", "x"]) == 2
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err


def test_dotenv_is_read_from_cwd(tree, monkeypatch):
    """Regression: a tool-installed jfind lives outside the project, so .env must be found via cwd."""
    monkeypatch.delenv("TYPESAFE_API_KEY")
    (tree / ".env").write_text("TYPESAFE_API_KEY=from-dotenv\n")
    monkeypatch.chdir(tree)
    assert main([".", "--like", "pay"]) == 0
    assert FakeClient.calls  # got past the key check and called the (fake) API


def test_bad_root(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main([str(tmp_path / "nope"), "--like", "x"])
    assert e.value.code == 2
    assert "No such file" in capsys.readouterr().err


def test_file_roots(tree, capsys):
    a, b = tree / "src" / "payments.py", tree / "src" / "auth.py"
    assert main([str(a), str(b), "--like", "pay"]) == 0
    assert capsys.readouterr().out == f"{a}\n"


@pytest.mark.parametrize(
    "argv",
    [["--concurrency", "0"], ["--threshold", "1.5"], ["--threshold", "-0.1"], ["-maxdepth", "-1"]],
)
def test_argument_validation(tree, argv, capsys):
    with pytest.raises(SystemExit) as e:
        main([str(tree), "--like", "x", *argv])
    assert e.value.code == 2
    assert FakeClient.calls == []


def test_per_file_failure_keeps_other_results(tree, monkeypatch, capsys):
    class Flaky(FakeClient):
        async def system_one(self, state, questions):
            if state["name"] == "auth.py":
                raise TimeoutError("slow")
            return await super().system_one(state, questions)

    monkeypatch.setattr(matcher, "AsyncTypeSafeClient", Flaky)
    assert main([str(tree), "--like", "pay"]) == 2  # errors trump matches for the exit code…
    out, err = capsys.readouterr()
    assert "payments.py" in out and "pay.md" in out  # …but successful matches are still printed
    assert "auth.py: TimeoutError: slow" in err


def test_auth_error_is_fatal(tree, monkeypatch, capsys):
    import httpx2
    from typesafe_sdk import TypeSafeAuthenticationError

    class Unauthorized(FakeClient):
        async def system_one(self, state, questions):
            raise TypeSafeAuthenticationError(401, {}, httpx2.Headers(), message="bad key")

    monkeypatch.setattr(matcher, "AsyncTypeSafeClient", Unauthorized)
    assert main([str(tree), "--like", "x"]) == 2
    assert "TypeSafeAuthenticationError" in capsys.readouterr().err


def test_keyboard_interrupt(tree, monkeypatch):
    class Interrupt(FakeClient):
        async def system_one(self, state, questions):
            # A real Ctrl-C: SIGINT reaches asyncio.run()'s handler, which cancels the main task and
            # re-raises KeyboardInterrupt from run(). Park briefly so the cancellation can land.
            signal.raise_signal(signal.SIGINT)
            await asyncio.sleep(1)
            pytest.fail("SIGINT was not delivered to asyncio.run()")

    # asyncio.run() only installs its handler over the default one; a runner that ignores SIGINT
    # (e.g. pytest backgrounded from a non-interactive shell) would otherwise swallow the signal.
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        monkeypatch.setattr(matcher, "AsyncTypeSafeClient", Interrupt)
        assert main([str(tree), "--like", "x"]) == 130
    finally:
        signal.signal(signal.SIGINT, previous)


def test_non_ascii_path_output(tree, capsys):
    (tree / "płatności.py").write_text("")  # 'pay' is not in the name, so give the fake a reason to match
    (tree / "pay-zażółć.md").write_text("")
    assert main([str(tree), "--like", "pay"]) == 0
    assert "pay-zażółć.md" in capsys.readouterr().out


def test_bad_newer(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main([str(tmp_path), "-newer", str(tmp_path / "nope"), "--like", "x"])
    assert e.value.code == 2
    assert "-newer" in capsys.readouterr().err


def test_api_error_is_reported(tree, monkeypatch, capsys):
    class Boom(FakeClient):
        async def system_one(self, state, questions):
            raise RuntimeError("rate limited")

    monkeypatch.setattr(matcher, "AsyncTypeSafeClient", Boom)
    assert main([str(tree), "--like", "x"]) == 2
    assert "RuntimeError: rate limited" in capsys.readouterr().err


def test_dry_run_estimate_is_calibrated(tree):
    """The estimate must land near what jev bills, not just count the JSON payload.

    Measured live: a metadata-only request bills ~400 input tokens (the figure the README quotes).
    """
    from jfind.walk import Filters, collect

    c = next(c for c in collect([tree], Filters()) if c.path.name == "auth.py")
    per_file = matcher.estimate_tokens([c], matcher.build_questions("handles user authentication", False))
    assert 300 <= per_file <= 500, per_file


@pytest.mark.parametrize("flag", ["-l", "-like", "--like"])
def test_like_aliases(tree, flag):
    """-like must be an exact alias, or argparse would read it as `-l ike` and search for 'ike'."""
    assert main([str(tree), flag, "pay", "--dry-run"]) == 0
