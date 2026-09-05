"""Profiled OOXML workbook reader preserving cell-level source semantics."""

from __future__ import annotations

import io
import re
import stat
import zipfile
from dataclasses import dataclass
from typing import Any, BinaryIO
from xml.parsers import expat

from openpyxl import load_workbook  # type: ignore[import-untyped]
from openpyxl.utils import get_column_letter, range_boundaries  # type: ignore[import-untyped]

from clinpgx_link.exceptions import DataValidationError


@dataclass(frozen=True)
class SpreadsheetCell:
    value: Any
    formula: str | None
    cached_value: Any
    present: bool
    merged_range: str | None
    merge_anchor: str | None


@dataclass(frozen=True)
class SpreadsheetRow:
    ordinal: int
    fields: dict[str, SpreadsheetCell]


@dataclass(frozen=True)
class SpreadsheetSheet:
    name: str
    headers: tuple[str, ...]
    rows: tuple[SpreadsheetRow, ...]
    hidden: bool
    merged_ranges: tuple[str, ...]


@dataclass(frozen=True)
class SpreadsheetDocument:
    sheets: tuple[SpreadsheetSheet, ...]


@dataclass(frozen=True)
class SpreadsheetLimits:
    """Hard package and worksheet bounds applied before openpyxl materializes cells."""

    max_package_bytes: int = 128 * 1024 * 1024
    max_parts: int = 2048
    max_expanded_bytes: int = 256 * 1024 * 1024
    max_part_bytes: int = 64 * 1024 * 1024
    max_xml_bytes: int = 32 * 1024 * 1024
    max_sheets: int = 256
    max_rows: int = 100_000
    max_columns: int = 512
    max_cells: int = 250_000
    max_merged_ranges: int = 10_000
    max_merged_cells: int = 250_000

    @classmethod
    def for_tests(cls, **overrides: int) -> SpreadsheetLimits:
        values = {
            "max_package_bytes": 1024 * 1024,
            "max_parts": 100,
            "max_expanded_bytes": 4 * 1024 * 1024,
            "max_part_bytes": 2 * 1024 * 1024,
            "max_xml_bytes": 1024 * 1024,
            "max_sheets": 10,
            "max_rows": 1000,
            "max_columns": 100,
            "max_cells": 10_000,
            "max_merged_ranges": 100,
            "max_merged_cells": 10_000,
        }
        unknown = set(overrides) - set(values)
        if unknown:
            raise ValueError("Unknown spreadsheet limit")
        values.update(overrides)
        return cls(**values)


def _validate_limits(limits: SpreadsheetLimits) -> None:
    values = vars(limits).values()
    if any(type(value) is not int or value <= 0 for value in values):
        raise DataValidationError(
            "Spreadsheet limits are internally inconsistent", subtype="resource_limit"
        )
    if limits.max_xml_bytes > limits.max_part_bytes:
        raise DataValidationError(
            "Spreadsheet XML limit exceeds its part limit", subtype="resource_limit"
        )


def _workbook_bytes(stream: BinaryIO, limits: SpreadsheetLimits) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := stream.read(min(1024 * 1024, limits.max_package_bytes + 1 - total)):
        total += len(chunk)
        if total > limits.max_package_bytes:
            raise DataValidationError(
                "Spreadsheet exceeds its package byte limit", subtype="resource_limit"
            )
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        raise DataValidationError("Spreadsheet does not have an OOXML ZIP signature")
    return raw


def _safe_part_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    parts = name.split("/")
    mode = info.external_attr >> 16
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or "\x00" in name
        or any(part in {"", ".", ".."} for part in parts)
        or info.flag_bits & 0x1
        or stat.S_ISLNK(mode)
    ):
        raise DataValidationError("Spreadsheet contains an unsafe OOXML part")
    return name


