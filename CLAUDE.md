# jfind

`find(1)` clone whose `--like TEXT` predicate is answered by TypeSafe.ai's jev model
(`POST /v1/systemone`, one Noul yes/no question per candidate). PyPI name is `jfind-cli`
(`jfind` is taken); the command and module are `jfind`.

## Layout

- `src/jfind/walk.py` — local walk and filters (`-name`, `-type`, `-maxdepth`, `.gitignore`, hidden). Never touches the network.
- `src/jfind/matcher.py` — builds the jev questions, async fan-out, per-file error collection.
- `src/jfind/cli.py` — argparse, `.env` loading, output, exit codes (0 match / 1 none / 2 error / 130 interrupted).
- `tests/` — the API client is stubbed (`FakeClient` in `test_cli.py`); no key needed.

## Commands

```sh
uv sync --group dev
uv run pytest -q
uv run ruff check . && uv run ruff format .
uv run jfind . -l "…" --dry-run        # no API calls
uv run jfind . -l "…" --explain        # live; reads TYPESAFE_API_KEY from .env
uv build && uv tool install --reinstall .
```

## Rules

- **Communication style:** use ASD-STE-100 when you speak to the user, and when you edit this file.
- Python 3.13+, `uv` only. Keep `uv.lock` committed.
- No OS-specific branches (`sys.platform`) — behaviour must be identical on macOS, Linux and Windows.
  Paths are written as bytes via `os.fsencode`; "hidden" means dot-prefixed everywhere.
- Every classic predicate runs locally before anything is sent; keep `--dry-run` free of API calls.
- Never read or print `.env`.
- Release: bump `version` in `pyproject.toml` + `CHANGELOG.md`, tag `vX.Y.Z`, publish a GitHub release
  (`publish.yml` uses PyPI Trusted Publishing).
