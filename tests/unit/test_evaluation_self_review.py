"""Synthetic tests for deterministic consumer self-review extraction."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from scripts.evaluation_contracts import ASPECTS
from scripts.evaluation_self_review import (
    ExtractedSelfReview,
    SelfReviewExtractionError,
    extract_self_review,
)
from scripts.evaluation_traces import NormalizedRun

SHA = "a" * 64
COMMIT = "c" * 40


def _review(
    scores: tuple[int | None, ...] = (0, 10, 20, 30, 40, 50, 90, 100),
) -> dict[str, object]:
    return {
        "experience_review": {
            aspect: {
                "score": score,
                "evidence": f"Exact evidence for {aspect}.",
                "improvement": f"Exact improvement for {aspect}.",
            }
            for aspect, score in zip(ASPECTS, scores, strict=True)
        }
    }


def _run(final_answer: str | None, **overrides: object) -> NormalizedRun:
    values: dict[str, object] = {
        "consumer": "opus",
        "candidate_sha": COMMIT,
        "prompt_sha256": "1" * 64,
        "summary_sha256": "2" * 64,
        "trace_sha256": "3" * 64,
        "trace_bytes": 100,
        "requested_model": "opus",
        "expected_model": "claude-opus-5",
        "observed_model": "claude-opus-5",
        "requested_effort": None,
        "observed_effort": None,
        "effort_unavailable_reason": "not_configured_or_exposed",
        "model_rerouted": False,
        "transport_passed": True,
        "capture_complete": True,
        "lifecycle_complete": True,
        "hard_call_limit": 40,
        "deadline_seconds": 600.0,
        "trace_limit_bytes": 1_000_000,
        "duration_seconds": 12.0,
        "calls": (),
        "final_answer": final_answer,
        "measurements": {},
        "failures": (),
        "limitations": (),
    }
    values.update(overrides)
    return NormalizedRun.model_validate(values)


def test_extracts_fenced_review_with_exact_scores_text_hashes_and_spans() -> None:
    review = _review()
    review["experience_review"]["correctness_confidence"]["evidence"] = (  # type: ignore[index]
        "Exact β evidence for correctness_confidence."
    )
    raw_review = json.dumps(review, ensure_ascii=False, indent=2)
    prefix = "Narrative 🧬 before the review.\n```JSON\n"
    between = "\n```\n"
    recommendations = "Recommendations:\n1. Keep exact source context.\n"
    final = prefix + raw_review + between + recommendations

    result = extract_self_review(_run(final))

    assert result.candidate_sha == COMMIT
    assert result.prompt_sha256 == "1" * 64
    assert result.summary_sha256 == "2" * 64
    assert result.trace_sha256 == "3" * 64
    assert result.final_answer_sha256 == hashlib.sha256(final.encode()).hexdigest()
    assert result.scores.model_dump() == dict(
        zip(ASPECTS, (0, 10, 20, 30, 40, 50, 90, 100), strict=True)
    )
    assert result.review.correctness_confidence.evidence == (
        "Exact β evidence for correctness_confidence."
    )
    assert result.review.overall_usability.improvement == (
        "Exact improvement for overall_usability."
    )
    assert result.review_span.codepoint_start == len(prefix)
    assert result.review_span.codepoint_end == len(prefix + raw_review)
    assert result.review_span.utf8_byte_start == len(prefix.encode())
    assert result.review_span.utf8_byte_end == len((prefix + raw_review).encode())
    assert result.review_span.sha256 == hashlib.sha256(raw_review.encode()).hexdigest()
    assert result.recommendations_text == recommendations
    assert result.recommendations_span is not None
    assert result.recommendations_span.codepoint_start == len(prefix + raw_review + between)
    assert result.recommendations_span.codepoint_end == len(final)
    assert result.recommendations_span.utf8_byte_start == len(
        (prefix + raw_review + between).encode()
    )
    assert result.recommendations_span.utf8_byte_end == len(final.encode())
    assert (
        result.recommendations_span.sha256 == hashlib.sha256(recommendations.encode()).hexdigest()
    )
    assert result.recommendations_missing_reason is None


def _fence(value: object, *, marker: str = "```", language: str = "json") -> str:
    return f"{marker}{language}\n{json.dumps(value, ensure_ascii=False)}\n{marker}"


def _assert_reason(final: str | None, reason: str) -> SelfReviewExtractionError:
    with pytest.raises(SelfReviewExtractionError) as caught:
        extract_self_review(_run(final))
    assert caught.value.reason == reason
    assert str(caught.value) == reason
    assert caught.value.__cause__ is None
    return caught.value


def test_extracts_standalone_bare_review_and_preserves_all_null_as_unobserved() -> None:
    raw = json.dumps(_review((None,) * 8), ensure_ascii=False)
    final = f" \n{raw}\t"

    result = extract_self_review(_run(final))

    assert result.scores.model_dump() == dict.fromkeys(ASPECTS)
    assert result.review_span.codepoint_start == 2
    assert result.review_span.codepoint_end == 2 + len(raw)
    assert result.recommendations_text is None
    assert result.recommendations_span is None
    assert result.recommendations_missing_reason == "not_present"
    assert not ({"passed", "source_assertions_passed", "blind_view"} & result.model_fields_set)


def test_fenced_review_without_suffix_records_recommendations_as_absent() -> None:
    result = extract_self_review(_run(_fence(_review())))
    assert result.recommendations_text is None
    assert result.recommendations_span is None
    assert result.recommendations_missing_reason == "not_present"


def test_ignores_valid_unrelated_blocks_nested_keys_and_quoted_mentions() -> None:
    unrelated = {
        "note": "experience_review is only text",
        "nested": {"experience_review": _review()["experience_review"]},
    }
    review = _review()
    final = (
        "Narrative\n"
        + _fence(unrelated, marker="~~~~", language="JsOn")
        + "\nThen the actual report:\n"
        + _fence(review, marker="~~~~", language="jSoN")
        + "\nRecommendations remain raw."
    )

    result = extract_self_review(_run(final))

    assert result.scores.correctness_confidence == 0
    assert result.recommendations_text == "Recommendations remain raw."


@pytest.mark.parametrize(
    ("final", "reason"),
    [
        (None, "final_answer_missing"),
        ("No structured review here.", "review_not_found"),
        (_fence({"experience_review": {}, "extra": 1}), "invalid_review"),
        (_fence({"nested": _review()}), "review_not_found"),
        ('```json\n{"experience_review": {}\n```', "malformed_candidate"),
    ],
)
def test_fails_conservatively_for_missing_or_malformed_review(
    final: str | None, reason: str
) -> None:
    _assert_reason(final, reason)


def test_rejects_multiple_reports_even_when_equal_or_one_is_malformed() -> None:
    block = _fence(_review())
    _assert_reason(f"{block}\n{block}", "multiple_review_candidates")
    malformed = '```json\n{"experience_review": {\n```'
    _assert_reason(f"{malformed}\n{block}", "multiple_review_candidates")


@pytest.mark.parametrize(
    "raw",
    [
        '{"experience_review":{},"experience_review":{}}',
        '{"experience_review":{"correctness_confidence":'
        '{"score":90,"score":100,"evidence":"e","improvement":"i"}}}',
        '{"experience_review":{"correctness_confidence":'
        '{"score":NaN,"evidence":"e","improvement":"i"}}}',
        '{"experience_review":{"correctness_confidence":'
        '{"score":Infinity,"evidence":"e","improvement":"i"}}}',
    ],
)
def test_rejects_duplicate_keys_and_nonfinite_json_with_fixed_reason(raw: str) -> None:
    _assert_reason(f"```json\n{raw}\n```", "malformed_candidate")


@pytest.mark.parametrize("score", [-1, 101, True, 90.5, "90"])
def test_rejects_scores_outside_raw_strict_integer_contract(score: object) -> None:
    review = _review()
    review["experience_review"]["correctness_confidence"]["score"] = score  # type: ignore[index]
    _assert_reason(_fence(review), "invalid_review")


def test_rejects_missing_or_extra_aspects_and_entry_fields() -> None:
    missing_aspect = _review()
    del missing_aspect["experience_review"]["speed"]  # type: ignore[index]
    _assert_reason(_fence(missing_aspect), "invalid_review")

    extra_aspect = _review()
    extra_aspect["experience_review"]["other"] = {  # type: ignore[index]
        "score": 90,
        "evidence": "e",
        "improvement": "i",
    }
    _assert_reason(_fence(extra_aspect), "invalid_review")

    missing_field = _review()
    del missing_field["experience_review"]["speed"]["evidence"]  # type: ignore[index]
    _assert_reason(_fence(missing_field), "invalid_review")

    extra_field = _review()
    extra_field["experience_review"]["speed"]["explanation"] = "no"  # type: ignore[index]
    _assert_reason(_fence(extra_field), "invalid_review")


@pytest.mark.parametrize("field", ["evidence", "improvement"])
@pytest.mark.parametrize("text", ["", " \t\n", "x" * 16_385])
def test_rejects_empty_whitespace_or_oversized_aspect_prose(field: str, text: str) -> None:
    review = _review()
    review["experience_review"]["speed"][field] = text  # type: ignore[index]
    _assert_reason(_fence(review), "invalid_review")


def test_requires_matching_anchored_fence_and_accepts_longer_matching_close() -> None:
    raw = json.dumps(_review())
    _assert_reason(f"````json\n{raw}\n```", "malformed_candidate")

    result = extract_self_review(_run(f"  ````JSON\n{raw}\n  `````\nrecommend"))
    assert result.recommendations_text == "recommend"


def test_rejects_invalid_unicode_and_recommendation_byte_overflow_safely() -> None:
    hostile = "never echo this \ud800 payload"
    error = _assert_reason(hostile, "invalid_unicode")
    assert "never echo" not in repr(error)
    assert "payload" not in repr(error)

    final = _fence(_review()) + "\n" + ("é" * 32_769)
    _assert_reason(final, "recommendations_too_large")

    escaped_surrogate = json.dumps(_review()).replace(
        "Exact evidence for speed.", r"escaped \ud800 surrogate"
    )
    _assert_reason(f"```json\n{escaped_surrogate}\n```", "invalid_unicode")


def test_enforces_final_candidate_depth_and_block_count_bounds() -> None:
    _assert_reason("x" * (1024 * 1024 + 1), "final_answer_too_large")

    oversized = {
        "experience_review": _review()["experience_review"],
        "padding": "x" * (256 * 1024),
    }
    _assert_reason(_fence(oversized), "candidate_too_large")

    nested: object = "leaf"
    for _ in range(70):
        nested = [nested]
    deep = {
        "experience_review": _review()["experience_review"],
        "nested": nested,
    }
    _assert_reason(_fence(deep), "candidate_too_deep")

    blocks = "\n".join(_fence({"block": index}) for index in range(33))
    _assert_reason(blocks, "too_many_json_codeblocks")


def test_revalidates_model_construct_forged_normalized_run() -> None:
    valid = _run(_fence(_review()))
    forged = NormalizedRun.model_construct(**{**valid.__dict__, "candidate_sha": "hostile"})
    with pytest.raises(SelfReviewExtractionError) as caught:
        extract_self_review(forged)
    assert caught.value.reason == "invalid_run"
    assert "hostile" not in repr(caught.value)


def test_extracted_contract_rejects_scores_that_contradict_detailed_review() -> None:
    result = extract_self_review(_run(_fence(_review())))
    contradictory = result.model_dump()
    contradictory["scores"]["speed"] = 100  # type: ignore[index]
    with pytest.raises(ValidationError):
        ExtractedSelfReview.model_validate(contradictory)


def test_extraction_does_not_mutate_attempt_or_infer_transport_acceptance() -> None:
    run = _run(
        _fence(_review()),
        transport_passed=False,
        capture_complete=False,
        lifecycle_complete=False,
        failures=("synthetic_transport_failure",),
    )
    before = run.model_dump()

    result = extract_self_review(run)

    assert result.scores.overall_usability == 100
    assert run.model_dump() == before
    assert not (
        {"passed", "transport_passed", "source_assertions_passed"} & result.model_fields_set
    )


def test_invalid_review_error_never_echoes_hostile_review_prose() -> None:
    review = _review()
    review["experience_review"]["speed"]["evidence"] = "DO_NOT_ECHO"  # type: ignore[index]
    review["experience_review"]["speed"]["score"] = "wrong"  # type: ignore[index]

    error = _assert_reason(_fence(review), "invalid_review")

    assert "DO_NOT_ECHO" not in repr(error)
    assert "wrong" not in repr(error)
