# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-09-20

Initial release.

### Added
- `find(1)`-style walk with a semantic `--like TEXT` (`-l`, `-like`) predicate answered by
  TypeSafe.ai's jev model.
- Local predicates, evaluated before any API call: `-name`, `-type`, `-maxdepth`, `-newer`,
  `--hidden`, `.gitignore` rules (`--no-ignore` to disable). Symlinks are never followed.
- `--content` (first 8 KiB of each text file), `--kind` (classify matches), `--threshold`,
  `--explain`, `--dry-run` (cost estimate, no API calls), `-print0`, `--concurrency`, `--model`.
- Exit codes follow `grep`: 0 match, 1 none, 2 error, 130 interrupted. Per-file API failures
  go to stderr; other results are still printed.
- Paths are written as raw filesystem bytes, so non-ASCII names survive redirection on every OS.
- `TYPESAFE_API_KEY` is read from the environment or from a `.env` file in the current or a
  parent directory.

[Unreleased]: https://github.com/religa/jfind/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/religa/jfind/releases/tag/v0.1.0
