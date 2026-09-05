"""Shared strict, bounded canonical JSON primitives for inner release contracts."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    WithJsonSchema,
)
from pydantic.json_schema import JsonSchemaValue

from clinpgx_link.exceptions import DataValidationError

RFC3339_PATTERN = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
SEMVER_PATTERN = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
SCHEMA_METADATA: JsonSchemaValue = {"$schema": "https://json-schema.org/draft/2020-12/schema"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


def _schema_version(value: object) -> object:
    if type(value) is not int or value != 1:
        raise ValueError("schema_version must be integer 1")
    return value


def _timestamp(value: str) -> str:
    if re.fullmatch(RFC3339_PATTERN, value) is None:
        raise ValueError("explicit RFC3339 timezone required")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("explicit RFC3339 timezone required")
    return value


def _https(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("canonical HTTPS URL required") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.netloc != parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
        or parsed.hostname.lower() != parsed.hostname
        or value != parsed.geturl()
    ):
        raise ValueError("canonical HTTPS URL required")
    return value


SchemaVersion = Annotated[
    StrictInt,
    BeforeValidator(_schema_version),
    WithJsonSchema({"type": "integer", "const": 1}),
]
Timestamp = Annotated[
    str,
    Field(min_length=20, max_length=64, pattern=RFC3339_PATTERN),
    AfterValidator(_timestamp),
    WithJsonSchema(
        {
            "type": "string",
            "format": "date-time",
            "pattern": RFC3339_PATTERN,
            "minLength": 20,
            "maxLength": 64,
        }
    ),
]
HttpsUrl = Annotated[
    str,
    Field(min_length=9, max_length=2048),
    AfterValidator(_https),
    WithJsonSchema({"type": "string", "format": "uri", "minLength": 9, "maxLength": 2048}),
]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Sha256Identity = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
SemanticVersion = Annotated[str, Field(pattern=SEMVER_PATTERN, min_length=5, max_length=64)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
ShortText = Annotated[str, Field(min_length=1, max_length=512)]
LongText = Annotated[str, Field(min_length=1, max_length=8192)]


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_number(_value: str) -> None:
    raise ValueError("nonfinite JSON number")


def canonical_value_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise DataValidationError("Release contract cannot be canonicalized.") from exc


def canonical_bytes(model: BaseModel) -> bytes:
    """Serialize one admitted model without dropping explicit null evidence."""
    return canonical_value_bytes(model.model_dump(mode="json"))


def json_array(value: object) -> object:
    """Admit JSON arrays while retaining immutable tuples in frozen models."""
    if type(value) is not list:
        raise ValueError("JSON array required")
    return tuple(value)


def parse_canonical[ModelT: BaseModel](raw: bytes, *, maximum: int, model: type[ModelT]) -> ModelT:
    """Parse exact canonical bytes; this does not authenticate the enclosing bundle."""
    if not isinstance(raw, bytes) or len(raw) > maximum:
        raise DataValidationError(
            "Release contract exceeds its byte bound.", subtype="resource_limit"
        )
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=_invalid_number,
        )
        if canonical_value_bytes(value) != raw:
            raise ValueError("input is not canonical JSON")
        return model.model_validate(value)
    except (UnicodeError, ValueError, TypeError, RecursionError, ValidationError) as exc:
        raise DataValidationError("Release contract is invalid.") from exc


def schema_bytes(model: type[BaseModel]) -> bytes:
    return canonical_value_bytes(model.model_json_schema())


__all__ = [
    "SCHEMA_METADATA",
    "HttpsUrl",
    "LongText",
    "NonNegativeInt",
    "PositiveInt",
    "SchemaVersion",
    "SemanticVersion",
    "Sha256Hex",
    "Sha256Identity",
    "ShortText",
    "StrictModel",
    "Timestamp",
    "canonical_bytes",
    "canonical_value_bytes",
    "json_array",
    "parse_canonical",
    "schema_bytes",
]
