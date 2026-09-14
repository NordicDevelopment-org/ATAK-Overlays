MN_PACK = "MN_Counties_2024.kmz"

"""Tests for statepacks/build_county_pack.py - the stdlib-only ATAK county builder.

No network: every fetch is monkeypatched. What is being proved here is that the
builder never invents a value and never mangles a shape.
"""
import importlib.util
import os
import sys
import zipfile
import xml.dom.minidom as minidom

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SP = os.path.join(ROOT, "statepacks")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SP, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bcp = _load("build_county_pack")


# --------------------------------------------------------------------------
# Geometry: the bug that silently deletes parts of a county
# --------------------------------------------------------------------------
def test_rings_of_keeps_holes_and_every_polygon():
    outer = [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
    hole = [[0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4], [0.2, 0.2]]
    exclave = [[5, 5], [6, 5], [6, 6], [5, 6], [5, 5]]

    # a polygon with a hole keeps BOTH rings
    assert bcp.rings_of({"type": "Polygon", "coordinates": [outer, hole]}) == [outer, hole]

    # a multipolygon keeps every part - this is the Northwest Angle case
    multi = {"type": "MultiPolygon", "coordinates": [[outer, hole], [exclave]]}
    assert bcp.rings_of(multi) == [outer, hole, exclave]

    assert bcp.rings_of(None) == []
    assert bcp.rings_of({"type": "Point", "coordinates": [0, 0]}) == []


def test_multipolygon_county_renders_every_part():
    """A county with a detached exclave must draw both, not just the first."""
    outer = [[-93.0, 45.0], [-92.0, 45.0], [-92.0, 46.0], [-93.0, 46.0], [-93.0, 45.0]]
    exclave = [[-95.0, 49.0], [-94.9, 49.0], [-94.9, 49.3], [-95.0, 49.3], [-95.0, 49.0]]
    meta = _meta()
    pm = bcp.county_placemark({}, {"type": "MultiPolygon",
                                   "coordinates": [[outer], [exclave]]}, meta)
    assert "<MultiGeometry>" in pm
    assert pm.count("<LineString>") == 2
    assert "-94.9,49.3,0" in pm            # the exclave really is in there


# --------------------------------------------------------------------------
# Provenance: every value carries its year, absence is stated
# --------------------------------------------------------------------------
def test_sourced_renders_value_with_source_and_vintage():
    s = bcp.Sourced(58241, "ACS 5-year", "2023")
    assert s.render(lambda v: f"{v:,}") == "58,241  [ACS 5-year 2023]"
    assert bool(s) is True


def test_sourced_absence_is_explicit_never_zero():
    for empty in (bcp.Sourced(), bcp.Sourced(None, "ACS 5-year", "2023"), bcp.Sourced("")):
        assert bool(empty) is False
        assert empty.render() == "not in dataset"
        assert "0" != empty.render()


def _meta(**over):
    m = {
        "name": "Chisago County", "geoid_str": "27025",
        "geoid": bcp.Sourced("27025", "TIGER", "2024"), "state_abbr": "MN",
        "tiger_vintage": "2024", "built": "2026-09-14",
        "boundary_source": "US Census TIGERweb (vintage 2024)",
        "boundary_url": "https://tigerweb.example/County/MapServer/1",
        "population": bcp.Sourced(58241, "ACS 5-year", "2023"),
        "housing_units": bcp.Sourced(23110, "ACS 5-year", "2023"),
        "land_area": bcp.Sourced("414.2 sq mi", "TIGER ALAND", "2024"),
        "water_area": bcp.Sourced("28.5 sq mi", "TIGER AWATER", "2024"),
        "seat": bcp.Sourced(), "le_agency": bcp.Sourced(), "le_phone": bcp.Sourced(),
    }
    m.update(over)
    return m


def test_popup_stamps_every_value_and_admits_what_is_missing():
    pm = bcp.county_placemark({}, {"type": "Polygon", "coordinates": [
        [[-93, 45], [-92, 45], [-92, 46], [-93, 45]]]}, _meta())
    assert "58,241  [ACS 5-year 2023]" in pm
    assert "414.2 sq mi  [TIGER ALAND 2024]" in pm
    # seat and LE were not supplied, so they must say so rather than look blank
    assert "County seat:</b> not in dataset" in pm
    assert "Sheriff / primary LE:</b> not in dataset" in pm
    assert "LE non-emergency:</b> not in dataset" in pm
    # and the boundary provenance rides in the placemark itself
    assert "tigerweb.example" in pm


# --------------------------------------------------------------------------
# End to end, with the network stubbed out
# --------------------------------------------------------------------------
RING = [[-93.2, 45.3], [-92.6, 45.3], [-92.6, 45.8], [-93.2, 45.8], [-93.2, 45.3]]


def _fake_counties(n=3):
    out = []
    for i in range(n):
        out.append({
            "properties": {"NAME": f"County{i} County", "BASENAME": f"County{i}",
                           "GEOID": f"270{i:02d}",
                           "AREALAND": 1_072_000_000 + i, "AREAWATER": 73_800_000},
            "geometry": {"type": "Polygon", "coordinates": [RING]},
        })
    return out


@pytest.fixture
def stubbed(monkeypatch):
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print:
                        (_fake_counties(), "https://tigerweb.example/County/MapServer/1", "2024"))
    monkeypatch.setattr(bcp, "fetch_acs", lambda sfp, year=2023, log=print: {
        "000": {"population": bcp.Sourced(15900, "ACS 5-year", str(year)),
                "housing_units": bcp.Sourced(11000, "ACS 5-year", str(year))},
        "001": {"population": bcp.Sourced(372000, "ACS 5-year", str(year)),
                "housing_units": bcp.Sourced(140000, "ACS 5-year", str(year))},
    })
    return bcp


