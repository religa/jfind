# jfind — find files by describing them

[![CI](https://github.com/religa/jfind/actions/workflows/ci.yml/badge.svg)](https://github.com/religa/jfind/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/jfind-cli)](https://pypi.org/project/jfind-cli/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**jfind** is a command-line file search tool that understands plain English. It works like the
Unix `find` command, with one extra predicate — `--like "…"` — that matches files by *what they
are* rather than by name pattern. Instead of guessing a glob or a regex, describe the file:

```sh
jfind . --like "unit tests for the payment module"
jfind src -type f -name '*.py' --like "handles user authentication" --content
jfind . --like "image or binary asset" -print0 | xargs -0 ls -l
```

![jfind demo: install, jfind -h, and a semantic search](demo.gif)

The semantic matching is done by [TypeSafe.ai](https://typesafe.ai)'s **jev** model, which
answers typed yes/no questions with a calibrated probability. Everything else — walking the tree,
`-name`, `-type`, `-maxdepth`, `.gitignore` — runs locally, so only the files that pass your
ordinary filters are ever sent for a semantic check.

## Table of contents

- [Why jfind](#why-jfind)
- [Installation](#installation)
- [Usage](#usage)
- [How it works](#how-it-works)
- [What gets sent to the API](#what-gets-sent-to-the-api)
- [Cost](#cost)
- [Compared to find, fd and ripgrep](#compared-to-find-fd-and-ripgrep)
- [FAQ](#faq)
- [Development](#development)

## Why jfind

Classic file search tools match *names* (`find -name`, `fd`) or *contents* (`grep`, `ripgrep`).
Neither helps when you know what a file is for but not what it's called: "the config that sets
up logging", "anything to do with refunds", "the script that seeds the database". jfind answers
those questions in one command, ranks results by confidence, and composes with `xargs`, `fzf`
and shell pipelines like any other find-alternative.

## Installation

```sh
uv tool install jfind-cli        # recommended: isolated, on PATH as `jfind`
pipx install jfind-cli           # alternative
pip install jfind-cli            # into the current environment
```

The PyPI package is `jfind-cli`; the command and the Python module are `jfind`.
Requires Python 3.13+ and a TypeSafe.ai API key from
[console.typesafe.ai/keys](https://console.typesafe.ai/keys):

```sh
export TYPESAFE_API_KEY=...      # or put it in a .env file in the current or a parent directory
```

## Usage

```
jfind [PATH ...] -l TEXT [options]          # PATH may be a directory or a file

  -l, --like TEXT   natural-language description to match (also -like)

  -name GLOB        match basename against a shell glob (case-sensitive)
  -type f|d         restrict to files or directories
  -maxdepth N       descend at most N levels
  -newer FILE       only entries modified after FILE
  --hidden          include dotfiles and dotdirs
  --no-ignore       do not honour .gitignore (repo-root, root and nested files all apply)
  --content         also send the first 8 KiB of each text file (binaries are skipped)
  --threshold P     minimum match probability (default 0.7)
  --kind            also classify each match: source/test/config/docs/data/binary/other
  --explain         print the probability (and kind) next to each path
  --dry-run         show candidate count and cost estimate; makes no API calls
  -print0           NUL-separate output for xargs -0
  --concurrency N   parallel requests (default 20)
  --model ID        jev model id (default: jev-latest)
```

### Examples

```sh
# Where is the code that talks to Stripe?
jfind src --like "integration with the Stripe payment API" --content

# Find test files for a feature, then run them
jfind tests --like "tests for user login and sessions" -print0 | xargs -0 pytest

# Which of the files changed since the last pull are documentation?
jfind . -newer .git/ORIG_HEAD --like "documentation or prose" --kind --explain

# Interactive: pick one of the semantic matches with fzf (-l is short for --like)
jfind . -l "database migration" | fzf

# Estimate before you spend anything
jfind . --like "anything" --content --dry-run
```

A run with `--explain` shows the probability jev assigned to each match and the token count:

```sh
$ jfind . --like "anything about payments" --content --kind --threshold 0.5 --explain
0.99  ./docs/payments.md  [docs]
0.99  ./src/payments/charge.py  [source]
0.98  ./tests/test_payments.py  [test]
jfind: 3/14 matched, 7,661 input tokens (~$0.0003)
```

### Exit codes and platform notes

Exit codes follow `grep`: `0` = at least one match, `1` = none, `2` = error (matches are still
printed; a single failed request never discards the others), `130` = interrupted.

Symlinks are never followed (like `find -P`): they are neither files nor directories, and
`--content` never reads outside the tree through one. Works the same on macOS, Linux and
Windows: paths are printed exactly as the filesystem stores them, "hidden" always means
dot-prefixed, and `.gitignore` matching does not depend on the path separator. On Windows, run it
from PowerShell or cmd and quote `--like` the same way.

## How it works

1. **Walk locally.** jfind walks the directory tree and applies every classic predicate
   (`-name`, `-type`, `-maxdepth`, `-newer`, hidden files, `.gitignore`) before touching the network.
2. **Ask one question per candidate.** Each surviving entry is described to jev as structured
   JSON — path, name, extension, size, modification time and, with `--content`, the first 8 KiB
   of text — together with a single yes/no question: *does this match "…"?*
3. **Keep the confident ones.** jev returns a probability between 0 and 1. Entries at or above
   `--threshold` are printed, highest probability first.

jev is not a chat model. It is a purpose-built model for typed questions (yes/no, multiple
choice, scored), which is exactly the shape of a `find` predicate: cheap, parallel and calibrated.
Requests run concurrently (`--concurrency`), so a few thousand files take seconds.

## What gets sent to the API

Only the entries that pass your local filters, and for each of them only:

- the path relative to the root you gave, the file name and extension
- size and modification time
- with `--content`: the first 8 KiB of the file, if it is text (binaries are detected and skipped)

Nothing else leaves your machine. `--dry-run` shows how many entries would be sent and what it
would cost, without sending anything.

## Cost

jev charges $0.042 per **million** input tokens; output is free. A metadata-only query costs
roughly 400 tokens per file, `--content` a few thousand — so scanning a 1,000-file project is
well under a cent. `--dry-run` prints an estimate first.

## Compared to find, fd and ripgrep

| Tool | Matches on | Example |
|---|---|---|
| `find` / `fd` | file name, type, size, time | `fd -e py test_` |
| `grep` / `ripgrep` | literal text or regex inside files | `rg "def charge"` |
| **`jfind`** | a natural-language description of the file | `jfind --like "charges a customer's card"` |

jfind is not a replacement for any of them: it takes the same predicates as `find`, prints the
same kind of output, and is meant to sit in the same pipelines. Use it when the thing you know
about a file is what it *does*, not what it's called.

## FAQ

**Does it work offline?** No — the semantic matching runs on TypeSafe.ai's API. Everything
else (walking, filtering, `--dry-run`) is local.

**Does it read my whole codebase?** Only metadata by default. `--content` adds the first 8 KiB
of each text file that passed your local filters. See [What gets sent](#what-gets-sent-to-the-api).

**How accurate is it?** jev returns a probability, not a verdict, so you choose the trade-off:
raise `--threshold` for precision, lower it for recall, and use `--explain` to see the scores.
Descriptions in English work best.

**Can I use it in scripts?** Yes. Output is one path per line (or `-print0`), exit codes follow
`grep`, and errors go to stderr.

**Why is the package called `jfind-cli`?** The name `jfind` was already taken on PyPI. The
command you run and the module you import are still `jfind`.

## Development

```sh
git clone https://github.com/religa/jfind && cd jfind
uv sync --group dev
uv run pytest            # unit tests; the API client is stubbed, no key needed
uv run ruff check . && uv run ruff format --check .
uv run jfind . --like "the CLI entry point" --explain   # live, needs TYPESAFE_API_KEY
uv build                 # sdist + wheel into dist/
```

Layout: `src/jfind/walk.py` (local filtering), `matcher.py` (jev questions, async fan-out),
`cli.py` (argparse + output).

Releases: bump `version` in `pyproject.toml` and `CHANGELOG.md`, tag `vX.Y.Z`, publish a GitHub
release — `.github/workflows/publish.yml` uploads to PyPI via Trusted Publishing.

## License

[MIT](LICENSE)
