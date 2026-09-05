"""Bounded, source-neutral diagnostics for unsuccessful exact searches."""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from clinpgx_link.exceptions import InvalidInputError
from clinpgx_link.models import SourceResponse

CANONICAL_FILTER_PRIORITY = (
    "id",
    "gene",
    "chemical",
    "variant",
    "name",
    "source",
    "annotation_id",
)
CANONICAL_FILTERS = frozenset(CANONICAL_FILTER_PRIORITY)
_NON_EQUIVALENCE = (
    "Observed examples are not equivalence claims. They are distinct stored values seen "
    "after removing only the named exact filter from the same snapshot search scope."
)


@dataclass(frozen=True)
class DiagnosticLimits:
    """Injected limits make interruption behavior deterministic in tests."""

    step_budget: int = 50_000
    timeout_seconds: float = 0.05
    quantum: int = 100

    def __post_init__(self) -> None:
        if self.step_budget < 1 or self.timeout_seconds <= 0 or self.quantum < 1:
            raise ValueError("Diagnostic limits must be positive")


@dataclass
class _Budget:
    step_budget: int | None
    deadline: float | None
    steps: int = 0
    interrupted: bool = False
    outer_interrupted: bool = False


class SQLiteProgressHooks:
    """Explicit owner of one connection's composable SQLite progress callback.

    SQLite exposes no getter for an unknown prior callback. Callers must therefore give
    this owner exclusive progress-handler ownership for the managed connection.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: Callable[[], float] = time.monotonic,
        quantum: int = 100,
    ) -> None:
        if quantum < 1:
            raise ValueError("Progress quantum must be positive")
        self._connection = connection
        self._clock = clock
        self._quantum = quantum
        self._budgets: list[_Budget] = []

    def _progress(self) -> int:
        now = self._clock()
        outer_interrupted = False
        should_interrupt = False
        for budget in self._budgets:
            if outer_interrupted:
                budget.outer_interrupted = True
            if budget.step_budget is not None:
                budget.steps += self._quantum
            if (budget.step_budget is not None and budget.steps >= budget.step_budget) or (
                budget.deadline is not None and now >= budget.deadline
            ):
                budget.interrupted = True
                outer_interrupted = True
                should_interrupt = True
        return int(should_interrupt)

    @contextmanager
    def budget(
        self,
        *,
        step_budget: int | None = None,
        timeout_seconds: float | None = None,
        deadline: float | None = None,
    ) -> Iterator[_Budget]:
        if step_budget is not None and step_budget < 1:
            raise ValueError("Step budget must be positive")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("Timeout must be positive")
        if step_budget is None and timeout_seconds is None and deadline is None:
            raise ValueError("At least one progress limit is required")
        local_deadline = self._clock() + timeout_seconds if timeout_seconds is not None else None
        if deadline is not None:
            local_deadline = deadline if local_deadline is None else min(deadline, local_deadline)
        state = _Budget(step_budget=step_budget, deadline=local_deadline)
        self._budgets.append(state)
        if len(self._budgets) == 1:
            self._connection.set_progress_handler(self._progress, self._quantum)
        try:
            yield state
        finally:
            state_index = next(
                index for index, active in enumerate(self._budgets) if active is state
            )
            del self._budgets[state_index]
            if not self._budgets:
                self._connection.set_progress_handler(None, 0)

    @staticmethod
    def was_local_interruption(budget: _Budget) -> bool:
        """Return true only when this budget, and no enclosing budget, expired."""
        return budget.interrupted and not budget.outer_interrupted


class RepositoryDiagnosticsSupport:
    """Repository mixin that owns progress hooks and diagnostic orchestration."""

    _connection: sqlite3.Connection
    _connection_lock: threading.RLock
    _diagnostic_limits: DiagnosticLimits
    _progress_hooks: SQLiteProgressHooks

    def _initialize_search_diagnostics(self, limits: DiagnosticLimits | None = None) -> None:
        self._diagnostic_limits = limits or DiagnosticLimits()
        self._progress_hooks = SQLiteProgressHooks(
            self._connection, quantum=self._diagnostic_limits.quantum
        )

    @contextmanager
    def execution_budget(
        self, *, step_budget: int | None = None, deadline: float | None = None
    ) -> Iterator[None]:
        """Compose an outer repository limit with any nested diagnostic limit."""
        with self._connection_lock:
            with self._progress_hooks.budget(step_budget=step_budget, deadline=deadline):
                yield

    @staticmethod
    def _fts_query(query: str) -> str | None:
        if "*" in query:
            raise InvalidInputError(
                "ASCII star is not supported in literal token queries",
                field="query",
                hint="Omit query and use an exact gene or name filter.",
                subtype="wildcard_query_unsupported",
            )
        tokens = re.findall(r"\w+", query, re.UNICODE)
        if not tokens:
            return None
        return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)

    def _with_search_diagnostics(
        self,
        response: SourceResponse,
        *,
        dataset_id: str,
        member: str | None,
        query: str | None,
        canonical_filters: Mapping[str, str],
        source_filters: Mapping[str, str],
        match: str,
    ) -> SourceResponse:
        if response.details["total_count"] != 0 or match != "exact" or not canonical_filters:
            return response
        response.details["search_diagnostics"] = (
            unavailable_exact_diagnostics(canonical_filters)
            if source_filters
            else exact_zero_diagnostics(
                self._connection,
                self._progress_hooks,
                self._diagnostic_limits,
                dataset_id=dataset_id,
                member=member,
                filters=canonical_filters,
                query_expression=self._fts_query(query) if query is not None else None,
            )
        )
        return response


def validate_filter_values(filters: Mapping[str, str]) -> None:
    for key, value in filters.items():
        if not isinstance(value, str) or not value:
            raise InvalidInputError("Filter values must be nonempty strings", field=key)


def validate_canonical_filters(filters: Mapping[str, str]) -> None:
    validate_filter_values(filters)
    for key in filters:
        if key not in CANONICAL_FILTERS:
            raise InvalidInputError("Unknown canonical dataset filter", field=key)


def _ordered_filters(filters: Mapping[str, str]) -> list[str]:
    return [key for key in CANONICAL_FILTER_PRIORITY if key in filters]


def diagnostic_statement(
    *,
    dataset_id: str,
    member: str | None,
    target_filter: str,
    retained_filters: Mapping[str, str],
    query_expression: str | None,
) -> tuple[str, list[Any]]:
    """Build an index-seeking stream of relaxed records and target values."""
    if target_filter not in CANONICAL_FILTERS or any(
        key not in CANONICAL_FILTERS for key in retained_filters
    ):
        raise ValueError("Diagnostic filters must be canonical")
    ordered_retained = _ordered_filters(retained_filters)
    parameters: list[Any] = [target_filter]
    if ordered_retained:
        anchor = ordered_retained[0]
        sql = (
            "SELECT r.record_pk,candidate.value FROM membership AS anchor "
            "INDEXED BY membership_lookup CROSS JOIN record AS r "
            "LEFT JOIN membership AS candidate INDEXED BY sqlite_autoindex_membership_1 "
            "ON candidate.record_pk=r.record_pk AND candidate.kind=? "
            "AND candidate.match_mode='exact' WHERE anchor.kind=? AND anchor.value=? "
            "AND anchor.match_mode='exact' AND r.record_pk=anchor.record_pk "
            "AND r.dataset_id=?"
        )
        parameters.extend([anchor, retained_filters[anchor], dataset_id])
        remaining = ordered_retained[1:]
    else:
        sql = (
            "SELECT r.record_pk,candidate.value FROM record AS r "
            "INDEXED BY record_dataset_member_ordinal "
            "LEFT JOIN membership AS candidate INDEXED BY sqlite_autoindex_membership_1 "
            "ON candidate.record_pk=r.record_pk AND candidate.kind=? "
            "AND candidate.match_mode='exact' WHERE r.dataset_id=?"
        )
        parameters.append(dataset_id)
        remaining = []
    if member is not None:
        sql += " AND r.member=?"
        parameters.append(member)
    for key in remaining:
        sql += (
            " AND EXISTS (SELECT 1 FROM membership AS retained "
            "WHERE retained.record_pk=r.record_pk AND retained.kind=? "
            "AND retained.value=? AND retained.match_mode='exact')"
        )
        parameters.extend([key, retained_filters[key]])
    if query_expression is not None:
        sql += " AND r.record_pk IN (SELECT rowid FROM record_fts WHERE record_fts MATCH ?)"
        parameters.append(query_expression)
    return sql, parameters


def _retain_binary_top3(examples: list[str], value: str) -> None:
    if value in examples:
        return
    examples.append(value)
    examples.sort(key=lambda item: item.encode("utf-8"))
    del examples[3:]


def exact_zero_diagnostics(
    connection: sqlite3.Connection,
    hooks: SQLiteProgressHooks,
    limits: DiagnosticLimits,
    *,
    dataset_id: str,
    member: str | None,
    filters: Mapping[str, str],
    query_expression: str | None,
) -> dict[str, Any]:
    """Diagnose at most two exact filters without publishing interrupted work."""
    results: list[dict[str, Any]] = []
    for target in _ordered_filters(filters)[:2]:
        retained = {key: value for key, value in filters.items() if key != target}
        sql, parameters = diagnostic_statement(
            dataset_id=dataset_id,
            member=member,
            target_filter=target,
            retained_filters=retained,
            query_expression=query_expression,
        )
        count = 0
        last_record_pk: int | None = None
        examples: list[str] = []
        local_budget: _Budget | None = None
        try:
            with hooks.budget(
                step_budget=limits.step_budget,
                timeout_seconds=limits.timeout_seconds,
            ) as local_budget:
                for row in connection.execute(sql, parameters):
                    record_pk = int(row[0])
                    if record_pk != last_record_pk:
                        count += 1
                        last_record_pk = record_pk
                    if row[1] is not None:
                        _retain_binary_top3(examples, str(row[1]))
        except sqlite3.OperationalError as exc:
            if (
                "interrupted" not in str(exc).lower()
                or local_budget is None
                or not hooks.was_local_interruption(local_budget)
            ):
                raise
            results.append({"filter": target, "status": "diagnostics_unavailable"})
        else:
            results.append(
                {
                    "filter": target,
                    "status": "available",
                    "count_without_filter": count,
                    "examples": examples,
                }
            )
    return {
        "status": (
            "available"
            if all(item["status"] == "available" for item in results)
            else "diagnostics_unavailable"
        ),
        "limitation": _NON_EQUIVALENCE,
        "filters": results,
    }


def unavailable_exact_diagnostics(filters: Mapping[str, str]) -> dict[str, Any]:
    """Disclose that an unindexed retained scope cannot be diagnosed honestly."""
    return {
        "status": "diagnostics_unavailable",
        "limitation": _NON_EQUIVALENCE,
        "filters": [
            {"filter": key, "status": "diagnostics_unavailable"}
            for key in _ordered_filters(filters)[:2]
        ],
    }


__all__ = [
    "CANONICAL_FILTERS",
    "CANONICAL_FILTER_PRIORITY",
    "DiagnosticLimits",
    "RepositoryDiagnosticsSupport",
    "SQLiteProgressHooks",
    "diagnostic_statement",
    "exact_zero_diagnostics",
    "unavailable_exact_diagnostics",
    "validate_canonical_filters",
    "validate_filter_values",
]
