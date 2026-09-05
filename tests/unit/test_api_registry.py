"""The API registry is the only path from public arguments to upstream requests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[2]


@pytest.fixture
def registry():
    from clinpgx_link.api.registry import ApiRegistry

    return ApiRegistry()


def test_registry_lists_and_describes_every_vendored_operation(registry):
    source = json.loads((PROJECT_ROOT / "clinpgx_link/api/operations.json").read_text())

    listed = registry.list_operations()

    assert len(listed) == source["operation_count"] == 34
    assert {entry["operation"] for entry in listed} == {
        entry["operation"] for entry in source["operations"]
    }
    described = registry.describe("GET /data/gene/{id}")
    assert described["path"] == "/data/gene/{id}"
    described["path"] = "/mutated"
    assert registry.describe("GET /data/gene/{id}")["path"] == "/data/gene/{id}"


def test_bind_substitutes_validated_path_and_keeps_query_separate(registry):
    request = registry.bind(
        "GET /data/gene/{id}",
        {"id": "PA124"},
        {"view": "max"},
        representation="jsonld",
    )

    assert request.method == "GET"
    assert request.path == "/data/gene/PA124"
    assert request.params == {"view": "max"}
    assert request.form is None
    assert request.representation == "jsonld"


@pytest.mark.parametrize(
    "hostile",
    ["../secret", "PA124/extra", "https://evil.example", "%2fadmin", "PA124?view=max", ""],
)
def test_bind_rejects_path_escape_without_reflecting_it(registry, hostile):
    from clinpgx_link.exceptions import InvalidInputError

    with pytest.raises(InvalidInputError) as caught:
        registry.bind("GET /data/gene/{id}", {"id": hostile}, {})

    rendered = " ".join(
        filter(None, [str(caught.value), caught.value.field, caught.value.hint])
    )
    if hostile:
        assert hostile not in rendered
    assert caught.value.field == "path_parameters"


def test_bind_rejects_missing_extra_and_unknown_parameters(registry):
    from clinpgx_link.exceptions import InvalidInputError

    cases = [
        ("GET /data/gene/{id}", {}, {}, None),
        ("GET /data/gene/{id}", {"id": "PA124", "extra": "x"}, {}, None),
        ("GET /data/gene/{id}", {"id": "PA124"}, {"symobl": "CYP2C19"}, None),
        ("GET /data/gene/{id}", {"id": "PA124"}, {}, {"payload": "x"}),
    ]
    for operation, path, query, form in cases:
        with pytest.raises(InvalidInputError):
            registry.bind(operation, path, query, form_parameters=form)


def test_bind_rejects_unknown_operation_without_reflection(registry):
    from clinpgx_link.exceptions import InvalidInputError

    hostile = "GET https://evil.example/<script>"
    with pytest.raises(InvalidInputError) as caught:
        registry.bind(hostile, {}, {})

    assert hostile not in str(caught.value)
    assert caught.value.field == "operation"


@pytest.mark.parametrize(
    ("operation", "query"),
    [
        ("GET /data/gene", {}),
        ("GET /data/gene", {"view": "min"}),
        ("GET /data/summaryAnnotation", {}),
        ("GET /data/connection", {}),
        ("GET /infobutton", {}),
    ],
)
def test_bind_rejects_search_without_runtime_required_criteria(registry, operation, query):
    from clinpgx_link.exceptions import InvalidInputError

    with pytest.raises(InvalidInputError) as caught:
        registry.bind(operation, {}, query, representation="html" if "infobutton" in operation else "json")
    assert caught.value.subtype == "missing_criteria"


def test_bind_enforces_openapi_required_fields_types_and_enums(registry):
    from clinpgx_link.exceptions import InvalidInputError

    with pytest.raises(InvalidInputError):
        registry.bind("GET /report/variantFrequency", {}, {})
    with pytest.raises(InvalidInputError):
        registry.bind("GET /data/gene", {}, {"symbol": "CYP2C19", "view": "everything"})
    with pytest.raises(InvalidInputError):
        registry.bind("GET /data/gene", {}, {"symbol": ["CYP2C19"]})
    with pytest.raises(InvalidInputError):
        registry.bind("GET /data/literature/{id}", {"id": "not-a-number"}, {})


@pytest.mark.parametrize(
    ("operation", "representation"),
    [
        ("GET /data/gene/{id}", "html"),
        ("GET /report/stats", "jsonld"),
        ("GET /report/literatureId/{pmid}", "json"),
        ("GET /infobutton", "text"),
        ("POST /infobutton", "json"),
    ],
)
def test_bind_rejects_representation_not_verified_for_route(registry, operation, representation):
    from clinpgx_link.exceptions import InvalidInputError

    path = {"pmid": "123"} if "literatureId" in operation else {}
    query = {"mainSearchCriteria.v.c": "PA124"} if operation == "GET /infobutton" else {}
    form = {"mainSearchCriteria.v.c": "PA124"} if operation == "POST /infobutton" else None
    with pytest.raises(InvalidInputError):
        registry.bind(
            operation,
            path,
            query,
            form_parameters=form,
            representation=representation,
        )


def test_infobutton_post_accepts_only_verified_form_fields(registry):
    from clinpgx_link.exceptions import InvalidInputError

    request = registry.bind(
        "POST /infobutton",
        {},
        {},
        form_parameters={
            "mainSearchCriteria.v.c": "PA124",
            "mainSearchCriteria.v.cs": "https://www.clinpgx.org",
        },
        representation="html",
    )
    assert request.path == "/infobutton"
    assert request.params == {}
    assert request.form == {
        "mainSearchCriteria.v.c": "PA124",
        "mainSearchCriteria.v.cs": "https://www.clinpgx.org",
    }

    with pytest.raises(InvalidInputError):
        registry.bind(
            "POST /infobutton",
            {},
            {},
            form_parameters={"url": "https://evil.example"},
            representation="html",
        )