def _read_part(package: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    try:
        return package.read(info)
    except (KeyError, NotImplementedError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise DataValidationError("Spreadsheet OOXML part cannot be decoded") from exc


def _range_bounds(reference: str, *, context: str) -> tuple[int, int, int, int]:
    if not isinstance(reference, str) or not re.fullmatch(
        r"[A-Z]{1,3}[1-9][0-9]*(?::[A-Z]{1,3}[1-9][0-9]*)?", reference
    ):
        raise DataValidationError(f"Spreadsheet {context} is invalid")
    try:
        bounds = range_boundaries(reference)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"Spreadsheet {context} is invalid") from exc
    if any(value is None for value in bounds):
        raise DataValidationError(f"Spreadsheet {context} is invalid")
    return tuple(int(value) for value in bounds)  # type: ignore[return-value]


_XML_DECLARATION = re.compile(r"^\s*<\?xml\b([^?]*)\?>", re.IGNORECASE)
_XML_ENCODING = re.compile(r"(?:^|\s)encoding\s*=\s*(['\"])(.*?)\1", re.IGNORECASE)


def _canonical_xml_bytes(raw: bytes) -> bytes:
    """Admit only UTF-8 XML so byte-level safety scans cannot be encoding-obscured."""
    if b"\x00" in raw:
        raise DataValidationError("Spreadsheet XML uses an unsupported encoding")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DataValidationError("Spreadsheet XML uses an unsupported encoding") from exc
    declaration = _XML_DECLARATION.match(text)
    if declaration is not None:
        encoding = _XML_ENCODING.search(declaration.group(1))
        if encoding is not None and encoding.group(2).casefold() not in {"utf-8", "utf8"}:
            raise DataValidationError("Spreadsheet XML uses an unsupported encoding")
    canonical = text.encode("utf-8")
    upper = canonical.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise DataValidationError("Spreadsheet XML contains a forbidden declaration")
    return canonical


def _validate_worksheet_xml(raw: bytes, limits: SpreadsheetLimits) -> None:
    max_row = 0
    max_column = 0
    cell_count = 0
    merge_count = 0
    merged_cells = 0

    def start_element(name: str, attributes: dict[str, str]) -> None:
        nonlocal cell_count, max_column, max_row, merge_count, merged_cells
        local_name = name.rsplit("}", maxsplit=1)[-1]
        if local_name == "dimension":
            minimum_column, minimum_row, maximum_column, maximum_row = _range_bounds(
                attributes.get("ref", ""), context="dimension"
            )
            if minimum_column < 1 or minimum_row < 1:
                raise DataValidationError("Spreadsheet dimension is invalid")
            if (
                maximum_row > limits.max_rows
                or maximum_column > limits.max_columns
                or maximum_row * maximum_column > limits.max_cells
            ):
                raise DataValidationError(
                    "Spreadsheet dimension exceeds its cell bounds", subtype="resource_limit"
                )
            max_row = max(max_row, maximum_row)
            max_column = max(max_column, maximum_column)
        elif local_name == "row":
            row = attributes.get("r", "")
            if not row.isascii() or not row.isdecimal() or int(row) > limits.max_rows:
                raise DataValidationError(
                    "Spreadsheet row dimension exceeds its bounds", subtype="resource_limit"
                )
            max_row = max(max_row, int(row))
        elif local_name == "c":
            minimum_column, minimum_row, maximum_column, maximum_row = _range_bounds(
                attributes.get("r", ""), context="cell coordinate"
            )
            cell_count += 1
            max_row = max(max_row, maximum_row)
            max_column = max(max_column, maximum_column)
            if (
                cell_count > limits.max_cells
                or maximum_row > limits.max_rows
                or maximum_column > limits.max_columns
            ):
                raise DataValidationError(
                    "Spreadsheet cell count or coordinate exceeds its bounds",
                    subtype="resource_limit",
                )
        elif local_name == "mergeCell":
            minimum_column, minimum_row, maximum_column, maximum_row = _range_bounds(
                attributes.get("ref", ""), context="merge range"
            )
            merge_count += 1
            merged_cells += (maximum_row - minimum_row + 1) * (
                maximum_column - minimum_column + 1
            )
            if (
                merge_count > limits.max_merged_ranges
                or merged_cells > limits.max_merged_cells
                or maximum_row > limits.max_rows
                or maximum_column > limits.max_columns
            ):
                raise DataValidationError(
                    "Spreadsheet merge ranges exceed their bounds", subtype="resource_limit"
                )
            max_row = max(max_row, maximum_row)
            max_column = max(max_column, maximum_column)

    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = start_element
    try:
        parser.Parse(raw, True)
    except DataValidationError:
        raise
    except expat.ExpatError as exc:
        raise DataValidationError("Spreadsheet worksheet XML is invalid") from exc
    if max_row * max_column > limits.max_cells:
        raise DataValidationError(
            "Spreadsheet dimension exceeds its cell bounds", subtype="resource_limit"
        )


_WORKBOOK_CONTENT_TYPES = frozenset(
    {
        "application/vnd.ms-excel.addin.macroEnabled.main+xml",
        "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml",
    }
)
_WORKSHEET_RELATIONSHIP_TYPES = frozenset(
    {
        "http://purl.oclc.org/ooxml/officeDocument/relationships/worksheet",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
    }
)


def _validate_content_types(raw: bytes) -> None:
    workbook_parts: list[str] = []

    def start_element(name: str, attributes: dict[str, str]) -> None:
        local_name = name.rsplit("}", maxsplit=1)[-1]
        content_type = attributes.get("ContentType", "")
        if content_type not in _WORKBOOK_CONTENT_TYPES:
            return
        if local_name == "Default":
            raise DataValidationError("Spreadsheet has an ambiguous default workbook part")
        if local_name == "Override":
            workbook_parts.append(attributes.get("PartName", ""))

    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = start_element
    try:
        parser.Parse(raw, True)
    except DataValidationError:
        raise
    except expat.ExpatError as exc:
        raise DataValidationError("Spreadsheet content types are invalid") from exc
    if workbook_parts != ["/xl/workbook.xml"]:
        raise DataValidationError("Spreadsheet workbook part is noncanonical or ambiguous")


def _workbook_sheet_ids(raw: bytes, limits: SpreadsheetLimits) -> tuple[str, ...]:
    identifiers: list[str] = []

    def start_element(name: str, attributes: dict[str, str]) -> None:
        if name.rsplit("}", maxsplit=1)[-1] != "sheet":
            return
        relationship_ids = [
            value for key, value in attributes.items() if key.endswith("}id") and value
        ]
        if len(relationship_ids) != 1:
            raise DataValidationError("Spreadsheet sheet has an invalid relationship identity")
        identifiers.append(relationship_ids[0])
        if len(identifiers) > limits.max_sheets:
            raise DataValidationError(
                "Spreadsheet exceeds its worksheet-count limit", subtype="resource_limit"
            )

    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = start_element
    try:
        parser.Parse(raw, True)
    except DataValidationError:
        raise
    except expat.ExpatError as exc:
        raise DataValidationError("Spreadsheet workbook XML is invalid") from exc
    if not identifiers or len(identifiers) != len(set(identifiers)):
        raise DataValidationError("Spreadsheet sheet relationship identities are missing or duplicate")
    return tuple(identifiers)


def _worksheet_relationship_targets(raw: bytes, sheet_ids: tuple[str, ...]) -> tuple[str, ...]:
    relationships: dict[str, tuple[str, str, str]] = {}

    def start_element(name: str, attributes: dict[str, str]) -> None:
        if name.rsplit("}", maxsplit=1)[-1] != "Relationship":
            return
        identifier = attributes.get("Id", "")
        if not identifier or identifier in relationships:
            raise DataValidationError("Spreadsheet relationship identity is missing or duplicate")
        relationships[identifier] = (
            attributes.get("Type", ""),
            attributes.get("Target", ""),
            attributes.get("TargetMode", "Internal"),
        )

    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = start_element
    try:
        parser.Parse(raw, True)
    except DataValidationError:
        raise
    except expat.ExpatError as exc:
        raise DataValidationError("Spreadsheet workbook relationships are invalid") from exc

    targets: list[str] = []
    for identifier in sheet_ids:
        relationship = relationships.get(identifier)
        if relationship is None:
            raise DataValidationError("Spreadsheet sheet relationship target is missing")
        relationship_type, target, target_mode = relationship
        if relationship_type not in _WORKSHEET_RELATIONSHIP_TYPES:
            raise DataValidationError("Spreadsheet sheet relationship type is unsupported")
        if target_mode != "Internal":
            raise DataValidationError("Spreadsheet worksheet relationship must be internal")
        relative = re.fullmatch(r"worksheets/sheet[1-9][0-9]*\.xml", target)
        absolute = re.fullmatch(r"/xl/worksheets/sheet[1-9][0-9]*\.xml", target)
        if relative is None and absolute is None:
            raise DataValidationError("Spreadsheet worksheet relationship target is noncanonical")
        targets.append(f"xl/{target}" if relative is not None else target.removeprefix("/"))
    if len(targets) != len(set(targets)):
        raise DataValidationError("Spreadsheet contains duplicate worksheet relationships")
    return tuple(targets)


def _validate_parts(package: zipfile.ZipFile, limits: SpreadsheetLimits) -> None:
    infos = package.infolist()
    if len(infos) > limits.max_parts:
        raise DataValidationError(
            "Spreadsheet exceeds its OOXML part-count limit", subtype="resource_limit"
        )
    seen: set[str] = set()
    part_by_name: dict[str, zipfile.ZipInfo] = {}
    total_expanded = 0
    content_types_body: bytes | None = None
    workbook_body: bytes | None = None
    relationship_body: bytes | None = None
    for info in infos:
        name = _safe_part_name(info)
        if name in seen:
            raise DataValidationError("Spreadsheet contains duplicate OOXML parts")
        seen.add(name)
        part_by_name[name] = info
        total_expanded += info.file_size
        if info.file_size > limits.max_part_bytes:
            raise DataValidationError(
                "Spreadsheet OOXML part exceeds its expanded limit", subtype="resource_limit"
            )
        if total_expanded > limits.max_expanded_bytes:
            raise DataValidationError(
                "Spreadsheet OOXML package exceeds its expanded byte limit",
                subtype="resource_limit",
            )
        if not info.is_dir() and name.lower().endswith((".xml", ".rels")):
            if info.file_size > limits.max_xml_bytes:
                raise DataValidationError(
                    "Spreadsheet XML part exceeds its byte limit", subtype="resource_limit"
                )
            body = _canonical_xml_bytes(_read_part(package, info))
            if name == "[Content_Types].xml":
                content_types_body = body
            elif name == "xl/workbook.xml":
                workbook_body = body
            elif name == "xl/_rels/workbook.xml.rels":
                relationship_body = body
    required = {"[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
    if (
        not required.issubset(seen)
        or content_types_body is None
        or workbook_body is None
        or relationship_body is None
    ):
        raise DataValidationError("Spreadsheet is missing required OOXML parts")
    _validate_content_types(content_types_body)
    sheet_ids = _workbook_sheet_ids(workbook_body, limits)
    worksheet_targets = _worksheet_relationship_targets(relationship_body, sheet_ids)
    for target in worksheet_targets:
        target_info = part_by_name.get(target)
        if target_info is None or target_info.is_dir():
            raise DataValidationError("Spreadsheet worksheet relationship target is missing")
        if target_info.file_size > limits.max_xml_bytes:
            raise DataValidationError(
                "Spreadsheet XML part exceeds its byte limit", subtype="resource_limit"
            )
        _validate_worksheet_xml(_canonical_xml_bytes(_read_part(package, target_info)), limits)


def _validate_package(raw: bytes, limits: SpreadsheetLimits) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as package:
            _validate_parts(package, limits)
    except zipfile.BadZipFile as exc:
        raise DataValidationError("Spreadsheet OOXML package is invalid") from exc


def _merged_cells(worksheet: Any) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for merged in worksheet.merged_cells.ranges:
        range_name = str(merged)
        anchor = f"{get_column_letter(merged.min_col)}{merged.min_row}"
        for row in range(merged.min_row, merged.max_row + 1):
            for column in range(merged.min_col, merged.max_col + 1):
                result[f"{get_column_letter(column)}{row}"] = (range_name, anchor)
    return result


def parse_spreadsheet(
    stream: BinaryIO, *, limits: SpreadsheetLimits | None = None
) -> SpreadsheetDocument:
    """Read a profiled first-row-header workbook without flattening formulas/merges."""
    configured = limits or SpreadsheetLimits()
    _validate_limits(configured)
    raw = _workbook_bytes(stream, configured)
    _validate_package(raw, configured)
    try:
        formulas = load_workbook(io.BytesIO(raw), data_only=False, read_only=False)
        cached = load_workbook(io.BytesIO(raw), data_only=True, read_only=False)
    except (KeyError, OSError, ValueError, zipfile.BadZipFile) as exc:
        raise DataValidationError("Spreadsheet OOXML parts cannot be decoded") from exc
    sheets: list[SpreadsheetSheet] = []
    try:
        for worksheet in formulas.worksheets:
            cached_sheet = cached[worksheet.title]
            max_column = worksheet.max_column
            if max_column < 1 or worksheet.max_row < 1:
                raise DataValidationError("Spreadsheet sheet is empty")
            headers: list[str] = []
            for column in range(1, max_column + 1):
                value = worksheet.cell(row=1, column=column).value
                if not isinstance(value, str) or not value:
                    raise DataValidationError("Spreadsheet first row must contain text headers")
                headers.append(value)
            if len(headers) != len(set(headers)):
                raise DataValidationError("Spreadsheet has duplicate headers")
            existing = getattr(worksheet, "_cells", {})
            present = {
                f"{get_column_letter(column)}{row}"
                for row, column in existing
                if existing[(row, column)].__class__.__name__ != "MergedCell"
            }
            merges = _merged_cells(worksheet)
            rows: list[SpreadsheetRow] = []
            for row_number in range(2, worksheet.max_row + 1):
                fields: dict[str, SpreadsheetCell] = {}
                for column, header in enumerate(headers, start=1):
                    coordinate = f"{get_column_letter(column)}{row_number}"
                    cell = worksheet[coordinate]
                    formula = (
                        cell.value
                        if cell.data_type == "f" and isinstance(cell.value, str)
                        else None
                    )
                    cached_value = cached_sheet[coordinate].value if formula is not None else None
                    merge = merges.get(coordinate)
                    fields[header] = SpreadsheetCell(
                        value=cached_value if formula is not None else cell.value,
                        formula=formula,
                        cached_value=cached_value,
                        present=coordinate in present,
                        merged_range=merge[0] if merge else None,
                        merge_anchor=merge[1] if merge else None,
                    )
                rows.append(SpreadsheetRow(ordinal=row_number - 1, fields=fields))
            sheets.append(
                SpreadsheetSheet(
                    name=worksheet.title,
                    headers=tuple(headers),
                    rows=tuple(rows),
                    hidden=worksheet.sheet_state != "visible",
                    merged_ranges=tuple(str(item) for item in worksheet.merged_cells.ranges),
                )
            )
    finally:
        formulas.close()
        cached.close()
    return SpreadsheetDocument(sheets=tuple(sheets))


__all__ = [
    "SpreadsheetCell",
    "SpreadsheetDocument",
    "SpreadsheetLimits",
    "SpreadsheetRow",
    "SpreadsheetSheet",
    "parse_spreadsheet",
]
