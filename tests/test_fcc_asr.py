import io
import zipfile

from overlaybuilder.drivers.fcc_asr import parse_tables, LAYOUT


def _row(n, **kw):
    r = [""] * n
    for i, v in kw.items():
        r[int(i)] = v
    return "|".join(r)


def _zip():
    L = LAYOUT
    ra = "\n".join([
        _row(45, **{str(L["usi"]): "100", str(L["reg"]): "1234567", str(L["ra_status"]): "C",
                    str(L["ra_structure_type"]): "GTOWER", str(L["ra_height_agl"]): "120.5",
                    str(L["ra_ground_elev"]): "300", str(L["ra_city"]): "Lindstrom", str(L["ra_state"]): "MN",
                    str(L["ra_date_constructed"]): "01/02/2003"}),
        _row(45, **{str(L["usi"]): "200", str(L["reg"]): "7654321", str(L["ra_status"]): "C",
                    str(L["ra_structure_type"]): "MTOWER", str(L["ra_state"]): "WI"}),
        _row(45, **{str(L["usi"]): "300", str(L["reg"]): "1111111", str(L["ra_status"]): "T",
                    str(L["ra_structure_type"]): "TOWER", str(L["ra_state"]): "MN"}),
    ])
    co = "\n".join([
        _row(18, **{str(L["usi"]): "100", str(L["co_lat_d"]): "45", str(L["co_lat_m"]): "30", str(L["co_lat_s"]): "0",
                    str(L["co_lat_h"]): "N", str(L["co_lon_d"]): "92", str(L["co_lon_m"]): "54", str(L["co_lon_s"]): "0",
                    str(L["co_lon_h"]): "W", str(L["co_array_pos"]): "1", str(L["co_array_total"]): "1"}),
        _row(18, **{str(L["usi"]): "200", str(L["co_lat_d"]): "44", str(L["co_lat_m"]): "0", str(L["co_lat_s"]): "0",
                    str(L["co_lat_h"]): "N", str(L["co_lon_d"]): "90", str(L["co_lon_m"]): "0", str(L["co_lon_s"]): "0",
                    str(L["co_lon_h"]): "W"}),
        _row(18, **{str(L["usi"]): "300", str(L["co_lat_d"]): "44", str(L["co_lat_m"]): "0", str(L["co_lat_s"]): "0",
                    str(L["co_lat_h"]): "N", str(L["co_lon_d"]): "90", str(L["co_lon_m"]): "0", str(L["co_lon_s"]): "0",
                    str(L["co_lon_h"]): "W"}),
    ])
    en = _row(10, **{str(L["usi"]): "100", str(L["en_contact_type"]): "O", str(L["en_name"]): "Tower Co LLC"})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("RA.dat", ra)
        z.writestr("CO.dat", co)
        z.writestr("EN.dat", en)
    return buf.getvalue()


def test_parse_joins_and_filters():
    feats = parse_tables(_zip(), {}, {"MN"}, {"C"})
    assert len(feats) == 1                       # WI row filtered by state, T row by status
    f = feats[0]
    lon, lat = f.geometry["coordinates"]
    assert abs(lat - 45.5) < 1e-9 and abs(lon + 92.9) < 1e-9
    p = f.properties
    assert p["structure_type"] == "Guyed tower" and p["status"] == "Constructed"
    assert p["height_agl_m"] == 120.5 and p["owner"] == "Tower Co LLC" and p["registration_number"] == "1234567"
    assert "array_position" not in p


def test_parse_all_states():
    feats = parse_tables(_zip(), {}, set(), {"C"})
    assert {f.properties["state"] for f in feats} == {"MN", "WI"}
