"""Minimal .xlsx reader (standard library only): one sheet -> list of dict rows.

Enough for government spreadsheets (EIA-860M generator inventory, NID
exports): shared strings, inline strings, numbers, dates as serials, a
configurable header row. No formulas evaluated (cached values are read).
"""
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _col_index(ref: str) -> int:
    m = re.match(r"([A-Z]+)", ref)
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _sheet_path(zf: zipfile.ZipFile, sheet: Optional[str]) -> str:
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {r.get("Id"): r.get("Target") for r in rels.findall("pr:Relationship", NS)}
    sheets = wb.findall("m:sheets/m:sheet", NS)
    if not sheets:
        raise RuntimeError("xlsx has no sheets")
    chosen = None
    if sheet is None:
        chosen = sheets[0]
    else:
        for s in sheets:
            if s.get("name", "").strip().lower() == sheet.strip().lower():
                chosen = s
                break
        if chosen is None:
            raise RuntimeError(f"sheet '{sheet}' not found; sheets: {[s.get('name') for s in sheets]}")
    target = rid_to_target[chosen.get(f"{{{NS['r']}}}id")]
    target = target.lstrip("/")
    return target if target.startswith("xl/") else "xl/" + target


def read_xlsx(raw: bytes, sheet: Optional[str] = None, header_row: int = 1) -> List[Dict[str, str]]:
    """header_row is 1-based. Returns rows below the header as dicts (strings)."""
    zf = zipfile.ZipFile(io.BytesIO(raw))
    shared: List[str] = []
    if "xl/sharedStrings.xml" in zf.namelist():
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    path = _sheet_path(zf, sheet)
    root = ET.fromstring(zf.read(path))
    rows: List[Dict[int, str]] = []
    for row in root.iter(f"{{{NS['m']}}}row"):
        cells: Dict[int, str] = {}
        for c in row.findall("m:c", NS):
            ref = c.get("r", "")
            idx = _col_index(ref) if ref else len(cells)
            t = c.get("t")
            v = c.find("m:v", NS)
            if t == "s" and v is not None:
                val = shared[int(v.text)] if v.text and v.text.isdigit() and int(v.text) < len(shared) else ""
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
            else:
                val = (v.text or "") if v is not None else ""
            cells[idx] = val.strip()
        rows.append(cells)
    if len(rows) < header_row:
        return []
    header = rows[header_row - 1]
    cols = {i: (name or f"col{i}") for i, name in header.items() if str(name).strip()}
    out: List[Dict[str, str]] = []
    for cells in rows[header_row:]:
        if not any(cells.values()):
            continue
        out.append({name: cells.get(i, "") for i, name in cols.items()})
    return out
