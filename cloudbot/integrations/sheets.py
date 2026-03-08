"""
Append autocoding results to a Google Sheet after each prompt is completed.

Sheet: https://docs.google.com/spreadsheets/d/1atmf7D_qXQzEUVmx82TFv9ztyzkPmG1FSYcFPIyF6rc/edit

Setup:
  1. Enable Google Sheets API (and Drive API) in Google Cloud Console.
  2. Create a service account, download JSON key.
  3. Share the spreadsheet with the service account email (Editor).
  4. Set credentials: gspread.service_account(filename='path/to/key.json')
     or put key at ~/.config/gspread/service_account.json.

Environment:
  GOOGLE_APPLICATION_CREDENTIALS  optional path to service account JSON
  AUTOCODING_SHEET_ID             optional; default is the Multi-Agent Autocoding sheet
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

# Default: Multi-Agent Autocoding Google Sheet
DEFAULT_SHEET_ID = "1atmf7D_qXQzEUVmx82TFv9ztyzkPmG1FSYcFPIyF6rc"


def _get_client():
    try:
        import gspread
    except ImportError:
        raise ImportError(
            "gspread is required for Google Sheets. Install with: pip install gspread"
        )
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_path and os.path.isfile(creds_path):
        return gspread.service_account(filename=creds_path)
    return gspread.service_account()


def _labels_to_string(final_labels: list[Any]) -> str:
    """Turn final_labels (list of dicts or strings) into one string for a cell."""
    if not final_labels:
        return ""
    out = []
    for item in final_labels:
        if isinstance(item, dict):
            out.append(item.get("label", str(item)))
        else:
            out.append(str(item))
    return ", ".join(out)


def append_result(
    prompt: str,
    final_labels: list[Any],
    *,
    sheet_id: str | None = None,
    worksheet_name: str | None = None,
    row_index: int | None = None,
    uncertain: list[Any] | None = None,
    credentials_path: str | None = None,
) -> bool:
    """
    Append one autocoding result as a new row in the Google Sheet.

    Columns (in order): Prompt | Final labels | Uncertain | Timestamp | Row index

    Args:
        prompt: The user prompt that was coded.
        final_labels: Adjudicator final_labels (list of dicts with 'label' or strings).
        sheet_id: Spreadsheet ID. Default from env AUTOCODING_SHEET_ID or DEFAULT_SHEET_ID.
        worksheet_name: Sheet tab name (e.g. "Sheet1"). Default: first worksheet.
        row_index: Optional 1-based index of the prompt in the batch (for CSV runs).
        uncertain: Optional list of uncertain items from adjudicator.
        credentials_path: Optional path to service account JSON (overrides env).

    Returns:
        True if append succeeded, False otherwise (e.g. missing credentials).
    """
    sheet_id = sheet_id or os.environ.get("AUTOCODING_SHEET_ID") or DEFAULT_SHEET_ID
    labels_str = _labels_to_string(final_labels)
    uncertain_str = ", ".join(str(u) for u in (uncertain or []))
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    row = [prompt, labels_str, uncertain_str, timestamp]
    if row_index is not None:
        row.append(row_index)

    try:
        if credentials_path:
            import gspread
            gc = gspread.service_account(filename=credentials_path)
        else:
            gc = _get_client()
        sh = gc.open_by_key(sheet_id)
        wks = sh.worksheet(worksheet_name) if worksheet_name else sh.sheet1
        wks.append_row(row, value_input_option="USER_ENTERED")
        return True
    except Exception:
        return False


def ensure_header_row(
    sheet_id: str | None = None,
    worksheet_name: str | None = None,
    headers: list[str] | None = None,
    credentials_path: str | None = None,
) -> bool:
    """
    If the first row is empty, write header row: Prompt, Final labels, Uncertain, Timestamp, Row index.
    """
    sheet_id = sheet_id or os.environ.get("AUTOCODING_SHEET_ID") or DEFAULT_SHEET_ID
    default_headers = ["Prompt", "Final labels", "Uncertain", "Timestamp", "Row index"]
    headers = headers or default_headers
    try:
        if credentials_path:
            import gspread
            gc = gspread.service_account(filename=credentials_path)
        else:
            gc = _get_client()
        sh = gc.open_by_key(sheet_id)
        wks = sh.worksheet(worksheet_name) if worksheet_name else sh.sheet1
        first = wks.row_values(1)
        if not any(c and str(c).strip() for c in first):
            wks.update("A1", [headers])
        return True
    except Exception:
        return False
