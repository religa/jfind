from __future__ import annotations

import argparse
import asyncio
import errno
import os
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from . import __version__, matcher
from .walk import Filters, collect

PRICE_PER_M_TOKENS = 0.042  # USD, jev input tokens (output is free)

EXIT_MATCH, EXIT_NO_MATCH, EXIT_ERROR, EXIT_INTERRUPTED = 0, 1, 2, 130


def _probability(text: str) -> float:
    v = float(text)
    if not 0.0 <= v <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return v


def _positive_int(text: str) -> int:
    v = int(text)
    if v < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return v


def _non_negative_int(text: str) -> int:
    v = int(text)
    if v < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return v


def _existing_path(text: str) -> Path:
    p = Path(text)
    if not p.exists():
        raise argparse.ArgumentTypeError(f"{text}: No such file or directory")
    return p


def _mtime_of(text: str) -> float:
    try:
        return Path(text).stat().st_mtime
    except OSError as e:
        raise argparse.ArgumentTypeError(f"{text}: {e.strerror}") from e


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jfind",
        description="find(1) with a semantic predicate answered by TypeSafe.ai's jev model.",
        epilog='example: jfind src -type f --like "unit tests for the payment module" --content',
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "paths", nargs="*", type=_existing_path, default=[Path()], help="root directories or files (default: .)"
    )
    p.add_argument("-l", "-like", "--like", required=True, metavar="TEXT", help="natural-language description to match")
    # classic find predicates, evaluated locally before anything is sent to the API
    p.add_argument("-name", metavar="GLOB", help="match basename against a shell glob (case-sensitive)")
    p.add_argument("-type", choices=["f", "d"], help="restrict to files (f) or directories (d)")
    p.add_argument("-maxdepth", type=_non_negative_int, metavar="N", help="descend at most N levels")
    p.add_argument("-newer", type=_mtime_of, metavar="FILE", help="only entries modified after FILE")
    p.add_argument("--hidden", action="store_true", help="include dotfiles and dotdirs")
    p.add_argument("--no-ignore", action="store_true", help="do not honour .gitignore")
    # semantic options
    p.add_argument("--content", action="store_true", help="also send the first 8 KiB of each text file")
    p.add_argument(
        "--threshold", type=_probability, default=0.7, metavar="P", help="min match probability (default 0.7)"
    )
    p.add_argument("--kind", action="store_true", help="also classify each match (source/test/config/...)")
    p.add_argument("--model", default=None, help="jev model id (default: TYPESAFE_DEFAULT_MODEL or jev-latest)")
    p.add_argument("--concurrency", type=_positive_int, default=20, metavar="N", help="parallel requests (default 20)")
    # output
    p.add_argument("--explain", action="store_true", help="print probability (and kind) next to each path")
    p.add_argument("--dry-run", action="store_true", help="show candidate count and cost estimate, do not call the API")
    p.add_argument("-print0", action="store_true", help="separate output with NUL instead of newline")
    return p


def _warn(msg: str) -> None:
    print(f"jfind: {msg}", file=sys.stderr)


def _fmt_cost(tokens: int) -> str:
    cost = tokens / 1e6 * PRICE_PER_M_TOKENS
    return f"~${cost:.4f}" if cost >= 0.0001 else "<$0.0001"  # noqa: PLR2004 - the display precision


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    except OSError as e:
        if e.errno not in (errno.EPIPE, errno.EINVAL):  # EINVAL is what Windows raises for a closed pipe
            raise
        # Downstream (e.g. `| head`) closed the pipe; exit quietly without a second error on flush.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return EXIT_MATCH


def _emit(line: str, sep: bytes) -> None:
    # Bytes, not text: paths round-trip exactly (os.fsencode restores undecodable POSIX names and is
    # UTF-8 on Windows) and a redirected stdout on Windows can't choke on its cp1252 default.
    sys.stdout.buffer.write(os.fsencode(line) + sep)


def _run(argv: list[str] | None) -> int:
    # usecwd: search from the working directory, not from this file (which lives in a tool venv).
    load_dotenv(find_dotenv(usecwd=True))
    args = build_parser().parse_args(argv)
    filters = Filters(
        name=args.name,
        kind=args.type,
        maxdepth=args.maxdepth,
        newer_than=args.newer,
        content=args.content,
        hidden=args.hidden,
        respect_gitignore=not args.no_ignore,
    )
    walk_errors = 0

    def on_walk_error(e: OSError) -> None:
        nonlocal walk_errors
        walk_errors += 1
        _warn(f"{e.filename}: {e.strerror}")

    candidates = collect(args.paths, filters, on_error=on_walk_error)

    if args.dry_run:
        tokens = matcher.estimate_tokens(candidates, matcher.build_questions(args.like, args.kind))
        print(f"{len(candidates)} candidates, ~{tokens:,} input tokens, {_fmt_cost(tokens)}")
        return EXIT_ERROR if walk_errors else EXIT_MATCH
    if not candidates:
        return EXIT_ERROR if walk_errors else EXIT_NO_MATCH
    if not os.environ.get("TYPESAFE_API_KEY"):
        _warn("TYPESAFE_API_KEY is not set (put it in .env or the environment)")
        return EXIT_ERROR

    try:
        matches, failures = asyncio.run(
            matcher.score_all(candidates, args.like, classify=args.kind, concurrency=args.concurrency, model=args.model)
        )
    except Exception as e:  # noqa: BLE001 - SDK errors carry a readable message; keep the CLI quiet
        _warn(f"{type(e).__name__}: {e}")
        return EXIT_ERROR
    return _report(args, matches, failures, walk_errors)


def _report(
    args: argparse.Namespace, matches: list[matcher.Match], failures: list[matcher.Failure], walk_errors: int
) -> int:
    """Print the hits and the stderr summary; return the exit code."""
    sep = b"\0" if args.print0 else b"\n"
    hits = [m for m in matches if m.probability >= args.threshold]
    hits.sort(key=lambda m: -m.probability)
    sys.stdout.flush()  # keep any earlier text output ahead of the raw bytes
    for m in hits:
        line = m.candidate.rel
        if args.explain:
            line = f"{m.probability:.2f}  {line}" + (f"  [{m.kind}]" if m.kind else "")
        _emit(line, sep)
    sys.stdout.buffer.flush()

    for f in failures:
        _warn(f"{f.candidate.rel}: {type(f.error).__name__}: {f.error}")
    if args.explain:
        total = sum(m.input_tokens for m in matches)
        _warn(f"{len(hits)}/{len(matches)} matched, {total:,} input tokens ({_fmt_cost(total)})")

    if failures or walk_errors:
        return EXIT_ERROR  # like grep: errors trump a match, but matches were still printed
    return EXIT_MATCH if hits else EXIT_NO_MATCH