def _doc(path):
    return zipfile.ZipFile(path).read("doc.kml").decode()


def test_build_state_writes_one_pack_with_every_county(stubbed, tmp_path):
    r = bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    assert r["counties"] == 3 and r["with_population"] == 2
    path = tmp_path / MN_PACK
    assert path.exists() and r["path"] == str(path)

    kml = _doc(path)
    minidom.parseString(kml)                       # valid XML
    assert kml.count("<Placemark>") == 3
    for i in range(3):
        assert f"<name>County{i} County</name>" in kml
        assert "County County" not in kml

    # document-level provenance, including which endpoint actually answered
    assert "tigerweb.example" in kml
    assert "ACS 5-year 2023" in kml
    assert "public domain (US Census Bureau)" in kml
    assert "2026-09-14" in kml


def test_land_area_comes_from_aland_not_a_projected_shape_area(stubbed, tmp_path):
    """1,072,000,000 m2 / 2,589,988.110336 = 413.9 sq mi.

    Reading a projected Shape__Area instead would be off by roughly the square
    of the local Mercator scale factor - about 2x at Minnesota's latitude.
    """
    bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    kml = _doc(tmp_path / MN_PACK)
    assert "413.9 sq mi  [TIGER ALAND 2024]" in kml
    assert "28.5 sq mi  [TIGER AWATER 2024]" in kml


def test_missing_acs_still_builds_and_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print:
                        (_fake_counties(1), "https://tigerweb.example/x/1", "2024"))
    monkeypatch.setattr(bcp, "fetch_acs", lambda sfp, year=2023, log=print: {})
    r = bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    assert r["counties"] == 1 and r["with_population"] == 0 and r["acs_year"] is None
    kml = _doc(tmp_path / MN_PACK)
    assert "Population:</b> not in dataset" in kml      # never a fabricated number
    assert "not retrieved" in kml                       # and the Document says why


def test_csv_enrichment_carries_its_own_source_and_vintage(stubbed, tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "county_seats.csv").write_text(
        "geoid,seat,source,vintage\n27000,Center City,Wikidata,2026-09-14\n")
    (data / "le_contacts.csv").write_text(
        "geoid,agency,phone,source,vintage\n"
        "27000,County0 Sheriff's Office,651-555-0100,county website,2026\n")
    monkeypatch.setattr(bcp, "DATA_DIR", str(data))

    bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    kml = _doc(tmp_path / MN_PACK)
    assert "Center City  [Wikidata 2026-09-14]" in kml
    assert "651-555-0100  [county website 2026]" in kml
    # the county with no CSV row still refuses to guess
    assert "not in dataset" in kml


