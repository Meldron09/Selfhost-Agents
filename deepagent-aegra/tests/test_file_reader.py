"""`agent.file_reader`'s pure extraction functions.

No `ToolRuntime`/graph involved here, mirroring tests/test_output_writer.py's
split: these are the seams the five `@tool`-wrapped functions delegate to
before touching the file store. The tool-level, error-shaped behavior (no
raised exception, an explicit tool-level error) is exercised end to end in
tests/test_file_reader_graph.py.

docs/adr/0005-testing-strategy-carryover-from-agent-runtime.md, point 2:
xlsx/docx/pptx/txt fixtures are produced by round-tripping through
`output-writer`'s own serializers rather than maintaining static files; only
pdf keeps the static checked-in fixture, since nothing in this project writes
pdf.
"""
from __future__ import annotations

import io
from pathlib import Path

import xlwt

from agent.file_reader import (
    _extract_docx,
    _extract_pdf,
    _extract_pptx,
    _extract_txt,
    _extract_xlsx,
)
from agent.output_writer import _serialize_docx, _serialize_pptx, _serialize_xlsx

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_PDF = (REPO_ROOT / "tests" / "fixtures" / "sample.pdf").read_bytes()
PDF_FIXTURE_MARKER = "PYPDF_FIXTURE_MARKER_98214"


def _legacy_xls(sheet_name: str, headers: list[str], rows: list[list[object]]) -> bytes:
    """A real legacy (OLE2/BIFF) .xls workbook, built with `xlwt` -- the
    write-side counterpart `xlrd` needs, since nothing else in this project
    (`output-writer` included) writes that format. Test-only, mirroring how
    `_serialize_xlsx` stands in for a fixture for the modern xlsx format.
    """
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet(sheet_name)
    for col, header in enumerate(headers):
        sheet.write(0, col, header)
    for row_index, row in enumerate(rows, start=1):
        for col, value in enumerate(row):
            sheet.write(row_index, col, value)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
"""The text embedded in tests/fixtures/sample.pdf's one page."""


# --- read_pdf ---------------------------------------------------------------


def test_extract_pdf_returns_the_embedded_text():
    content, error = _extract_pdf(SAMPLE_PDF)

    assert error is None
    assert PDF_FIXTURE_MARKER in content


def test_extract_pdf_of_corrupted_bytes_returns_an_explicit_error_not_an_exception():
    content, error = _extract_pdf(b"not actually a pdf")

    assert content is None
    assert error is not None


# --- read_xlsx ----------------------------------------------------------------


def test_extract_xlsx_round_trips_through_output_writers_serializer():
    sheets_in = [
        {"sheet_name": "Revenue", "headers": ["Region", "Q2"], "rows": [["EMEA", 100], ["AMER", 200]]},
        {"sheet_name": "Headcount", "headers": ["Team", "Count"], "rows": [["Eng", 42]]},
    ]
    data = _serialize_xlsx(sheets_in)

    sheets_out, error = _extract_xlsx(data)

    assert error is None
    assert sheets_out == [
        {"sheet_name": "Revenue", "headers": ["Region", "Q2"], "rows": [["EMEA", 100], ["AMER", 200]]},
        {"sheet_name": "Headcount", "headers": ["Team", "Count"], "rows": [["Eng", 42]]},
    ]


def test_extract_xlsx_of_corrupted_bytes_returns_an_explicit_error_not_an_exception():
    sheets, error = _extract_xlsx(b"not actually a workbook")

    assert sheets is None
    assert error is not None


# --- read_xlsx's .xls fallback (issue #17: "read_xlsx (+.xls)") -------------


def test_extract_xlsx_reads_a_real_legacy_xls_workbook_via_the_xlrd_fallback():
    data = _legacy_xls("Revenue", ["Region", "Q2"], [["EMEA", 100], ["AMER", 200]])

    sheets, error = _extract_xlsx(data)

    assert error is None
    assert sheets == [
        {"sheet_name": "Revenue", "headers": ["Region", "Q2"], "rows": [["EMEA", 100.0], ["AMER", 200.0]]}
    ]


# --- read_docx ----------------------------------------------------------------


def test_extract_docx_round_trips_through_output_writers_serializer():
    data = _serialize_docx("Kickoff Memo\n\nScope: independent demo.")

    text, error = _extract_docx(data)

    assert error is None
    assert "Kickoff Memo" in text
    assert "Scope: independent demo." in text


def test_extract_docx_of_corrupted_bytes_returns_an_explicit_error_not_an_exception():
    text, error = _extract_docx(b"not actually a document")

    assert text is None
    assert error is not None


# --- read_pptx ----------------------------------------------------------------


def test_extract_pptx_round_trips_through_output_writers_serializer():
    slides_in = [
        {"title": "Intro", "bullets": ["Point one", "Point two"], "speaker_notes": "say hi"},
        {"title": "Wrap-up", "bullets": [], "speaker_notes": ""},
    ]
    data = _serialize_pptx(slides_in)

    slides_out, error = _extract_pptx(data)

    assert error is None
    assert slides_out == [
        {"slide_number": 1, "title": "Intro", "bullets": ["Point one", "Point two"], "speaker_notes": "say hi"},
        {"slide_number": 2, "title": "Wrap-up", "bullets": [], "speaker_notes": ""},
    ]


def test_extract_pptx_of_corrupted_bytes_returns_an_explicit_error_not_an_exception():
    slides, error = _extract_pptx(b"not actually a presentation")

    assert slides is None
    assert error is not None


# --- read_txt -------------------------------------------------------------


def test_extract_txt_decodes_utf8_bytes():
    text, error = _extract_txt("Reminder: demo runs Ollama-only.".encode("utf-8"))

    assert error is None
    assert text == "Reminder: demo runs Ollama-only."


def test_extract_txt_of_undecodable_bytes_returns_an_explicit_error_not_an_exception():
    text, error = _extract_txt(b"\xff\xfe\x00\x81not valid utf-8")

    assert text is None
    assert error is not None
