"""Parses a grades+classes bulk-import file (CSV or XLSX) into a list of
plain row dicts with canonical, lowercase column keys.

Framework-free by design (no FastAPI/asyncpg imports) -- mirrors this
codebase's convention that service-layer-adjacent modules stay plain, so
this can be unit tested without spinning up the app or a database.
"""

import csv
import io

try:
    import openpyxl
except ImportError:  # pragma: no cover - exercised only if the dependency is missing
    openpyxl = None

# Generous headroom for any real school (a school with 2000 class sections
# would be enormous) -- the first bound of this kind in the codebase;
# save_academic_structure, the only other bulk-write path, has none.
MAX_IMPORT_ROWS = 2000
MAX_IMPORT_BYTES = 5 * 1024 * 1024

# Canonical column names, matched case-insensitively and whitespace-trimmed
# against whatever headers the uploaded file actually has.
IMPORT_COLUMNS = (
    "grade_name",
    "grade_ordinal",
    "grade_isced_level",
    "grade_age_min",
    "grade_age_max",
    "grade_active",
    "class_name",
    "class_capacity",
    "class_active",
    "head_teacher_email",
)


def detect_file_kind(filename: str, content_type: str | None) -> str:
    """Returns "csv" or "xlsx" from the filename's extension, cross-checked
    loosely against content_type where present. Raises ValueError for
    anything else -- mapped to an HTTP 400 at the router."""
    name = (filename or "").strip().lower()
    if name.endswith(".csv"):
        return "csv"
    if name.endswith(".xlsx"):
        return "xlsx"

    ctype = (content_type or "").lower()
    if "csv" in ctype:
        return "csv"
    if "spreadsheetml" in ctype or "excel" in ctype:
        return "xlsx"

    raise ValueError(
        f"Unsupported file type for {filename!r} -- only .csv and .xlsx are accepted"
    )


def _normalize_header(raw: str) -> str:
    return (raw or "").strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_row(raw_row: dict) -> dict:
    normalized = {_normalize_header(k): v for k, v in raw_row.items()}
    return {col: normalized.get(col) for col in IMPORT_COLUMNS}


def _parse_csv(raw_bytes: bytes) -> list[dict]:
    # utf-8-sig swallows a leading BOM -- matches the BOM the existing
    # student-roster CSV export already writes in StudentPlacementView.vue,
    # so a round-tripped file (export, edit, re-import) doesn't break on it.
    text = raw_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [_normalize_row(row) for row in reader]


def _parse_xlsx(raw_bytes: bytes) -> list[dict]:
    if openpyxl is None:
        raise ValueError("XLSX support is unavailable on this server (openpyxl not installed)")
    workbook = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            return []
        headers = [_normalize_header(str(h) if h is not None else "") for h in header_row]
        rows = []
        for values in rows_iter:
            if all(v is None or str(v).strip() == "" for v in values):
                continue  # a fully blank row (common trailing artifact in exported sheets)
            # strict=False deliberately: a ragged spreadsheet row (fewer
            # trailing cells than the header row) is common and not an error
            # -- missing columns just come through as None, same as a blank
            # CSV cell would.
            raw_row = dict(zip(headers, values, strict=False))
            rows.append({col: raw_row.get(col) for col in IMPORT_COLUMNS})
        return rows
    finally:
        workbook.close()


def parse_import_file(raw_bytes: bytes, kind: str) -> list[dict]:
    """Returns a list of row dicts with canonical string-ish values (CSV
    values are always str; XLSX cell values may be int/float/bool/str --
    callers that need a specific type parse it themselves, matching how the
    repository's _parse_import_int/_parse_import_bool already stringify
    before parsing). Raises ValueError on anything that should surface as a
    400: wrong kind, oversized file, too many rows, or (for XLSX) a missing
    parser dependency."""
    if len(raw_bytes) > MAX_IMPORT_BYTES:
        raise ValueError(
            f"File is too large ({len(raw_bytes)} bytes) -- the limit is {MAX_IMPORT_BYTES} bytes"
        )

    if kind == "csv":
        rows = _parse_csv(raw_bytes)
    elif kind == "xlsx":
        rows = _parse_xlsx(raw_bytes)
    else:
        raise ValueError(f"Unsupported file kind {kind!r}")

    if len(rows) > MAX_IMPORT_ROWS:
        raise ValueError(f"File has {len(rows)} rows -- the limit is {MAX_IMPORT_ROWS}")

    return rows