def test_per_county_also_writes_individual_files(stubbed, tmp_path):
    bcp.build_state("MN", str(tmp_path), per_county=True, log=lambda *a: None,
                    today="2026-09-14")
    names = {p.name for p in tmp_path.glob("*.kmz")}
    assert MN_PACK in names
    assert {"MN_County0_County.kmz", "MN_County1_County.kmz"} <= names


def test_every_state_is_addressable_and_fips_round_trips():
    assert len(bcp.STATE_FIPS) == 52                  # 50 states + DC + PR
    assert bcp.STATE_FIPS["MN"] == "27" and bcp.FIPS_STATE["27"] == "MN"
    assert all(len(v) == 2 and v.isdigit() for v in bcp.STATE_FIPS.values())


def test_unknown_state_is_rejected_not_guessed(capsys):
    assert bcp.main(["--state", "ZZ"]) == 1
    assert "unknown state" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Trust the server's data, not its filtering
# --------------------------------------------------------------------------
def test_county_geoid_identifies_counties_and_refuses_to_invent_one():
    assert bcp.county_geoid({"GEOID": "27025"}) == "27025"
    assert bcp.county_geoid({"geoid": "06001"}) == "06001"
    assert bcp.county_geoid({"STATE": "27", "COUNTY": "025"}) == "27025"
    assert bcp.county_geoid({"STATEFP": 6, "COUNTYFP": 1}) == "06001"

    # a STATE with no COUNTY must NOT become "27000" - that is a FIPS code no
    # source returned, and it used to be stamped [TIGER 2024] in the popup
    assert bcp.county_geoid({"STATE": "27"}) is None
    # a 2-digit state GEOID is a state polygon, not a county
    assert bcp.county_geoid({"GEOID": "27"}) is None
    assert bcp.county_geoid({"NAME": "Nowhere"}) is None
    assert bcp.county_geoid(None) is None


def test_counties_from_other_states_are_dropped_not_shipped(monkeypatch):
    """A `where` clause the server ignores would otherwise put California
    counties inside MN_Counties.kmz, silently."""
    everything = [
        {"properties": {"NAME": f"C{i}", "GEOID": f"{st}{i:03d}"}, "geometry": None}
        for st in ("27", "06", "48") for i in range(2)
    ]
    monkeypatch.setattr(bcp, "get_json", lambda url, params=None, **kw:
                        {"features": everything})
    monkeypatch.setattr(bcp, "service_vintage", lambda url, log=print: "2024")
    msgs = []
    kept, used, vintage = bcp.fetch_counties("27", "http://e/0", [], log=msgs.append)

    assert len(kept) == 2
    assert all(f["properties"]["GEOID"].startswith("27") for f in kept)
    assert any("not counties of state 27" in m and "not honoured" in m for m in msgs)


def test_a_page_with_no_matching_state_is_a_failure_not_an_empty_pack(monkeypatch):
    monkeypatch.setattr(bcp, "get_json", lambda url, params=None, **kw:
                        {"features": [{"properties": {"GEOID": "06001"}, "geometry": None}]})
    monkeypatch.setattr(bcp, "service_vintage", lambda url, log=print: "2024")
    with pytest.raises(RuntimeError, match="no county endpoint answered"):
        bcp.fetch_counties("27", "http://e/0", [], log=lambda *a: None)


def test_hostile_text_cannot_break_the_kml(tmp_path):
    """A CSV value containing ]]> must not terminate the CDATA and corrupt the
    document; & < > must survive as displayable text."""
    meta = _meta(seat=bcp.Sourced("Evil]]><script>x</script>", "csv", "2026"),
                 le_agency=bcp.Sourced("A & B <Sheriff>", "csv", "2026"))
    pm = bcp.county_placemark({}, {"type": "Polygon", "coordinates": [
        [[-93, 45], [-92, 45], [-92, 46], [-93, 45]]]}, meta)
    kml = bcp.state_kml("MN", [pm], {
        "title": "T", "boundary_source": "s", "boundary_url": "u",
        "acs_label": "a", "tiger_vintage": "2024", "built": "b"})
    minidom.parseString(kml)                       # would raise if CDATA broke
    assert "]]><script" not in kml                 # the escape really happened
    assert "]]&gt;" in kml
    assert "A &amp; B &lt;Sheriff&gt;" in kml


