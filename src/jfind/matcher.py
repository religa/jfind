"""The semantic predicate: one Noul question per candidate, answered by jev."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    Noul,
    TypeSafeAuthenticationError,
    TypeSafePermissionDeniedError,
)

if TYPE_CHECKING:
    from .walk import Candidate

KINDS = {
    "source": "program source code",
    "test": "automated tests",
    "config": "configuration, build or packaging metadata",
    "docs": "documentation or prose",
    "data": "data files (json, csv, fixtures, etc.)",
    "binary": "compiled, archived or media files",
    "other": "none of the above",
}

# Tokens jev bills per request beyond the state and questions themselves (measured with --explain:
# a metadata-only request costs ~400 tokens, of which ~100 are the JSON payload).
REQUEST_OVERHEAD_TOKENS = 300

# Errors that will fail identically for every request; no point continuing.
FATAL_ERRORS = (TypeSafeAuthenticationError, TypeSafePermissionDeniedError)


@dataclass(slots=True)
class Match:
    candidate: Candidate
    probability: float
    kind: str | None
    input_tokens: int


@dataclass(slots=True)
class Failure:
    candidate: Candidate
    error: Exception


def build_questions(query: str, classify: bool) -> dict:
    questions = {
        "match": Noul(
            instructions=(
                "You are evaluating a single filesystem entry described in the state. "
                f"Does it match this description: {query!r}?"
            ),
            criteria={
                "true": "The entry clearly fits the description.",
                "false": "The entry does not fit the description.",
            },
        )
    }
    if classify:
        questions["kind"] = Choice(instructions="What kind of file is this?", criteria=KINDS)
    return questions


def estimate_tokens(candidates: list[Candidate], questions: dict) -> int:
    """Rough estimate of input tokens across all requests, for --dry-run.

    ~4 chars/token plus a fixed per-request overhead; measured within ~10% of what jev bills.
    """
    q_chars = sum(len(json.dumps(q.model_dump(), default=str)) for q in questions.values())
    return sum(REQUEST_OVERHEAD_TOKENS + (len(json.dumps(c.to_state())) + q_chars) // 4 for c in candidates)


async def score_all(
    candidates: list[Candidate],
    query: str,
    *,
    classify: bool = False,
    concurrency: int = 20,
    model: str | None = None,
) -> tuple[list[Match], list[Failure]]:
    """Score every candidate.

    Per-file errors are collected, not raised, so one bad request doesn't discard the rest;
    errors that would fail every request are raised immediately.
    """
    questions = build_questions(query, classify)
    sem = asyncio.Semaphore(concurrency)

    async with AsyncTypeSafeClient(model=model) as client:

        async def one(c: Candidate) -> Match | Failure:
            try:
                async with sem:
                    res = await client.system_one(c.to_state(), questions)
            except FATAL_ERRORS:
                raise
            except Exception as e:  # noqa: BLE001 - any per-file error is reported, not fatal
                return Failure(c, e)
            kind = res.choices["kind"].choice if classify else None
            return Match(c, res.nouls["match"].noul, kind, res.usage.input_tokens)

        results = await asyncio.gather(*(one(c) for c in candidates))

    matches = [r for r in results if isinstance(r, Match)]
    failures = [r for r in results if isinstance(r, Failure)]
    return matches, failures
