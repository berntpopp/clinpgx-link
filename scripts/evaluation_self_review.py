"""Extract bounded, raw consumer self-review evidence from a normalized run."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Annotated, Literal, NoReturn

from pydantic import Field, ValidationError, field_validator, model_validator

from scripts.evaluation_contracts import (
    ASPECTS,
    AspectScores,
    GitSha,
    Sha256,
    StrictModel,
    StrictNonNegativeInt,
    StrictScore,
)
from scripts.evaluation_traces import AdapterInputError, NormalizedRun, parse_json

MAX_FINAL_BYTES = 1024 * 1024
MAX_CANDIDATE_BYTES = 256 * 1024
MAX_JSON_CODEBLOCKS = 32
MAX_JSON_DEPTH = 64
MAX_RECOMMENDATIONS_BYTES = 65_536
MAX_REVIEW_TEXT_CHARACTERS = 16_384

SelfReviewFailureReason = Literal[
    "invalid_run",
    "final_answer_missing",
    "invalid_unicode",
    "final_answer_too_large",
    "too_many_json_codeblocks",
    "review_not_found",
    "multiple_review_candidates",
    "candidate_too_large",
    "candidate_too_deep",
    "malformed_candidate",
    "invalid_review",
    "recommendations_too_large",
]
ReviewText = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=MAX_REVIEW_TEXT_CHARACTERS),
]


class SelfReviewExtractionError(ValueError):
    """Safe fixed-reason failure at the self-review extraction boundary."""

    def __init__(self, reason: SelfReviewFailureReason) -> None:
        self.reason = reason
        super().__init__(reason)


class ReviewAspect(StrictModel):
    score: StrictScore | None
    evidence: ReviewText
    improvement: ReviewText

    @field_validator("evidence", "improvement")
    @classmethod
    def prose_is_not_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("review prose must not be whitespace")
        return value


class SelfReview(StrictModel):
    correctness_confidence: ReviewAspect
    completeness: ReviewAspect
    discoverability: ReviewAspect
    token_efficiency: ReviewAspect
    speed: ReviewAspect
    error_recovery: ReviewAspect
    provenance_clarity: ReviewAspect
    overall_usability: ReviewAspect


class ExactTextSpan(StrictModel):
    """Half-open locations and digest of an exact substring in the final answer."""

    codepoint_start: StrictNonNegativeInt
    codepoint_end: StrictNonNegativeInt
    utf8_byte_start: StrictNonNegativeInt
    utf8_byte_end: StrictNonNegativeInt
    sha256: Sha256


class ExtractedSelfReview(StrictModel):
    """Raw self-review evidence; this is not an acceptance or source-truth result."""

    schema_version: Literal[1] = 1
    candidate_sha: GitSha
    prompt_sha256: Sha256
    summary_sha256: Sha256
    trace_sha256: Sha256
    final_answer_sha256: Sha256
    review: SelfReview
    scores: AspectScores
    review_span: ExactTextSpan
    recommendations_text: str | None
    recommendations_span: ExactTextSpan | None
    recommendations_missing_reason: Literal["not_present"] | None

    @model_validator(mode="after")
    def redundant_scores_match_review(self) -> ExtractedSelfReview:
        derived = {aspect: getattr(self.review, aspect).score for aspect in ASPECTS}
        if self.scores.model_dump() != derived:
            raise ValueError("scores must match the detailed review")
        recommendations_present = self.recommendations_text is not None
        if recommendations_present != (self.recommendations_span is not None):
            raise ValueError("recommendation text and span availability must match")
        if recommendations_present == (self.recommendations_missing_reason is not None):
            raise ValueError("recommendation missing reason is inconsistent")
        return self


@dataclass(frozen=True)
class _JsonBlock:
    content_start: int
    content_end: int
    suffix_start: int
    closed: bool


@dataclass(frozen=True)
class _Candidate:
    start: int
    end: int
    suffix_start: int | None
    parsed: object | None
    failure: SelfReviewFailureReason | None


_OPENING_FENCE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>[^\r\n]*)$")
_JSON_WHITESPACE = " \t\r\n"


def _fail(reason: SelfReviewFailureReason) -> NoReturn:
    raise SelfReviewExtractionError(reason) from None


def _validated_run(run: NormalizedRun) -> NormalizedRun:
    if type(run) is not NormalizedRun:
        _fail("invalid_run")
    try:
        return NormalizedRun.model_validate(run.model_dump(mode="python"), strict=True)
    except (AttributeError, TypeError, ValueError, ValidationError):
        _fail("invalid_run")


def _without_line_ending(line: str) -> str:
    if line.endswith("\n"):
        line = line[:-1]
    if line.endswith("\r"):
        line = line[:-1]
    return line


def _is_closing_fence(line: str, marker: str) -> bool:
    body = _without_line_ending(line)
    indent = len(body) - len(body.lstrip(" "))
    if indent > 3:
        return False
    body = body[indent:]
    marker_character = marker[0]
    marker_length = len(body) - len(body.lstrip(marker_character))
    return marker_length >= len(marker) and not body[marker_length:].strip(" \t")


def _json_blocks(text: str) -> tuple[_JsonBlock, ...]:
    lines = text.splitlines(keepends=True)
    blocks: list[_JsonBlock] = []
    offset = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        opening = _OPENING_FENCE.fullmatch(_without_line_ending(line))
        if opening is None:
            offset += len(line)
            index += 1
            continue
        marker = opening.group("marker")
        info = opening.group("info")
        if marker[0] == "`" and "`" in info:
            offset += len(line)
            index += 1
            continue
        is_json = info.strip(" \t").casefold() == "json"
        if is_json and len(blocks) == MAX_JSON_CODEBLOCKS:
            _fail("too_many_json_codeblocks")
        content_start = offset + len(line)
        scan_offset = content_start
        index += 1
        while index < len(lines) and not _is_closing_fence(lines[index], marker):
            scan_offset += len(lines[index])
            index += 1
        if index == len(lines):
            if is_json:
                blocks.append(_JsonBlock(content_start, len(text), len(text), False))
            break
        closing_line = lines[index]
        if is_json:
            blocks.append(
                _JsonBlock(
                    content_start=content_start,
                    content_end=scan_offset,
                    suffix_start=scan_offset + len(closing_line),
                    closed=True,
                )
            )
        offset = scan_offset + len(closing_line)
        index += 1
    return tuple(blocks)


def _scan_string(text: str, start: int) -> int:
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return index + 1
        index += 1
    return len(text)


def _has_top_level_review_key(text: str) -> bool:
    index = len(text) - len(text.lstrip(_JSON_WHITESPACE))
    if index == len(text) or text[index] != "{":
        return False
    depth = 0
    while index < len(text):
        character = text[index]
        if character == '"':
            end = _scan_string(text, index)
            if depth == 1:
                after = end
                while after < len(text) and text[after] in _JSON_WHITESPACE:
                    after += 1
                if after < len(text) and text[after] == ":":
                    try:
                        if json.loads(text[index:end]) == "experience_review":
                            return True
                    except (UnicodeError, ValueError, TypeError):
                        pass
            index = end
            continue
        if character in "[{":
            depth += 1
        elif character in "]}":
            depth -= 1
        index += 1
    return False


def _json_depth_exceeded(text: str) -> bool:
    depth = 0
    index = 0
    while index < len(text):
        character = text[index]
        if character == '"':
            index = _scan_string(text, index)
            continue
        if character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                return True
        elif character in "]}":
            depth -= 1
        index += 1
    return False


def _json_strings_are_utf8(value: object) -> bool:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError:
                return False
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            pending.extend(item)
            pending.extend(item.values())
    return True


def _candidate(
    text: str, start: int, end: int, suffix_start: int | None, *, closed: bool
) -> _Candidate | None:
    raw_region = text[start:end]
    left = len(raw_region) - len(raw_region.lstrip(_JSON_WHITESPACE))
    right = len(raw_region.rstrip(_JSON_WHITESPACE))
    raw = raw_region[left:right]
    start += left
    end = start + len(raw)
    has_review_key = _has_top_level_review_key(raw)
    if not has_review_key:
        if not raw or not closed:
            return None
        try:
            parsed = parse_json(raw.encode("utf-8"))
        except AdapterInputError:
            return None
        if not isinstance(parsed, dict) or "experience_review" not in parsed:
            return None
    if len(raw.encode("utf-8")) > MAX_CANDIDATE_BYTES:
        return _Candidate(start, end, suffix_start, None, "candidate_too_large")
    if _json_depth_exceeded(raw):
        return _Candidate(start, end, suffix_start, None, "candidate_too_deep")
    if not closed:
        return _Candidate(start, end, suffix_start, None, "malformed_candidate")
    try:
        parsed = parse_json(raw.encode("utf-8"))
    except AdapterInputError:
        return _Candidate(start, end, suffix_start, None, "malformed_candidate")
    if not _json_strings_are_utf8(parsed):
        return _Candidate(start, end, suffix_start, None, "invalid_unicode")
    return _Candidate(start, end, suffix_start, parsed, None)


def _find_candidates(text: str) -> tuple[_Candidate, ...]:
    blocks = _json_blocks(text)
    if blocks:
        return tuple(
            candidate
            for block in blocks
            if (
                candidate := _candidate(
                    text,
                    block.content_start,
                    block.content_end,
                    block.suffix_start,
                    closed=block.closed,
                )
            )
            is not None
        )
    start = len(text) - len(text.lstrip(_JSON_WHITESPACE))
    end = len(text.rstrip(_JSON_WHITESPACE))
    candidate = _candidate(text, start, end, None, closed=True)
    return (candidate,) if candidate is not None else ()


def _span(text: str, start: int, end: int) -> ExactTextSpan:
    raw = text[start:end].encode("utf-8")
    return ExactTextSpan(
        codepoint_start=start,
        codepoint_end=end,
        utf8_byte_start=len(text[:start].encode("utf-8")),
        utf8_byte_end=len(text[:end].encode("utf-8")),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _validated_review(candidate: _Candidate) -> SelfReview:
    if candidate.failure is not None:
        _fail(candidate.failure)
    if not isinstance(candidate.parsed, dict) or "experience_review" not in candidate.parsed:
        _fail("invalid_review")
    try:
        return SelfReview.model_validate(candidate.parsed["experience_review"], strict=True)
    except (TypeError, ValueError, ValidationError):
        _fail("invalid_review")


def extract_self_review(run: NormalizedRun) -> ExtractedSelfReview:
    """Extract exact review evidence without judging its correctness or coverage."""
    normalized = _validated_run(run)
    final = normalized.final_answer
    if final is None:
        _fail("final_answer_missing")
    try:
        final_raw = final.encode("utf-8")
    except UnicodeError:
        _fail("invalid_unicode")
    if len(final_raw) > MAX_FINAL_BYTES:
        _fail("final_answer_too_large")
    candidates = _find_candidates(final)
    if not candidates:
        _fail("review_not_found")
    if len(candidates) != 1:
        _fail("multiple_review_candidates")
    candidate = candidates[0]
    review = _validated_review(candidate)
    scores = AspectScores.model_validate(
        {aspect: getattr(review, aspect).score for aspect in ASPECTS}, strict=True
    )
    recommendations = (
        final[candidate.suffix_start :] if candidate.suffix_start is not None else None
    )
    if recommendations == "":
        recommendations = None
    if (
        recommendations is not None
        and len(recommendations.encode("utf-8")) > MAX_RECOMMENDATIONS_BYTES
    ):
        _fail("recommendations_too_large")
    return ExtractedSelfReview(
        candidate_sha=normalized.candidate_sha,
        prompt_sha256=normalized.prompt_sha256,
        summary_sha256=normalized.summary_sha256,
        trace_sha256=normalized.trace_sha256,
        final_answer_sha256=hashlib.sha256(final_raw).hexdigest(),
        review=review,
        scores=scores,
        review_span=_span(final, candidate.start, candidate.end),
        recommendations_text=recommendations,
        recommendations_span=(
            _span(final, candidate.suffix_start, len(final))
            if recommendations is not None and candidate.suffix_start is not None
            else None
        ),
        recommendations_missing_reason=None if recommendations is not None else "not_present",
    )


__all__ = [
    "ExactTextSpan",
    "ExtractedSelfReview",
    "ReviewAspect",
    "SelfReview",
    "SelfReviewExtractionError",
    "SelfReviewFailureReason",
    "extract_self_review",
]