# --------------------------------------------------------------------------
# Regressions for defects an adversarial review found in the first cut
# --------------------------------------------------------------------------
def test_shipped_csvs_parse_despite_their_comment_block(tmp_path, monkeypatch):
    """THE bug this file exists to prevent recurring.

    csv.DictReader has no notion of comments. Handed the shipped CSV it adopted
    the first "#" line as the only field name, so every real row was dropped and
    both documented ways of filling county seats and sheriff contacts were dead
    on arrival - silently, because a dropped row renders as "not in dataset"
    exactly like a genuine absence.

    This test uses the REAL shipped files, not a hand-written fixture with a
    bare header; that shortcut is what hid the bug the first time.
    """
    real_seats = os.path.join(SP, "data", "county_seats.csv")
    real_le = os.path.join(SP, "data", "le_contacts.csv")
    assert "#" in open(real_seats).read(200)        # the shipped shape, still
    assert "#" in open(real_le).read(200)

    data = tmp_path / "data"
    data.mkdir()
    (data / "county_seats.csv").write_text(
        open(real_seats).read() + "27025,Center City,Wikidata,2026-09-14\n")
    (data / "le_contacts.csv").write_text(
        open(real_le).read()
        + "27025,Chisago County Sheriff's Office,651-555-0100,county website,2026\n")
    monkeypatch.setattr(bcp, "DATA_DIR", str(data))

    seats = bcp.load_csv_table("county_seats.csv")
    le = bcp.load_csv_table("le_contacts.csv")
    assert seats["27025"]["seat"] == "Center City"
    assert le["27025"]["phone"] == "651-555-0100"


def test_shipped_csvs_really_are_empty():
    """They promise to ship empty. Verify it, so a stray row cannot sneak in."""
    monkeypatched = bcp.DATA_DIR
    assert bcp.load_csv_table("county_seats.csv") == {}
    assert bcp.load_csv_table("le_contacts.csv") == {}
    assert monkeypatched.endswith("data")


def test_esc_removes_xml_illegal_control_characters():
    """XML 1.0 forbids C0 controls outright - they cannot be escaped, only
    dropped. One of them anywhere made the whole pack unparseable while the
    builder reported success."""
    assert bcp.esc("bad\x00name\x1f") == "badname"
    assert bcp.esc("keep\tthis\nand\rthis") == "keep\tthis\nand\rthis"
    assert bcp.esc("a & b < c") == "a &amp; b &lt; c"

    meta = _meta(seat=bcp.Sourced("Cen\x00ter Ci\x08ty", "csv", "2026"))
    pm = bcp.county_placemark({}, {"type": "Polygon", "coordinates": [
        [[-93, 45], [-92, 45], [-92, 46], [-93, 45]]]}, meta)
    kml = bcp.state_kml("MN", [pm], {"title": "T\x0c", "boundary_source": "s",
                                     "boundary_url": "u", "acs_label": "a",
                                     "tiger_vintage": "2024", "built": "b"})
    minidom.parseString(kml)                        # would raise on a raw \x00
    assert "Center City" in kml


@pytest.mark.parametrize("props,expected", [
    ({"NAME": "Chisago County"}, "Chisago County"),
    ({"NAME": "Acadia Parish"}, "Acadia Parish"),
    ({"NAME": "Nome Census Area"}, "Nome Census Area"),
    ({"NAME": "Alexandria city"}, "Alexandria city"),
    ({"NAME": "Adjuntas Municipio"}, "Adjuntas Municipio"),
    ({"NAME": "District of Columbia"}, "District of Columbia"),
    ({"NAMELSAD": "Juneau City and Borough", "NAME": "Juneau"},
     "Juneau City and Borough"),
    ({"BASENAME": "Chisago"}, "Chisago County"),    # bare name: suffix is right
    ({}, "Unknown"),
])
def test_county_label_never_bolts_County_onto_a_parish(props, expected):
    """A hardcoded ' County' produced 'Acadia Parish County', 'Chisago County
    County' and 'District of Columbia County'. 15 states do not call their
    county-equivalents counties."""
    assert bcp.county_label(props) == expected


