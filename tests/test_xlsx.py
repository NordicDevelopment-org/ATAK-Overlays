import io
import zipfile

from overlaybuilder.drivers._xlsx import read_xlsx
from overlaybuilder.drivers.file import _month_candidates


def _xlsx(rows, sheet="Operating"):
    strings = []

    def sidx(s):
        if s not in strings:
            strings.append(s)
        return strings.index(s)

    def cell(ref, v):
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"><v>{v}</v></c>'
        return f'<c r="{ref}" t="s"><v>{sidx(v)}</v></c>'
    body = []
    for r, row in enumerate(rows, 1):
        cells = "".join(cell(f"{chr(65 + i)}{r}", v) for i, v in enumerate(row) if v is not None)
        body.append(f'<row r="{r}">{cells}</row>')
    sheet_xml = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                 + "".join(body) + "</sheetData></worksheet>")
    sst = ('<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
    wb = ('<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          f'<sheets><sheet name="{sheet}" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        z.writestr("xl/sharedStrings.xml", sst)
    return buf.getvalue()


def test_read_xlsx_header_row_and_types():
    raw = _xlsx([["Title row", None], ["notes", None],
                 ["Plant Name", "Nameplate Capacity (MW)", "Latitude", "Longitude", "Status"],
                 ["Prairie Island", 1146.4, 44.622, -92.633, "OP"],
                 ["Empty", None, None, None, None]])
    rows = read_xlsx(raw, "Operating", header_row=3)
    assert len(rows) == 2
    assert rows[0]["Plant Name"] == "Prairie Island" and rows[0]["Nameplate Capacity (MW)"] == "1146.4"
    assert rows[1]["Plant Name"] == "Empty" and rows[1]["Latitude"] == ""
    assert read_xlsx(raw, None, header_row=3)[0]["Status"] == "OP"


def test_missing_sheet_errors():
    raw = _xlsx([["a"]])
    try:
        read_xlsx(raw, "Nope")
    except RuntimeError as e:
        assert "Nope" in str(e)
    else:
        raise AssertionError


def test_month_candidates():
    c = list(_month_candidates("https://x/{month}_generator{year}.xlsx", 2))
    assert len(c) == 3 and all("{" not in u for u in c)
    assert c[0] != c[1]
