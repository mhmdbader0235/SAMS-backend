"""Unit tests for app/domains/students/import_parser.py -- framework-free,
no database or app fixture needed."""

import io

import openpyxl
import pytest

from app.domains.students.import_parser import (
    MAX_IMPORT_BYTES,
    MAX_IMPORT_ROWS,
    detect_file_kind,
    parse_import_file,
)


class TestDetectFileKind:
    def test_csv_extension(self):
        assert detect_file_kind("grades.csv", None) == "csv"

    def test_xlsx_extension(self):
        assert detect_file_kind("grades.xlsx", None) == "xlsx"

    def test_falls_back_to_content_type_when_extension_is_ambiguous(self):
        assert detect_file_kind("upload", "text/csv") == "csv"
        assert detect_file_kind(
            "upload",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ) == "xlsx"

    def test_unsupported_type_raises(self):
        with pytest.raises(ValueError):
            detect_file_kind("grades.pdf", "application/pdf")


class TestParseCsv:
    def test_basic_row_parses_with_normalized_headers(self):
        content = (
            "grade_name,grade_ordinal,class_name,class_capacity,head_teacher_email\n"
            "Grade 7,7,Grade 7 - A,28,jane@school.com\n"
        )
        rows = parse_import_file(content.encode(), "csv")
        assert len(rows) == 1
        assert rows[0]["grade_name"] == "Grade 7"
        assert rows[0]["class_name"] == "Grade 7 - A"
        assert rows[0]["head_teacher_email"] == "jane@school.com"

    def test_header_case_and_spacing_variants_are_normalized(self):
        content = "Grade Name,Class Name\nGrade 8,Grade 8 - A\n"
        rows = parse_import_file(content.encode(), "csv")
        assert rows[0]["grade_name"] == "Grade 8"
        assert rows[0]["class_name"] == "Grade 8 - A"

    def test_bom_prefixed_file_parses_correctly(self):
        # Matches the BOM the existing student-roster CSV export already
        # writes, so a round-tripped file doesn't break on re-import.
        content = "grade_name,class_name\nGrade 9,Grade 9 - A\n"
        raw = "﻿".encode() + content.encode()
        rows = parse_import_file(raw, "csv")
        assert rows[0]["grade_name"] == "Grade 9"

    def test_grade_only_row_has_no_class_name(self):
        content = "grade_name,class_name\nGrade 10,\n"
        rows = parse_import_file(content.encode(), "csv")
        assert rows[0]["grade_name"] == "Grade 10"
        assert not rows[0]["class_name"]

    def test_row_cap_enforced(self):
        header = "grade_name,class_name\n"
        body = "".join(f"Grade {i},\n" for i in range(MAX_IMPORT_ROWS + 1))
        with pytest.raises(ValueError):
            parse_import_file((header + body).encode(), "csv")

    def test_byte_cap_enforced(self):
        oversized = b"x" * (MAX_IMPORT_BYTES + 1)
        with pytest.raises(ValueError):
            parse_import_file(oversized, "csv")


class TestParseXlsx:
    def _build_workbook_bytes(self, headers, rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(headers)
        for row in rows:
            ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_basic_row_parses(self):
        raw = self._build_workbook_bytes(
            ["grade_name", "grade_ordinal", "class_name", "class_capacity"],
            [["Grade 7", 7, "Grade 7 - A", 28]],
        )
        rows = parse_import_file(raw, "xlsx")
        assert len(rows) == 1
        assert rows[0]["grade_name"] == "Grade 7"
        # openpyxl preserves numeric cell types -- the repository layer's
        # int-parsing helper is what stringifies these, not the parser.
        assert rows[0]["grade_ordinal"] == 7
        assert rows[0]["class_capacity"] == 28

    def test_blank_trailing_row_is_skipped(self):
        raw = self._build_workbook_bytes(
            ["grade_name", "class_name"],
            [["Grade 8", "Grade 8 - A"], [None, None]],
        )
        rows = parse_import_file(raw, "xlsx")
        assert len(rows) == 1

    def test_empty_sheet_returns_no_rows(self):
        raw = self._build_workbook_bytes(["grade_name", "class_name"], [])
        rows = parse_import_file(raw, "xlsx")
        assert rows == []