def test_paging_follows_the_servers_own_limit_flag(monkeypatch):
    """A service whose maxRecordCount is under 1000 returns a SHORT first page
    that is not the last one. Treating it as the last truncated the state."""
    pages = [
        {"features": [{"properties": {"GEOID": f"270{i:02d}"}} for i in range(5)],
         "exceededTransferLimit": True},
        {"features": [{"properties": {"GEOID": f"271{i:02d}"}} for i in range(3)],
         "exceededTransferLimit": False},
    ]
    calls = []

    def fake(url, params=None, **kw):
        calls.append(params.get("resultOffset") if params else None)
        return pages[min(len(calls) - 1, len(pages) - 1)]

    monkeypatch.setattr(bcp, "get_json", fake)
    monkeypatch.setattr(bcp, "service_vintage", lambda url, log=print: "2024")
    kept, used, vintage = bcp.fetch_counties("27", "http://e/0", [], log=lambda *a: None)
    assert len(kept) == 8                           # both pages, not just the first
    assert calls[:2] == [0, 5]


def test_paging_cannot_loop_forever_when_offset_is_ignored(monkeypatch):
    """A server that ignores resultOffset and always says 'more' used to spin."""
    monkeypatch.setattr(bcp, "get_json", lambda url, params=None, **kw: {
        "features": [{"properties": {"GEOID": "27001"}}],
        "exceededTransferLimit": True})
    monkeypatch.setattr(bcp, "service_vintage", lambda url, log=print: "2024")
    msgs = []
    kept, used, vintage = bcp.fetch_counties("27", "http://e/0", [], log=msgs.append)
    assert len(kept) <= 25                          # guard tripped
    assert any("kept reporting more results" in m for m in msgs)


def test_acs_degrades_instead_of_killing_the_pack(monkeypatch):
    """The docstring promises degradation, so the PARSE must be guarded too -
    not just the fetch."""
    for bad in ([], [["NAME", "state", "county"]], [{"not": "a list"}], "nonsense"):
        monkeypatch.setattr(bcp, "get_json", lambda url, params=None, _b=bad, **kw: _b)
        msgs = []
        assert bcp.fetch_acs("27", 2023, log=msgs.append) == {}
        assert any("unexpected shape" in m or "unavailable" in m for m in msgs)


def test_vintage_comes_from_the_service_not_a_constant(monkeypatch):
    monkeypatch.setattr(bcp, "get_json", lambda url, params=None, **kw:
                        {"name": "TIGERweb/State_County", "description": "TIGER 2025 current"})
    assert bcp.service_vintage("http://e/1", log=lambda *a: None) == "2025"

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(bcp, "get_json", boom)
    assert bcp.service_vintage("http://e/1", log=lambda *a: None) == bcp.TIGER_VINTAGE_UNKNOWN


def test_unknown_vintage_is_never_shown_as_a_year(monkeypatch, tmp_path):
    """When the service reports no year, the pack must say so - and its filename
    must not claim one either."""
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print:
                        (_fake_counties(1), "http://e/1", bcp.TIGER_VINTAGE_UNKNOWN))
    monkeypatch.setattr(bcp, "fetch_acs", lambda sfp, year=2023, log=print: {})
    r = bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    assert os.path.basename(r["path"]) == "MN_Counties_built2026_09_14.kmz"
    kml = _doc(r["path"])
    assert "vintage not reported" in kml
    assert "TIGER 2024" not in kml                  # never a year nobody returned


def test_a_feature_with_no_identity_shows_no_fips(stubbed, tmp_path, monkeypatch):
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print: ([{
                            "properties": {"NAME": "Mystery County", "STATE": "27"},
                            "geometry": {"type": "Polygon", "coordinates": [RING]},
                        }], "http://e/1", "2024"))
    bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    kml = _doc(tmp_path / "MN_Counties_2024.kmz")
    assert "FIPS (GEOID):</b> not in dataset" in kml
    assert "27000" not in kml                       # the old fabrication
