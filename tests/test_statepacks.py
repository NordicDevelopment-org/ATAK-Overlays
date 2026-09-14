MN_PACK = "MN_Counties__2024.kmz"

"""Tests for statepacks/build_county_pack.py - the stdlib-only ATAK county builder.

No network: every fetch is monkeypatched. What is being proved here is that the
builder never invents a value and never mangles a shape.
"""
import importlib.util
import json
import os
import re
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


def test_an_absent_value_renders_as_nothing_never_as_zero():
    """The popup omits an absent field entirely, so there is no placeholder
    string left to get mistaken for data. What must never happen is a 0."""
    for empty in (bcp.Sourced(), bcp.Sourced(None, "ACS 5-year", "2023"), bcp.Sourced("")):
        assert bool(empty) is False
        assert empty.render() == ""
        assert empty.parts() == ("", "")
        assert "0" not in empty.render()


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


def test_popup_shows_what_exists_and_omits_what_does_not():
    """A field nothing returned is left out entirely. Nine rows of "not in
    dataset" bury the three that carry real values."""
    pm = bcp.county_placemark({}, {"type": "Polygon", "coordinates": [
        [[-93, 45], [-92, 45], [-92, 46], [-93, 45]]]}, _meta())
    assert "58,241 [ACS 5-year 2023]" in _text(pm)
    assert "414.2 sq mi [TIGER ALAND 2024]" in _text(pm)

    # absent fields get no row of their own at all
    assert "<b>County seat:</b>" not in pm
    assert "<b>Sheriff / primary LE:</b>" not in pm
    assert "<b>LE non-emergency:</b>" not in pm
    assert "not in dataset" not in pm

    # but the pack still says which fields had nothing, in one compact line,
    # so a blank is never mistaken for a value of zero
    assert "No data for:" in pm
    assert "County seat" in pm
    assert "LE non-emergency" in pm

    # and the boundary provenance rides in the placemark itself
    assert "tigerweb.example" in pm


def test_a_county_with_everything_has_no_no_data_line():
    full = _meta(seat=bcp.Sourced("Center City", "Wikidata", "2026"),
                 le_agency=bcp.Sourced("Chisago County Sheriff", "OSM", "2026"),
                 le_phone=bcp.Sourced("651-257-4100", "OSM", "2026"))
    pm = bcp.county_placemark({}, {"type": "Polygon", "coordinates": [
        [[-93, 45], [-92, 45], [-92, 46], [-93, 45]]]}, full)
    assert "No data for:" not in pm
    assert "651-257-4100 [OSM 2026]" in _text(pm)


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
    monkeypatch.setattr(bcp, "fetch_acs", lambda sfp, year=2023, log=print, **kw: {
        "000": {"population": bcp.Sourced(15900, "ACS 5-year", str(year)),
                "housing_units": bcp.Sourced(11000, "ACS 5-year", str(year))},
        "001": {"population": bcp.Sourced(372000, "ACS 5-year", str(year)),
                "housing_units": bcp.Sourced(140000, "ACS 5-year", str(year))},
    })
    return bcp


def _text(kml):
    """Popup text with tags stripped and whitespace collapsed - assert on what a
    reader sees, not on the markup or the spacing around it."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", kml))

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
    assert "413.9 sq mi [TIGER ALAND 2024]" in _text(kml)
    assert "28.5 sq mi [TIGER AWATER 2024]" in _text(kml)


def test_missing_acs_still_builds_and_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print:
                        (_fake_counties(1), "https://tigerweb.example/x/1", "2024"))
    monkeypatch.setattr(bcp, "fetch_acs", lambda sfp, year=2023, log=print, **kw: {})
    r = bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    assert r["counties"] == 1 and r["with_population"] == 0 and r["acs_year"] is None
    kml = _doc(tmp_path / MN_PACK)
    assert "<b>Population:</b>" not in kml              # omitted, never fabricated
    assert "No data for:" in kml and "Population" in kml
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
    assert "Center City [Wikidata 2026-09-14]" in _text(kml)
    assert "651-555-0100 [county website 2026]" in _text(kml)
    # the county with no CSV row still refuses to guess
    assert "not in dataset" in kml


def test_per_county_also_writes_individual_files(stubbed, tmp_path):
    bcp.build_state("MN", str(tmp_path), per_county=True, log=lambda *a: None,
                    today="2026-09-14")
    names = {p.name for p in tmp_path.glob("*.kmz")}
    assert MN_PACK in names
    families = {n.split("__")[0] for n in names}
    assert {"MN_Counties", "MN_County0_County", "MN_County1_County"} <= families
    # every per-county file is versioned too, so a rebuild supersedes it
    assert all("__" in n for n in names), names


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
    monkeypatch.setenv("CENSUS_API_KEY", "abc123")
    for bad in ('[]', '[["NAME","state","county"]]', '[{"not": "a list"}]', '"nonsense"'):
        monkeypatch.setattr(bcp, "http_get",
                            lambda url, params=None, _b=bad, **kw: _b.encode())
        msgs = []
        assert bcp.fetch_acs("27", 2023, log=msgs.append) == {}
        assert any("unexpected shape" in m for m in msgs), (bad, msgs)

    # and a body that is not JSON at all
    monkeypatch.setattr(bcp, "http_get",
                        lambda url, params=None, **kw: b"\x00not json")
    msgs = []
    assert bcp.fetch_acs("27", 2023, log=msgs.append) == {}
    assert any("unparseable" in m for m in msgs)

    # and a transport failure
    def boom(*a, **k):
        raise RuntimeError("socket died")
    monkeypatch.setattr(bcp, "http_get", boom)
    msgs = []
    assert bcp.fetch_acs("27", 2023, log=msgs.append) == {}
    assert any("unavailable" in m for m in msgs)


def test_vintage_is_the_layers_own_group_not_a_guess(monkeypatch):
    """TIGERweb stacks vintages as GROUP layers - "Census 2020", "ACS 2025" -
    with the current one at top level. The vintage is that group's name, which
    the service states, rather than a year scraped out of prose."""
    tree = {"layers": [
        {"id": 0, "name": "States"},
        {"id": 1, "name": "Counties"},                       # top level = Current
        {"id": 53, "name": "Census 2020", "subLayerIds": [55]},
        {"id": 55, "name": "Counties", "parentLayerId": 53},
    ]}
    monkeypatch.setattr(bcp, "get_json", lambda url, params=None, **kw: tree)
    assert bcp.service_vintage("http://e/MapServer/55", log=lambda *a: None) == "Census 2020"
    assert bcp.service_vintage("http://e/MapServer/1", log=lambda *a: None) == "Current"

    # a flat service with no groups falls back to a year in its own metadata
    monkeypatch.setattr(bcp, "get_json", lambda url, params=None, **kw:
                        {"layers": [], "description": "TIGER 2025 edition"})
    assert bcp.service_vintage("http://e/MapServer/0", log=lambda *a: None) == "2025"

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(bcp, "get_json", boom)
    assert bcp.service_vintage("http://e/1", log=lambda *a: None) == bcp.TIGER_VINTAGE_UNKNOWN


def test_a_vintage_with_no_year_still_dates_the_filename(monkeypatch, tmp_path):
    """"Current" ages the moment it is written, so the build date goes in the
    name too - a pack must be datable from its filename alone."""
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print:
                        (_fake_counties(1), "http://e/1", "Current"))
    monkeypatch.setattr(bcp, "fetch_acs", lambda sfp, year=2023, log=print, **kw: {})
    r = bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    assert os.path.basename(r["path"]) == "MN_Counties__Current_2026_09_14.kmz"

    # one that already names a year needs no date appended
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print:
                        (_fake_counties(1), "http://e/55", "Census 2020"))
    r2 = bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    assert os.path.basename(r2["path"]) == "MN_Counties__Census_2020.kmz"


def test_unknown_vintage_is_never_shown_as_a_year(monkeypatch, tmp_path):
    """When the service reports no year, the pack must say so - and its filename
    must not claim one either."""
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print:
                        (_fake_counties(1), "http://e/1", bcp.TIGER_VINTAGE_UNKNOWN))
    monkeypatch.setattr(bcp, "fetch_acs", lambda sfp, year=2023, log=print, **kw: {})
    r = bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    assert os.path.basename(r["path"]) == "MN_Counties__built2026_09_14.kmz"
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
    kml = _doc(tmp_path / MN_PACK)
    assert "<b>FIPS (GEOID):</b>" not in kml         # omitted rather than invented
    assert "No data for:" in kml and "FIPS (GEOID)" in kml
    assert "27000" not in kml                       # the old fabrication


# --------------------------------------------------------------------------
# seed_le_contacts.py - sheriff/LE enrichment from the frozen HIFLD snapshot
# --------------------------------------------------------------------------
sle = _load("seed_le_contacts")


def _fake_shapes(*geoids, name="Test County"):
    """A county_shapes stub that honours with_names, like the real one."""
    shapes = [(g, [[[[0, 0], [5, 0], [5, 5], [0, 5], [0, 0]]]]) for g in geoids]
    names = {g: name for g in geoids}

    def stub(sfp, log=print, with_names=False, **kw):
        return (shapes, names) if with_names else shapes

    return stub



def test_layer_is_found_by_name_not_by_a_hardcoded_index(monkeypatch):
    """A re-host can renumber its layers. Matching the name survives that;
    an index would silently query whatever sits at 0."""
    monkeypatch.setattr(sle, "http_json", lambda url, params=None, **kw: {
        "description": "HIFLD Open final snapshot, 2025",
        "layers": [{"id": 0, "name": "correctional_facilities"},
                   {"id": 3, "name": "local_law_enforcement_locations"}]})
    lid, vintage = sle.resolve_layer(log=lambda *a: None)
    assert lid == 3 and vintage == "2025"


def test_missing_layer_fails_loudly_and_lists_what_was_there(monkeypatch):
    monkeypatch.setattr(sle, "http_json", lambda url, params=None, **kw: {
        "layers": [{"id": 0, "name": "fire_stations"}]})
    with pytest.raises(RuntimeError, match="no layer matching"):
        sle.resolve_layer(log=lambda *a: None)


def test_unknown_snapshot_year_is_never_guessed(monkeypatch):
    monkeypatch.setattr(sle, "http_json", lambda url, params=None, **kw: {
        "layers": [{"id": 1, "name": "local_law_enforcement"}]})
    _, vintage = sle.resolve_layer(log=lambda *a: None)
    assert vintage == sle.VINTAGE_UNKNOWN
    assert not any(ch.isdigit() for ch in vintage)


def _le(geoid, name, phone=""):
    return {"attributes": {"COUNTYFIPS": geoid, "NAME": name,
                           "TELEPHONE": phone, "ADDRESS": "1 Main St", "TYPE": "LOCAL"}}


def test_records_filed_under_a_county_they_are_not_in_are_dropped(monkeypatch):
    monkeypatch.setattr(sle, "http_json", lambda url, params=None, **kw: {
        "features": [_le("27025", "Chisago County Sheriff", "651-555-0100"),
                     _le("06001", "Alameda County Sheriff", "510-555-0100"),
                     _le("", "No County PD"), _le("bogus", "Bad PD")],
        "exceededTransferLimit": False})
    got = sle.fetch_state("27", 1, log=lambda *a: None)
    assert [r["geoid"] for r in got] == ["27025"]


def test_sheriff_is_picked_over_city_pd_and_a_phone_wins(monkeypatch):
    recs = [
        {"geoid": "27025", "agency": "Center City Police Department", "phone": "1", "address": "", "type": ""},
        {"geoid": "27025", "agency": "Chisago County Sheriff's Office", "phone": "", "address": "", "type": ""},
        {"geoid": "27025", "agency": "Chisago County Sheriff - Patrol", "phone": "651-555-0100", "address": "", "type": ""},
        {"geoid": "27053", "agency": "Hennepin County Sheriff", "phone": "612-555-0100", "address": "", "type": ""},
    ]
    picked = sle.pick_sheriffs(recs)
    assert set(picked) == {"27025", "27053"}
    assert "Police Department" not in picked["27025"]["agency"]
    assert picked["27025"]["phone"] == "651-555-0100"      # the one with a number


def test_seeding_never_overwrites_a_row_you_verified(tmp_path, monkeypatch, capsys):
    csv_path = tmp_path / "le_contacts.csv"
    csv_path.write_text(
        "# a comment block, like the shipped file\n"
        "geoid,agency,phone,source,vintage\n"
        "27025,Chisago County Sheriff's Office,651-257-4100,I called them,2026\n")
    monkeypatch.setattr(sle, "CSV_PATH", str(csv_path))
    monkeypatch.setattr(sle, "resolve_layer", lambda base=None, log=print: (1, "2025"))
    monkeypatch.setattr(sle, "fetch_state", lambda sfp, lid, base=None, log=print: [
        {"geoid": "27025", "agency": "Chisago County Sheriff", "phone": "000-000-0000",
         "address": "", "type": ""},
        {"geoid": "27053", "agency": "Hennepin County Sheriff", "phone": "612-555-0100",
         "address": "", "type": ""}])

    assert sle.main(["--state", "MN", "--source", "hifld"]) == 0
    rows, comments = sle.read_existing(str(csv_path))
    # the hand-verified row survives untouched...
    assert rows["27025"]["phone"] == "651-257-4100"
    assert rows["27025"]["source"] == "I called them"
    # ...and the new one is stamped with the snapshot's own vintage
    assert rows["27053"]["phone"] == "612-555-0100"
    assert rows["27053"]["vintage"] == "2025"
    assert sle.SOURCE in rows["27053"]["source"]
    assert comments and comments[0].startswith("#")       # comment block preserved

    # --overwrite is opt-in and does replace
    assert sle.main(["--state", "MN", "--source", "hifld", "--overwrite"]) == 0
    rows2, _ = sle.read_existing(str(csv_path))
    assert rows2["27025"]["phone"] == "000-000-0000"


def test_auto_prefers_osm_because_it_is_the_only_source_with_phone_numbers(
        monkeypatch, tmp_path, capsys):
    """The phone column has no other home: HIFLD's host is gone and USGS has no
    phone field at all. So auto reaches for OSM first."""
    monkeypatch.setattr(sle, "CSV_PATH", str(tmp_path / "le.csv"))
    monkeypatch.setattr(sle, "fetch_usgs",
                        lambda *a, **k: pytest.fail("USGS used when OSM was available"))
    monkeypatch.setattr(sle, "fetch_osm", lambda st, mirrors=None, log=print, **kw: [
        {"name": "Chisago County Sheriff", "phone": "651-257-4100", "address": "1 Main",
         "city": "Center City", "admintype": "county", "loaddate": "",
         "lon": 1.0, "lat": 1.0},
        {"name": "Elsewhere Sheriff", "phone": "", "address": "", "city": "",
         "admintype": "", "loaddate": "", "lon": 99.0, "lat": 99.0},
    ])
    monkeypatch.setattr(sle, "county_shapes", _fake_shapes("27025"))

    assert sle.main(["--state", "MN"]) == 0
    out = capsys.readouterr().out
    assert "OSM police features" in out
    assert "1 with a phone number" in out
    assert "fell outside every MN county" in out          # the unplaced one

    rows, _ = sle.read_existing(str(tmp_path / "le.csv"))
    assert rows["27025"]["phone"] == "651-257-4100"    # a real phone, at last
    assert "OpenStreetMap" in rows["27025"]["source"]
    # OSM has no dataset vintage, so the stamp is the fetch date, not a guess
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", rows["27025"]["vintage"])
    assert "community-maintained" in out


def test_osm_reads_both_phone_spellings_and_a_way_centre(monkeypatch, tmp_path):
    """OSM uses `phone` and `contact:phone` interchangeably, and a station
    mapped as a building has no lat/lon of its own - only `center`."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    els = [
        {"type": "node", "id": 1, "lat": 45.5, "lon": -92.8,
         "tags": {"name": "A PD", "phone": "111"}},
        {"type": "way", "id": 2, "center": {"lat": 45.6, "lon": -92.9},
         "tags": {"name": "B Sheriff", "contact:phone": "222",
                  "addr:housenumber": "12", "addr:street": "Main St",
                  "addr:city": "Town", "operator:type": "county"}},
        {"type": "way", "id": 3, "tags": {"name": "No Geometry"}},   # dropped
    ]
    monkeypatch.setattr(sle, "_overpass_tile", lambda tile, m, t, a, log, deadline=None, locks=None, start=0: (els, False))
    got = sle.fetch_osm("MN", log=lambda *a: None, bbox=(-97.3, 43.4, -89.4, 49.4))

    assert [r["name"] for r in got] == ["A PD", "B Sheriff"]
    assert got[0]["phone"] == "111"
    assert got[1]["phone"] == "222"                    # contact:phone honoured
    assert (got[1]["lon"], got[1]["lat"]) == (-92.9, 45.6)   # way centre used
    assert got[1]["address"] == "12 Main St"
    assert got[1]["admintype"] == "county"


def test_the_overpass_query_is_a_bbox_not_an_area_lookup(monkeypatch, tmp_path):
    """area["ISO3166-2"=...] makes the server resolve the state boundary first,
    and that is what every public mirror answered with 504."""
    import json as _json
    import urllib.parse
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    captured = {}

    class FakeResp:
        def read(self):
            return _json.dumps({"elements": []}).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None, context=None):
        captured["body"] = urllib.parse.unquote_plus(req.data.decode())
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    sle._overpass_tile((-97.3, 43.4, -89.4, 49.4), sle.OVERPASS_MIRRORS, 90, 1,
                       lambda *a: None)
    body = captured["body"]
    assert 'amenity"="police' in body
    assert "out center tags" in body
    assert "area[" not in body
    assert "(43.4000,-97.3000,49.4000,-89.4000)" in body   # s,w,n,e order


def test_every_source_down_writes_nothing_and_exits_nonzero(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sle, "CSV_PATH", str(tmp_path / "le.csv"))

    def dead(*a, **k):
        raise RuntimeError("host unreachable")
    monkeypatch.setattr(sle, "resolve_layer", dead)
    monkeypatch.setattr(sle, "fetch_usgs", dead)
    monkeypatch.setattr(sle, "fetch_osm", dead)
    monkeypatch.setattr(sle, "county_shapes", _fake_shapes("27025"))
    assert sle.main(["--state", "MN"]) == 2
    err = capsys.readouterr().err
    assert "no source answered" in err and "stay empty" in err
    assert not (tmp_path / "le.csv").exists()            # nothing written at all


def test_forcing_hifld_does_not_silently_use_usgs(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sle, "CSV_PATH", str(tmp_path / "le.csv"))

    def dead(*a, **k):
        raise RuntimeError("host unreachable")
    monkeypatch.setattr(sle, "resolve_layer", dead)
    monkeypatch.setattr(sle, "fetch_usgs",
                        lambda *a, **k: pytest.fail("USGS must not be used with --source hifld"))
    assert sle.main(["--state", "MN", "--source", "hifld"]) == 2


def test_seeded_rows_flow_into_the_popup_with_their_vintage(tmp_path, monkeypatch):
    """The whole point: a seeded row must reach ATAK carrying its own year."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "le_contacts.csv").write_text(
        "# comment\ngeoid,agency,phone,source,vintage\n"
        "27000,County0 Sheriff's Office,651-555-0100,"
        "HIFLD LE Locations (frozen snapshot),2025\n")
    monkeypatch.setattr(bcp, "DATA_DIR", str(data))
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print:
                        (_fake_counties(1), "http://e/1", "2024"))
    monkeypatch.setattr(bcp, "fetch_acs", lambda sfp, year=2023, log=print, **kw: {})
    bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    kml = _doc(tmp_path / MN_PACK)
    txt = _text(kml)
    assert "Sheriff&#39;s Office [HIFLD LE Locations (frozen snapshot) 2025]" in txt \
        or "Sheriff's Office [HIFLD LE Locations (frozen snapshot) 2025]" in txt
    assert "651-555-0100 [HIFLD LE Locations (frozen snapshot) 2025]" in txt


# --------------------------------------------------------------------------
# Census API key. A keyless request returns HTTP 200 with an HTML page titled
# "Missing Key", so the failure has to be recognised by body, not status.
# --------------------------------------------------------------------------
MISSING_KEY_HTML = ('<html style="font-size: 14px;">\n\n<head>\n'
                    '    <title>Missing Key</title>\n')
INVALID_KEY_HTML = '<html><head><title>Invalid Key</title></head></html>'


def test_key_is_found_in_priority_order(monkeypatch, tmp_path):
    keyfile = tmp_path / "census_key"
    keyfile.write_text("from_file\n")
    monkeypatch.setattr(bcp, "KEY_FILE", str(keyfile))

    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    assert bcp.census_key() == "from_file"

    monkeypatch.setenv("CENSUS_API_KEY", "from_env")
    assert bcp.census_key() == "from_env"            # env beats the file
    assert bcp.census_key("explicit") == "explicit"  # the flag beats both

    monkeypatch.setattr(bcp, "KEY_FILE", str(tmp_path / "nope"))
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    assert bcp.census_key() == ""                    # absent, not an exception


def test_no_key_says_exactly_what_to_do(monkeypatch, tmp_path):
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    monkeypatch.setattr(bcp, "KEY_FILE", str(tmp_path / "nope"))
    msgs = []
    assert bcp.fetch_acs("27", 2023, log=msgs.append) == {}
    blob = "\n".join(msgs)
    assert "requires a key" in blob
    assert bcp.ACS_KEY_SIGNUP in blob
    assert "CENSUS_API_KEY" in blob
    assert "not in dataset" in blob                  # says what the pack will show


def test_html_refusal_is_reported_as_a_key_problem_not_a_parse_error(monkeypatch):
    """A keyless request answers 200 + HTML, so json.loads reported
    "Expecting value: line 1 column 1" and buried the real cause."""
    monkeypatch.setenv("CENSUS_API_KEY", "abc123")
    monkeypatch.setattr(bcp, "http_get",
                        lambda url, params=None, **kw: MISSING_KEY_HTML.encode())
    msgs = []
    assert bcp.fetch_acs("27", 2023, log=msgs.append) == {}
    blob = "\n".join(msgs)
    assert "no key reached the API" in blob
    assert "Expecting value" not in blob

    monkeypatch.setattr(bcp, "http_get",
                        lambda url, params=None, **kw: INVALID_KEY_HTML.encode())
    msgs = []
    assert bcp.fetch_acs("27", 2023, log=msgs.append) == {}
    assert "key was rejected" in "\n".join(msgs)


def test_the_key_is_actually_sent(monkeypatch):
    seen = {}

    def capture(url, params=None, **kw):
        seen.update(params or {})
        return json.dumps([
            ["NAME", "B01003_001E", "B25001_001E", "state", "county"],
            ["Chisago County, Minnesota", "58241", "23110", "27", "025"],
        ]).encode()

    monkeypatch.setenv("CENSUS_API_KEY", "secret-key")
    monkeypatch.setattr(bcp, "http_get", capture)
    got = bcp.fetch_acs("27", 2023, log=lambda *a: None)
    assert seen.get("key") == "secret-key"
    assert got["025"]["population"].value == 58241
    assert got["025"]["population"].vintage == "2023"
    assert got["025"]["housing_units"].value == 23110


def test_key_flows_from_build_state_into_the_popup(monkeypatch, tmp_path):
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print:
                        (_fake_counties(1), "http://e/1", "Current"))
    seen = {}

    def capture(url, params=None, **kw):
        seen.update(params or {})
        return json.dumps([
            ["NAME", "B01003_001E", "B25001_001E", "state", "county"],
            ["County0, Minnesota", "15900", "11000", "27", "000"],
        ]).encode()

    monkeypatch.setattr(bcp, "http_get", capture)
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14",
                    census_api_key="passed-through")
    assert seen.get("key") == "passed-through"
    kml = _doc(tmp_path / "MN_Counties__Current_2026_09_14.kmz")
    assert "15,900 [ACS 5-year 2023]" in _text(kml)
    assert "11,000 [ACS 5-year 2023]" in _text(kml)


# --------------------------------------------------------------------------
# A key must never reach console output. These scripts are written to have
# their output pasted into chats and bug reports.
# --------------------------------------------------------------------------
# A FAKE key shaped like a real one. Never put a live credential in a test.
SECRET = "deadbeefcafe0000deadbeefcafe0000deadbeef"


def test_redact_strips_keys_from_urls():
    u = f"https://api.census.gov/data/2023/acs/acs5?get=NAME&key={SECRET}"
    assert SECRET not in bcp.redact(u)
    assert "key=<redacted>" in bcp.redact(u)
    # mid-query, and other secret-ish names
    assert SECRET not in bcp.redact(f"http://x/y?a=1&key={SECRET}&b=2")
    assert "b=2" in bcp.redact(f"http://x/y?a=1&key={SECRET}&b=2")
    assert SECRET not in bcp.redact(f"http://x?api_key={SECRET}")
    assert SECRET not in bcp.redact(f"http://x?token={SECRET}")
    # leaves ordinary URLs alone
    plain = "https://tigerweb.geo.census.gov/x/MapServer/1/query?where=STATE%3D%2727%27"
    assert bcp.redact(plain) == plain


def test_a_failed_acs_request_does_not_print_the_key(monkeypatch):
    """http_get raises with the URL in the message, and the URL carries the key."""
    monkeypatch.setenv("CENSUS_API_KEY", SECRET)

    def boom(url, params=None, **kw):
        # mimic the real http_get: params are appended, then the URL is raised
        import urllib.parse
        full = url + "?" + urllib.parse.urlencode(params or {})
        raise RuntimeError(f"GET failed after 4 tries: {bcp.redact(full)}")

    monkeypatch.setattr(bcp, "http_get", boom)
    msgs = []
    assert bcp.fetch_acs("27", 2023, log=msgs.append) == {}
    blob = "\n".join(msgs)
    assert SECRET not in blob, "the API key leaked into log output"
    assert "unavailable" in blob


def test_real_http_get_redacts_before_raising(monkeypatch):
    """Drive the actual failure path, not a stand-in."""
    import urllib.error

    def always_fail(req, timeout=None, context=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(bcp, "urlopen", always_fail, raising=False)
    monkeypatch.setattr("urllib.request.urlopen", always_fail)
    monkeypatch.setattr(bcp.time, "sleep", lambda *_a: None, raising=False)
    try:
        bcp.http_get("https://api.census.gov/data/2023/acs/acs5",
                     {"get": "NAME", "key": SECRET}, tries=1)
    except RuntimeError as e:
        assert SECRET not in str(e), "the API key leaked into the exception"
        assert "key=<redacted>" in str(e)
    else:
        pytest.fail("expected the request to fail")


# --------------------------------------------------------------------------
# Local enrichment data lives beside the shipped template, never over it.
# --------------------------------------------------------------------------
def test_local_csv_overrides_shipped_and_neither_clobbers_the_other(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    # the shipped template: tracked in git, ships with a comment block
    (data / "county_seats.csv").write_text(
        "# shipped template\ngeoid,seat,source,vintage\n"
        "27001,FromShipped,repo,2020\n27003,OnlyShipped,repo,2020\n")
    # what a fetch writes: gitignored, wins where they overlap
    (data / "county_seats.local.csv").write_text(
        "# local\ngeoid,seat,source,vintage\n"
        "27001,FromLocal,Wikidata,2026-09-14\n27005,OnlyLocal,Wikidata,2026-09-14\n")
    monkeypatch.setattr(bcp, "DATA_DIR", str(data))

    t = bcp.load_csv_table("county_seats.csv")
    assert t["27001"]["seat"] == "FromLocal"          # local wins
    assert t["27001"]["source"] == "Wikidata"
    assert t["27003"]["seat"] == "OnlyShipped"        # shipped still read
    assert t["27005"]["seat"] == "OnlyLocal"          # local-only row present


def test_missing_local_file_is_not_an_error(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "le_contacts.csv").write_text(
        "# shipped\ngeoid,agency,phone,source,vintage\n")
    monkeypatch.setattr(bcp, "DATA_DIR", str(data))
    assert bcp.load_csv_table("le_contacts.csv") == {}


def test_the_fetchers_write_local_files_not_the_tracked_templates():
    """A fetch must never dirty a tracked file - that is what makes `git pull`
    conflict on a machine that has run one."""
    assert sle.CSV_PATH.endswith("le_contacts.local.csv")
    seats = _load("fetch_county_seats")
    assert seats.CSV_PATH.endswith("county_seats.local.csv")


def test_local_data_is_gitignored():
    ignore = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
    assert "statepacks/data/*.local.csv" in ignore


# --------------------------------------------------------------------------
# Spatial county assignment. USGS has no FIPS column, so the county comes from
# geometry - and a sheriff filed under the wrong county is worse than absent.
# --------------------------------------------------------------------------
SQUARE = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
HOLE = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]


def test_point_in_ring_basic():
    assert sle.point_in_ring(5, 5, SQUARE)
    assert not sle.point_in_ring(15, 5, SQUARE)
    assert not sle.point_in_ring(5, -1, SQUARE)
    assert not sle.point_in_ring(-0.001, 5, SQUARE)


def test_a_point_in_a_hole_is_outside_the_polygon():
    """A county with an enclave carved out does not contain what is inside it."""
    rings = [SQUARE, HOLE]
    assert sle.point_in_polygon(2, 2, rings)          # in the body
    assert not sle.point_in_polygon(5, 5, rings)      # in the hole
    assert sle.point_in_polygon(9, 9, rings)          # other side of the hole


def test_assign_county_picks_the_containing_county():
    a = ("27001", [[[[0, 0], [5, 0], [5, 5], [0, 5], [0, 0]]]])
    b = ("27003", [[[[5, 0], [10, 0], [10, 5], [5, 5], [5, 0]]]])
    counties = [a, b]
    assert sle.assign_county(1, 1, counties) == "27001"
    assert sle.assign_county(7, 2, counties) == "27003"


def test_a_point_outside_every_county_is_dropped_not_guessed():
    counties = [("27001", [[SQUARE]])]
    assert sle.assign_county(99, 99, counties) is None
    assert sle.assign_county(-5, 5, counties) is None


def test_multipolygon_county_matches_its_exclave():
    """A detached exclave is still that county - Lake of the Woods and the
    Northwest Angle, for one."""
    main = [[0, 0], [5, 0], [5, 5], [0, 5], [0, 0]]
    exclave = [[20, 20], [22, 20], [22, 22], [20, 22], [20, 20]]
    counties = [("27077", [[main], [exclave]])]
    assert sle.assign_county(1, 1, counties) == "27077"
    assert sle.assign_county(21, 21, counties) == "27077"      # the exclave
    assert sle.assign_county(10, 10, counties) is None


def test_usgs_vintage_reads_loaddate_and_never_invents_one():
    import datetime as dt
    ms = int(dt.datetime(2024, 6, 1).timestamp() * 1000)
    older = int(dt.datetime(2019, 1, 1).timestamp() * 1000)
    assert sle.usgs_vintage([{"loaddate": ms}, {"loaddate": older}]) == "2024"
    assert sle.usgs_vintage([{"loaddate": "2022-03-04"}]) == "2022"
    got = sle.usgs_vintage([{"loaddate": ""}, {}])
    assert got == "load date not reported"
    assert not any(ch.isdigit() for ch in got)


def test_usgs_fetch_keeps_only_points_with_usable_geometry(monkeypatch):
    monkeypatch.setattr(sle, "http_json", lambda url, params=None, **kw: {
        "features": [
            {"properties": {"NAME": "Chisago County Sheriff", "ADDRESS": "1 Main",
                            "ADMINTYPE": "County", "LOADDATE": 1700000000000},
             "geometry": {"type": "Point", "coordinates": [-92.8, 45.5]}},
            {"properties": {"NAME": "No Geometry PD"}, "geometry": None},
            {"properties": {"NAME": "A Line"},
             "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}},
            {"properties": {"NAME": "Short Coords"},
             "geometry": {"type": "Point", "coordinates": [1]}},
        ],
        "exceededTransferLimit": False})
    got = sle.fetch_usgs("MN", 18, log=lambda *a: None)
    assert [r["name"] for r in got] == ["Chisago County Sheriff"]
    assert got[0]["lon"] == -92.8 and got[0]["lat"] == 45.5
    assert got[0]["admintype"] == "County"


def test_module_defines_everything_main_needs_before_the_entrypoint():
    """The __main__ guard executes where it sits in the file, so a function
    defined below it does not exist when main() runs - and monkeypatched tests
    never notice, because they import the whole module first. This caught a
    real NameError that every other test passed straight through."""
    src = open(os.path.join(SP, "seed_le_contacts.py"), encoding="utf-8").read()
    guard = src.index('if __name__ == "__main__":')
    after = src[guard:]
    assert "\ndef " not in after, (
        "a function is defined after the __main__ guard; it will not exist "
        "when main() runs")

    for name in ("fetch_usgs", "county_shapes", "assign_county", "usgs_vintage",
                 "point_in_polygon", "redact_err"):
        assert src.index(f"def {name}") < guard, f"{name} is defined too late"


def test_records_that_all_fail_the_filter_are_reported_not_silently_zero(
        monkeypatch, tmp_path, capsys):
    """448 records in and 0 rows out is a filter problem, not an absence of
    sheriffs. Saying nothing reads as 'this state has none'."""
    monkeypatch.setattr(sle, "CSV_PATH", str(tmp_path / "le.csv"))

    def dead(*a, **k):
        raise RuntimeError("gone")
    monkeypatch.setattr(sle, "resolve_layer", dead)
    monkeypatch.setattr(sle, "fetch_osm", lambda st, mirrors=None, log=print, **kw: [
        {"name": "MINNEAPOLIS POLICE DEPT", "phone": "", "address": "", "city": "",
         "admintype": "Local", "loaddate": "", "lon": 1.0, "lat": 1.0},
        {"name": "ST PAUL POLICE", "phone": "", "address": "", "city": "",
         "admintype": "Local", "loaddate": "", "lon": 2.0, "lat": 2.0},
    ])
    monkeypatch.setattr(sle, "county_shapes", _fake_shapes("27053"))

    sle.main(["--state", "MN"])
    out = capsys.readouterr().out
    assert "none matched" in out
    assert "--show 5" in out                    # tells you how to look
    assert "--all-agencies" in out              # and how to widen


def test_show_dumps_records_and_reports_what_the_filter_would_match(
        monkeypatch, capsys):
    monkeypatch.setattr(sle, "resolve_layer",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gone")))
    monkeypatch.setattr(sle, "fetch_osm", lambda st, mirrors=None, log=print, **kw: [
        {"name": "Chisago County Sheriff", "phone": "651-1", "address": "1 Main",
         "city": "X", "admintype": "County", "loaddate": "", "lon": 1.0, "lat": 1.0},
        {"name": "Center City Police", "phone": "", "address": "2 Main",
         "city": "Y", "admintype": "Local", "loaddate": "", "lon": 2.0, "lat": 2.0},
    ])
    assert sle.main(["--state", "MN", "--show", "2"]) == 0
    out = capsys.readouterr().out
    assert "Chisago County Sheriff" in out
    assert "admintype" in out
    assert "1 of 2" in out                      # the match count
    assert "distinct admintype" in out          # the classifying column's values


def test_bbox_comes_from_the_real_boundaries_not_a_typed_in_envelope():
    """The Overpass bbox is derived from the county shapes already downloaded,
    so no envelope is carried in this file to drift out of date."""
    shapes = [
        ("27001", [[[[-93.0, 45.0], [-92.0, 45.0], [-92.0, 46.0], [-93.0, 45.0]]]]),
        ("27003", [[[[-95.0, 44.0], [-94.0, 44.0], [-94.0, 44.5], [-95.0, 44.0]]]]),
    ]
    w, s_, e, n = sle.bbox_of_shapes(shapes, pad=0.0)
    assert (w, s_, e, n) == (-95.0, 44.0, -92.0, 46.0)

    w2, s2, e2, n2 = sle.bbox_of_shapes(shapes, pad=0.5)
    assert (w2, s2, e2, n2) == (-95.5, 43.5, -91.5, 46.5)

    with pytest.raises(ValueError, match="no county geometry"):
        sle.bbox_of_shapes([])


def test_discovery_looks_inside_services_whose_own_name_does_not_match(monkeypatch, capsys):
    """A service called "mn_structures" can hold a law-enforcement LAYER.
    Filtering on the service name alone means never opening it - which is how a
    29-service server reported 'nothing matched'."""
    root = "http://gis.example/arcgis/rest/services"

    def fake(url, params=None, **kw):
        if url == root:
            return {"folders": [], "services": [
                {"name": "mn_structures", "type": "MapServer"},
                {"name": "mn_hydro", "type": "MapServer"},
            ]}
        if url.endswith("mn_structures/MapServer"):
            return {"layers": [{"id": 0, "name": "Schools"},
                               {"id": 7, "name": "Law Enforcement Locations"}]}
        return {"layers": [{"id": 0, "name": "Lakes"}]}

    monkeypatch.setattr(sle, "http_json", fake)
    found = sle.discover_arcgis(root, "law|police|sheriff", log=lambda *a: None)
    assert found == [(f"{root}/mn_structures/MapServer", 7, "Law Enforcement Locations")]


def test_discovery_with_an_empty_pattern_lists_everything(monkeypatch):
    root = "http://gis.example/arcgis/rest/services"

    def fake(url, params=None, **kw):
        if url == root:
            return {"folders": [], "services": [{"name": "a", "type": "MapServer"}]}
        return {"layers": [{"id": 0, "name": "Anything"}, {"id": 1, "name": "Else"}]}

    monkeypatch.setattr(sle, "http_json", fake)
    found = sle.discover_arcgis(root, "", log=lambda *a: None)
    assert len(found) == 2


def test_value_is_bold_and_provenance_is_grey_and_bracketed():
    """What someone is reading the popup FOR is the number. The source and year
    are there for judgement and must not compete with it."""
    pm = bcp.county_placemark({}, {"type": "Polygon", "coordinates": [
        [[-93, 45], [-92, 45], [-92, 46], [-93, 45]]]}, _meta())

    assert "<b>58,241</b>" in pm                        # the value, bold
    assert f'<font color="{bcp.GREY}">[ACS 5-year 2023]</font>' in pm
    assert "Population: <b>" in pm                      # label plain, value bold
    # provenance never ends up inside the bold run
    assert "<b>58,241  [ACS" not in pm

    # the absence line is grey too, so it recedes
    assert f'<font color="{bcp.GREY}"><i>No data for:' in pm
    # and the whole thing is still valid XML inside its CDATA
    kml = bcp.state_kml("MN", [pm], {
        "title": "T", "boundary_source": "s", "boundary_url": "u",
        "acs_label": "a", "tiger_vintage": "2024", "built": "b"})
    minidom.parseString(kml)


def test_parts_keeps_value_and_provenance_separate():
    s = bcp.Sourced(58241, "ACS 5-year", "2023")
    assert s.parts(lambda v: f"{v:,}") == ("58,241", "ACS 5-year 2023")
    assert bcp.Sourced("x").parts() == ("x", "")        # no source, no tag
    assert bcp.Sourced().parts() == ("", "")            # absent value
    # render() still gives the plain one-line form for logs and tests
    assert s.render(lambda v: f"{v:,}") == "58,241  [ACS 5-year 2023]"


def test_a_value_containing_markup_cannot_escape_its_bold_run():
    nasty = bcp.Sourced("</b><script>x</script>", "csv", "2026")
    pm = bcp.county_placemark({}, {"type": "Polygon", "coordinates": [
        [[-93, 45], [-92, 45], [-92, 46], [-93, 45]]]}, _meta(seat=nasty))
    assert "<script>" not in pm
    assert "&lt;script&gt;" in pm
    kml = bcp.state_kml("MN", [pm], {
        "title": "T", "boundary_source": "s", "boundary_url": "u",
        "acs_label": "a", "tiger_vintage": "2024", "built": "b"})
    minidom.parseString(kml)


# --------------------------------------------------------------------------
# Overpass reliability. The public mirrors 504 on a statewide box under load -
# the same query returned 511 features when a mirror was idle, so it is
# contention, not an impossible request.
# --------------------------------------------------------------------------
def test_tile_bbox_covers_the_whole_box_without_gaps():
    tiles = sle.tile_bbox((0.0, 0.0, 9.0, 9.0), cols=3, rows=3)
    assert len(tiles) == 9
    assert min(t[0] for t in tiles) == 0.0 and max(t[2] for t in tiles) == 9.0
    assert min(t[1] for t in tiles) == 0.0 and max(t[3] for t in tiles) == 9.0
    assert all(t[2] > t[0] and t[3] > t[1] for t in tiles)
    assert abs(sum((t[2] - t[0]) * (t[3] - t[1]) for t in tiles) - 81.0) < 1e-9


def _one_bad_tile(dead, ids=None):
    """A stub whose tile at `dead` (and every quarter of it) always fails."""
    ids = ids or {"n": 0}
    lock = __import__("threading").Lock()

    def stub(tile, mirrors, timeout, attempts, log, deadline=None,
             locks=None, start=0):
        w, s_, e, n = tile
        dw, ds, de, dn = dead
        inside = (w >= dw - 1e-9 and e <= de + 1e-9
                  and s_ >= ds - 1e-9 and n <= dn + 1e-9)
        if inside:
            raise RuntimeError("HTTP Error 504: Gateway Timeout")
        with lock:
            ids["n"] += 1
            i = ids["n"]
        return ([{"type": "node", "id": i, "lat": 1.0, "lon": 1.0,
                  "tags": {"name": f"PD {i}"}}], False)

    return stub


def test_a_failed_tile_refuses_to_pass_off_a_partial_answer(monkeypatch, tmp_path):
    """Some counties populated and others empty, with nothing in the pack to say
    which is which, is worse than a clean failure."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    bbox = (0.0, 0.0, 9.0, 9.0)
    dead = sle.tile_bbox(bbox)[4]                   # the middle tile, and its quarters
    monkeypatch.setattr(sle, "_overpass_tile", _one_bad_tile(dead))
    with pytest.raises(RuntimeError, match="tiles failed"):
        sle.fetch_osm("MN", bbox=bbox, log=lambda *a: None)

    monkeypatch.setattr(sle, "_overpass_tile", _one_bad_tile(dead))
    got = sle.fetch_osm("MN", bbox=bbox, log=lambda *a: None, allow_partial=True)
    assert len(got) == 8                            # 9 tiles, 1 failed


def test_a_tile_the_mirrors_refuse_is_retried_as_quarters(monkeypatch, tmp_path):
    """A smaller box is a cheaper question. Asking for the same one again is
    what turned one busy mirror into a failed run."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    bbox = (0.0, 0.0, 9.0, 9.0)
    dead = sle.tile_bbox(bbox)[4]
    seen, lock = [], __import__("threading").Lock()

    def stub(tile, mirrors, timeout, attempts, log, deadline=None,
             locks=None, start=0):
        with lock:
            seen.append(tile)
            i = len(seen)
        # the whole middle tile fails; its quarters are served
        if tile == dead:
            raise RuntimeError("HTTP Error 504: Gateway Timeout")
        return ([{"type": "node", "id": i, "lat": 1.0, "lon": 1.0,
                  "tags": {"name": f"PD {i}"}}], False)

    monkeypatch.setattr(sle, "_overpass_tile", stub)
    got = sle.fetch_osm("MN", bbox=bbox, log=lambda *a: None)
    quarters = sle.split_tile(dead)
    assert all(q in seen for q in quarters), "the failed tile was not split"
    assert len(got) == 8 + 4                        # 8 whole tiles + 4 quarters


def test_splitting_is_not_partial_credit(monkeypatch, tmp_path):
    """Three quarters out of four is still a hole. It must not pass as covered."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    bbox = (0.0, 0.0, 9.0, 9.0)
    dead = sle.tile_bbox(bbox)[4]
    doomed = sle.split_tile(dead)[2]
    lock, ids = __import__("threading").Lock(), {"n": 0}

    def stub(tile, mirrors, timeout, attempts, log, deadline=None,
             locks=None, start=0):
        if tile == dead or tile == doomed:
            raise RuntimeError("HTTP Error 504: Gateway Timeout")
        with lock:
            ids["n"] += 1
            i = ids["n"]
        return ([{"type": "node", "id": i, "lat": 1.0, "lon": 1.0,
                  "tags": {"name": f"PD {i}"}}], False)

    monkeypatch.setattr(sle, "_overpass_tile", stub)
    with pytest.raises(RuntimeError, match="1 of 9 tiles failed"):
        sle.fetch_osm("MN", bbox=bbox, log=lambda *a: None)


def test_successful_tiles_are_cached_so_a_retry_only_refetches_failures(
        monkeypatch, tmp_path):
    """Without a cache, every retry throws away the tiles that did work - which
    is the whole problem when the mirrors are rate-limiting."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    fetched = []

    class FakeResp:
        def __init__(self, payload):
            self._p = json.dumps(payload).encode()
        def read(self):
            return self._p
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None, context=None):
        fetched.append(req.full_url)
        return FakeResp({"elements": [
            {"type": "node", "id": len(fetched), "lat": 1.0, "lon": 1.0,
             "tags": {"name": "PD"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    sle.fetch_osm("MN", bbox=(0.0, 0.0, 3.0, 3.0), log=lambda *a: None)
    first = len(fetched)
    assert first == 9

    sle.fetch_osm("MN", bbox=(0.0, 0.0, 3.0, 3.0), log=lambda *a: None)
    assert len(fetched) == first                    # nothing refetched


def test_a_feature_on_a_tile_boundary_is_not_counted_twice(monkeypatch, tmp_path):
    """Tiles share edges, so the same way comes back from two of them."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    dup = {"type": "way", "id": 42, "center": {"lat": 1.0, "lon": 1.0},
           "tags": {"name": "Border Sheriff", "phone": "111"}}
    monkeypatch.setattr(sle, "_overpass_tile",
                        lambda tile, m, t, a, log, deadline=None, locks=None, start=0: ([dup], False))
    got = sle.fetch_osm("MN", bbox=(0.0, 0.0, 9.0, 9.0), log=lambda *a: None)
    assert len(got) == 1                            # nine tiles, one feature
    assert got[0]["phone"] == "111"


def test_the_misconfigured_mirror_is_not_retried():
    """overpass.osm.jp serves a certificate invalid for its own hostname, so
    every request there fails TLS. Keeping it only burns a retry."""
    assert not any("osm.jp" in m for m in sle.OVERPASS_MIRRORS)
    assert len(sle.OVERPASS_MIRRORS) >= 2


# --------------------------------------------------------------------------
# "__" separates a pack's identity from its version, so an install can retire
# older editions of the same pack without mistaking a sibling for one.
# --------------------------------------------------------------------------
def test_pack_filenames_carry_an_identity_version_boundary(stubbed, tmp_path):
    r = bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-16")
    name = os.path.basename(r["path"])
    assert "__" in name, name
    family, _, version = name.partition("__")
    assert family == "MN_Counties"                  # what the pack IS
    assert version.endswith(".kmz") and version != ".kmz"   # which edition


def test_two_builds_differ_only_after_the_boundary(monkeypatch, tmp_path):
    """Same pack, different day: the family must be identical so the installer
    can see the second as superseding the first."""
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print:
                        (_fake_counties(1), "http://e/1", "Current"))
    monkeypatch.setattr(bcp, "fetch_acs", lambda sfp, year=2023, log=print, **kw: {})
    a = os.path.basename(bcp.build_state("MN", str(tmp_path), log=lambda *a: None,
                                         today="2026-09-16")["path"])
    b = os.path.basename(bcp.build_state("MN", str(tmp_path), log=lambda *a: None,
                                         today="2026-09-17")["path"])
    assert a != b                                   # a new edition, not the same file
    assert a.split("__")[0] == b.split("__")[0] == "MN_Counties"


def test_per_county_files_are_versioned_too(stubbed, tmp_path):
    bcp.build_state("MN", str(tmp_path), per_county=True, log=lambda *a: None,
                    today="2026-09-16")
    per = [p.name for p in tmp_path.glob("MN_County0*.kmz")]
    assert per and all("__" in n for n in per), per


def test_sector_pack_names_have_no_version_boundary():
    """A sector pack carries no edition, so nothing may be retired for it -
    otherwise MN_Water would read as a newer MN_Energy-Electric."""
    for name in ("MN_Energy-Electric.kmz", "MN_Water.kmz", "SAMPLE_MN_Water.kmz"):
        assert "__" not in name


# --------------------------------------------------------------------------
# Wall-clock budgets. A fetch is allowed to be slow; it is not allowed to look
# hung. Retries multiply - tries x socket timeout x mirrors - and the first
# version of the tiled Overpass fetch worked out to 2.7 hours of silence.
# --------------------------------------------------------------------------
def _expired(seconds=60):
    d = sle.Deadline(seconds)
    d.start -= seconds + 1          # as if it had been running past its budget
    return d


def test_deadline_reports_what_it_has_left():
    d = sle.Deadline(60)
    assert not d.expired()
    assert 0 < d.left() <= 60
    assert d.elapsed() >= 0
    assert _expired().expired()


def test_overpass_tile_will_not_start_a_request_past_the_deadline(monkeypatch, tmp_path):
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))     # nothing cached
    import urllib.request

    def never(*a, **kw):
        raise AssertionError("a request was made after the budget ran out")

    monkeypatch.setattr(urllib.request, "urlopen", never)
    with pytest.raises(TimeoutError):
        sle._overpass_tile((-93.0, 45.0, -92.0, 46.0), ["https://example.invalid"],
                           90, 3, lambda *a: None, deadline=_expired())


def test_overpass_socket_never_outlives_the_remaining_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    import urllib.request
    seen = []

    def capture(req, timeout=None, **kw):
        seen.append(timeout)
        raise OSError("nope")

    monkeypatch.setattr(urllib.request, "urlopen", capture)
    with pytest.raises(RuntimeError):
        sle._overpass_tile((-93.0, 45.0, -92.0, 46.0), ["https://example.invalid"],
                           900, 1, lambda *a: None, deadline=sle.Deadline(10))
    # asked for a 900s socket, budget says 10 - the budget wins
    assert seen and all(0 < t <= 10 for t in seen), seen


def test_fetch_osm_stops_at_the_budget_and_says_so(monkeypatch):
    """Out of time must not read as 'no police stations in those counties'."""
    bbox = (-97.0, 43.0, -89.0, 49.0)
    tiles = sle.tile_bbox(bbox)
    lock, calls = __import__("threading").Lock(), []

    def tile(t, mirrors, timeout, attempts, log, deadline=None,
             locks=None, start=0):
        with lock:
            calls.append(t)
            n = len(calls)
        if n > 2:
            raise TimeoutError("deadline reached")
        return ([], False)

    monkeypatch.setattr(sle, "_overpass_tile", tile)
    with pytest.raises(RuntimeError) as ex:
        sle.fetch_osm("MN", bbox=bbox, log=lambda *a: None)
    msg = str(ex.value)
    # out of budget is reported as failure, and as a budget problem
    assert f"{len(tiles) - 2} of {len(tiles)} tiles failed" in msg, msg
    assert "ran out of the" in msg and "--deadline" in msg, msg
    # running out of budget is not a tile the mirrors refused: do not split it
    assert not any("q1" in str(c) for c in calls)
    assert len(calls) == len(tiles), calls


def test_running_out_of_budget_does_not_trigger_a_split(monkeypatch, tmp_path):
    """Splitting spends budget. Splitting because the budget is gone is absurd."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    bbox = (0.0, 0.0, 9.0, 9.0)
    quarters = {q for t in sle.tile_bbox(bbox) for q in sle.split_tile(t)}
    seen, lock = [], __import__("threading").Lock()

    def tile(t, mirrors, timeout, attempts, log, deadline=None,
             locks=None, start=0):
        with lock:
            seen.append(t)
        raise TimeoutError("deadline reached")

    monkeypatch.setattr(sle, "_overpass_tile", tile)
    with pytest.raises(RuntimeError):
        sle.fetch_osm("MN", bbox=bbox, log=lambda *a: None)
    assert not [t for t in seen if t in quarters], "split a tile that ran out of time"


def test_fetch_osm_passes_one_shared_clock_to_every_tile(monkeypatch):
    clocks, lock = [], __import__("threading").Lock()

    def tile(t, mirrors, timeout, attempts, log, deadline=None,
             locks=None, start=0):
        with lock:
            clocks.append(deadline)
        return ([], True)

    monkeypatch.setattr(sle, "_overpass_tile", tile)
    sle.fetch_osm("MN", bbox=(-97.0, 43.0, -89.0, 49.0), log=lambda *a: None)
    assert clocks and all(c is clocks[0] for c in clocks)
    assert isinstance(clocks[0], sle.Deadline)


def test_osm_deadline_is_reachable_from_the_command_line():
    """A budget nobody can change is a budget that blocks somebody."""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        sle.main(["--help"])
    out = buf.getvalue()
    assert "--deadline" in out and "--osm-attempts" in out


def test_http_get_gives_up_on_its_budget_without_sleeping_past_it(monkeypatch):
    # register the attribute so monkeypatch restores it for the next test
    monkeypatch.setattr(bcp, "HTTP_BUDGET_S", bcp.HTTP_BUDGET_S)
    slept = []
    monkeypatch.setattr(bcp.time, "sleep", lambda s: slept.append(s))

    def slow(*a, **kw):
        raise OSError("timed out")

    monkeypatch.setattr(bcp, "urlopen", slow)
    with pytest.raises(RuntimeError) as ex:
        bcp.http_get("https://example.invalid/x", budget=0.01,
                     log=lambda *a: None)
    assert not slept, "slept past a budget that was already spent"
    assert "budget 0.01s" in str(ex.value)


def test_http_get_clamps_the_socket_to_the_budget(monkeypatch):
    monkeypatch.setattr(bcp, "HTTP_BUDGET_S", bcp.HTTP_BUDGET_S)
    seen = []

    def capture(req, timeout=None, **kw):
        seen.append(timeout)
        raise OSError("nope")

    monkeypatch.setattr(bcp, "urlopen", capture)
    monkeypatch.setattr(bcp.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        bcp.http_get("https://example.invalid/x", timeout=600, budget=8,
                     log=lambda *a: None)
    assert seen and all(0 < t <= 8 for t in seen), seen


def test_http_get_names_the_host_that_stalled_before_retrying(monkeypatch):
    monkeypatch.setattr(bcp, "HTTP_BUDGET_S", bcp.HTTP_BUDGET_S)
    monkeypatch.setattr(bcp, "urlopen",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(bcp.time, "sleep", lambda s: None)
    lines = []
    with pytest.raises(RuntimeError):
        bcp.http_get("https://tigerweb.geo.census.gov/x", budget=60,
                     log=lines.append)
    assert lines, "a retry printed nothing - that is what 'hung' looks like"
    assert "tigerweb.geo.census.gov" in lines[0]
    assert "retry 2/" in lines[0]


def test_http_get_never_prints_a_key_while_reporting_a_failure(monkeypatch):
    monkeypatch.setattr(bcp, "HTTP_BUDGET_S", bcp.HTTP_BUDGET_S)
    monkeypatch.setattr(bcp, "urlopen",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(bcp.time, "sleep", lambda s: None)
    lines = []
    with pytest.raises(RuntimeError) as ex:
        bcp.http_get("https://api.census.gov/data/2023/acs/acs5",
                     {"key": "deadbeefcafe0000deadbeefcafe0000deadbeef"},
                     budget=60, log=lines.append)
    assert "deadbeefcafe" not in str(ex.value)
    assert not any("deadbeefcafe" in l for l in lines)


def test_http_budget_flag_reaches_every_request(monkeypatch):
    monkeypatch.setattr(bcp, "HTTP_BUDGET_S", bcp.HTTP_BUDGET_S)
    monkeypatch.setattr(bcp, "probe", lambda *a, **kw: True)
    assert bcp.main(["--probe", "--http-budget", "7"]) == 0
    assert bcp.HTTP_BUDGET_S == 7


# --------------------------------------------------------------------------
# Speed. A fetch that does real work is allowed to take time; doing the same
# work nine times in a row, or downloading the same boundaries twice, is not.
# --------------------------------------------------------------------------
def test_tiles_are_fetched_concurrently_not_one_after_another(monkeypatch, tmp_path):
    import threading
    import time
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    live, peak, lock = [0], [0], threading.Lock()

    def tile(t, mirrors, timeout, attempts, log, deadline=None, locks=None, start=0):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05)
        with lock:
            live[0] -= 1
        return ([], False)

    monkeypatch.setattr(sle, "_overpass_tile", tile)
    t0 = time.monotonic()
    sle.fetch_osm("MN", bbox=(0.0, 0.0, 9.0, 9.0), log=lambda *a: None, jobs=3)
    elapsed = time.monotonic() - t0
    assert peak[0] > 1, "tiles were fetched one at a time"
    assert elapsed < 9 * 0.05, f"no faster than serial ({elapsed:.2f}s)"


def test_a_mirror_only_takes_one_of_our_requests_at_a_time(monkeypatch, tmp_path):
    """The public instances ask for that, and queueing behind yourself is not
    faster anyway."""
    import threading
    import time
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    mirrors = ["https://a.invalid/i", "https://b.invalid/i", "https://c.invalid/i"]
    live, peak, lock = {}, {}, threading.Lock()

    class Resp:
        def read(self):
            return b'{"elements": []}'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None, context=None):
        host = req.full_url
        with lock:
            live[host] = live.get(host, 0) + 1
            peak[host] = max(peak.get(host, 0), live[host])
        time.sleep(0.05)
        with lock:
            live[host] -= 1
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    sle.fetch_osm("MN", bbox=(0.0, 0.0, 9.0, 9.0), mirrors=mirrors,
                  log=lambda *a: None, jobs=3)
    assert peak, "no mirror was used"
    assert max(peak.values()) == 1, peak


def test_results_come_back_in_tile_order_however_the_mirrors_answer(monkeypatch, tmp_path):
    """pick_sheriffs keeps the first match per county. If thread completion
    order reached it, the same data would pick a different agency per run."""
    import time
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    tiles = sle.tile_bbox((0.0, 0.0, 9.0, 9.0))
    index = {t: i for i, t in enumerate(tiles)}

    def tile(t, mirrors, timeout, attempts, log, deadline=None, locks=None, start=0):
        i = index[t]
        time.sleep(0.01 * (len(tiles) - i))     # later tiles finish FIRST
        return ([{"type": "node", "id": i, "lat": 1.0, "lon": 1.0,
                  "tags": {"name": f"PD {i}"}}], False)

    monkeypatch.setattr(sle, "_overpass_tile", tile)
    got = sle.fetch_osm("MN", bbox=(0.0, 0.0, 9.0, 9.0), log=lambda *a: None, jobs=4)
    assert [r["name"] for r in got] == [f"PD {i}" for i in range(len(tiles))]


def test_concurrent_tiles_do_not_all_start_on_the_same_mirror(monkeypatch, tmp_path):
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    mirrors = ["https://a.invalid/i", "https://b.invalid/i", "https://c.invalid/i"]
    first = []

    def fake_urlopen(req, timeout=None, context=None):
        first.append(req.full_url)
        raise OSError("no")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    for i in range(3):
        try:
            sle._overpass_tile((float(i), 0.0, float(i) + 1, 1.0), mirrors, 5, 1,
                               lambda *a: None, start=i)
        except RuntimeError:
            pass
    assert len({first[0], first[3], first[6]}) == 3, first


def test_split_tile_covers_its_parent_exactly():
    parent = (-97.0, 43.0, -89.0, 49.0)
    q = sle.split_tile(parent)
    assert len(q) == 4
    assert min(t[0] for t in q) == parent[0] and max(t[2] for t in q) == parent[2]
    assert min(t[1] for t in q) == parent[1] and max(t[3] for t in q) == parent[3]
    # every quarter is a quarter of the area, and they do not overlap
    area = lambda t: (t[2] - t[0]) * (t[3] - t[1])
    assert abs(sum(area(t) for t in q) - area(parent)) < 1e-9


def test_county_boundaries_are_downloaded_once_not_twice(monkeypatch, tmp_path):
    """87 polygons is the biggest download the seeder makes. It was being
    fetched for the bounding box AND again for the spatial match."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    import build_county_pack as _bcp
    hits = []

    def fake_fetch(sfp, ep=None, alts=None, log=print):
        hits.append(sfp)
        return ([{"properties": {"GEOID": "27025"},
                  "geometry": {"type": "Polygon",
                               "coordinates": [[[0, 0], [5, 0], [5, 5], [0, 5], [0, 0]]]}}],
                "http://e/1", "Current")

    monkeypatch.setattr(_bcp, "fetch_counties", fake_fetch)
    a = sle.county_shapes("27", log=lambda *x: None)
    b = sle.county_shapes("27", log=lambda *x: None)
    assert len(hits) == 1, hits
    assert a == b


def test_a_stale_shape_cache_is_refetched_not_trusted(monkeypatch, tmp_path):
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    import build_county_pack as _bcp
    hits = []

    def fake_fetch(sfp, ep=None, alts=None, log=print):
        hits.append(sfp)
        return ([{"properties": {"GEOID": "27025"},
                  "geometry": {"type": "Polygon",
                               "coordinates": [[[0, 0], [5, 0], [5, 5], [0, 5], [0, 0]]]}}],
                "http://e/1", "Current")

    monkeypatch.setattr(_bcp, "fetch_counties", fake_fetch)
    sle.county_shapes("27", log=lambda *x: None)
    # age it past the TTL
    path = sle._shapes_cache_path("27")
    doc = json.loads(open(path, encoding="utf-8").read())
    doc["fetched"] = "2000-01-01"
    open(path, "w", encoding="utf-8").write(json.dumps(doc))
    sle.county_shapes("27", log=lambda *x: None)
    assert len(hits) == 2, "a years-old boundary cache was used"

    # --refresh-shapes ignores a fresh cache too
    sle.county_shapes("27", log=lambda *x: None, refresh=True)
    assert len(hits) == 3


def test_a_corrupt_cache_file_is_refetched_not_read_as_empty(monkeypatch, tmp_path):
    """A truncated tile file reads back as 'no police stations here', which is
    indistinguishable from a county that genuinely has none."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    tile = (0.0, 0.0, 1.0, 1.0)
    path = sle._tile_cache_path(tile)
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('[{"type": "node", "id": 1')            # truncated mid-write

    class Resp:
        def read(self):
            return b'{"elements": [{"type": "node", "id": 9, "lat": 1, "lon": 1}]}'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None, context=None: Resp())
    els, from_cache = sle._overpass_tile(tile, ["https://x.invalid/i"], 5, 1,
                                         lambda *a: None)
    assert not from_cache and [e["id"] for e in els] == [9]
    # and the repaired cache is complete this time
    assert json.loads(open(path, encoding="utf-8").read())[0]["id"] == 9


def test_the_cache_is_written_atomically_leaving_no_temp_files(monkeypatch, tmp_path):
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    sle._write_cache(sle._tile_cache_path((0.0, 0.0, 1.0, 1.0)), [{"id": 1}])
    assert not [p for p in os.listdir(str(tmp_path)) if p.endswith(".tmp")]


def test_a_partial_result_names_the_counties_it_costs(monkeypatch, tmp_path):
    """'Some counties will be empty' is not actionable. Which ones is."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    bbox = (0.0, 0.0, 9.0, 9.0)
    dead = sle.tile_bbox(bbox)[0]
    shapes = [("27001", [[[[0.1, 0.1], [1.0, 0.1], [1.0, 1.0], [0.1, 1.0], [0.1, 0.1]]]]),
              ("27099", [[[[8.0, 8.0], [8.9, 8.0], [8.9, 8.9], [8.0, 8.9], [8.0, 8.0]]]])]
    monkeypatch.setattr(sle, "_overpass_tile", _one_bad_tile(dead))
    with pytest.raises(RuntimeError) as ex:
        sle.fetch_osm("MN", bbox=bbox, shapes=shapes, log=lambda *a: None)
    msg = str(ex.value)
    assert "27001" in msg, msg            # under the dead tile
    assert "27099" not in msg, msg        # nowhere near it


def test_a_tile_served_as_quarters_is_not_asked_for_again(monkeypatch, tmp_path):
    """It is the one tile the mirrors would not serve. Re-requesting it every
    run means every run pays its timeout, cache or no cache."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    parent = (0.0, 0.0, 2.0, 2.0)
    for j, q in enumerate(sle.split_tile(parent), 1):
        sle._write_cache(sle._tile_cache_path(q),
                         [{"type": "node", "id": j, "lat": 1.0, "lon": 1.0}])

    def never(*a, **kw):
        raise AssertionError("went to the network for a tile that is cached")

    monkeypatch.setattr("urllib.request.urlopen", never)
    els, from_cache = sle._overpass_tile(parent, ["https://x.invalid/i"], 5, 1,
                                         lambda *a: None)
    assert from_cache
    assert sorted(e["id"] for e in els) == [1, 2, 3, 4]


def test_a_timeout_is_told_apart_from_a_refusal():
    """The split threshold counts timeouts only. A 504 or a TLS failure is a
    mirror problem, not a sign the box is too big - counting it would split
    tiles that were never too big and waste the budget."""
    import socket
    import ssl as _ssl
    import urllib.error as ue
    timeouts = [socket.timeout("The read operation timed out"),
                ue.URLError(socket.timeout("timed out"))]
    refusals = [ue.HTTPError("u", 504, "Gateway Timeout", {}, None),
                ue.URLError(ConnectionRefusedError(111, "refused")),
                ue.URLError(socket.gaierror(-2, "Name or service not known")),
                ue.URLError(_ssl.SSLCertVerificationError("Hostname mismatch"))]
    assert all(sle._is_timeout(e) for e in timeouts)
    assert not any(sle._is_timeout(e) for e in refusals)


def test_two_timeouts_stop_a_tile_but_two_refusals_do_not(monkeypatch, tmp_path):
    import socket
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    mirrors = ["https://a.invalid/i", "https://b.invalid/i", "https://c.invalid/i"]

    for err, expected in ((socket.timeout("timed out"), sle.OSM_TIMEOUTS_BEFORE_SPLIT),
                          (OSError("connection refused"), len(mirrors))):
        tries = []

        def boom(req, timeout=None, context=None, _e=err):
            tries.append(req.full_url)
            raise _e

        monkeypatch.setattr("urllib.request.urlopen", boom)
        with pytest.raises(RuntimeError):
            sle._overpass_tile((0.0, 0.0, 1.0, 1.0), mirrors, 5, 1, lambda *a: None)
        assert len(tries) == expected, (err, tries)


def test_the_budget_running_out_is_never_counted_as_the_box_being_too_big(
        monkeypatch, tmp_path):
    """Both are TimeoutError. Only one of them means 'split this tile'."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))

    def never(*a, **kw):
        raise AssertionError("a request was made after the budget ran out")

    monkeypatch.setattr("urllib.request.urlopen", never)
    with pytest.raises(TimeoutError):           # not RuntimeError("timed out on N mirrors")
        sle._overpass_tile((0.0, 0.0, 1.0, 1.0), ["https://a.invalid/i"], 5, 1,
                           lambda *a: None, deadline=_expired())


def test_only_the_budget_ever_escapes_as_a_timeouterror(monkeypatch, tmp_path):
    """Load-bearing invariant: the caller tells 'out of budget' apart from
    'this box was too big' with a plain isinstance, and acts differently on
    each. A socket timeout leaking out as TimeoutError would stop the split."""
    import socket
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    for err in (socket.timeout("timed out"), OSError("refused"),
                RuntimeError("504")):
        monkeypatch.setattr("urllib.request.urlopen",
                            lambda req, timeout=None, context=None, _e=err:
                            (_ for _ in ()).throw(_e))
        with pytest.raises(RuntimeError) as ex:
            sle._overpass_tile((0.0, 0.0, 1.0, 1.0), ["https://a.invalid/i"],
                               5, 1, lambda *a: None)
        assert not isinstance(ex.value, TimeoutError), err


# --------------------------------------------------------------------------
# The gap report. "52 of 87" is a number, not a diagnosis: a county with no
# sheriff is either a filter problem or an absence, and those need opposite
# responses.
# --------------------------------------------------------------------------
def _gap_lines(capsys, **kw):
    sle.report_gaps(**kw)
    return capsys.readouterr().out


def test_gap_report_separates_a_filter_problem_from_an_absence(capsys):
    names = {"27001": "Aitkin County", "27003": "Anoka County",
             "27005": "Becker County"}
    records = [
        {"geoid": "27001", "agency": "Aitkin County Sheriff", "phone": "1",
         "website": ""},
        # has agencies, but none of them say "sheriff" - widening --match helps
        {"geoid": "27003", "agency": "Blaine Police Department", "phone": "",
         "website": ""},
        {"geoid": "27003", "agency": "Coon Rapids Police Dept", "phone": "",
         "website": ""},
        # 27005 has nothing at all - no filter fixes that
    ]
    chosen = {"27001": records[0]}
    out = _gap_lines(capsys, state="MN", geoids=list(names), names=names,
                     records=records, chosen=chosen, match="sheriff")
    assert "have records, none matched the filter : 1" in out
    assert "Blaine Police Department" in out          # what they are REALLY called
    assert "Anoka County" in out
    assert "no law-enforcement record at all      : 1" in out
    assert "Becker County" in out
    assert "widening --match" in out


def test_gap_report_counts_phone_and_website_separately(capsys):
    names = {"27001": "A County", "27003": "B County", "27005": "C County"}
    records = [
        {"geoid": "27001", "agency": "A County Sheriff", "phone": "651-555-0100",
         "website": "https://example.gov/sheriff"},
        {"geoid": "27003", "agency": "B County Sheriff", "phone": "",
         "website": "https://example.gov/b"},
        {"geoid": "27005", "agency": "C County Sheriff", "phone": "", "website": ""},
    ]
    chosen = {r["geoid"]: r for r in records}
    out = _gap_lines(capsys, state="MN", geoids=list(names), names=names,
                     records=records, chosen=chosen, match="sheriff")
    assert "carrying a phone number : 1" in out
    assert "carrying a website      : 2" in out
    assert "none matched the filter : 0" in out
    assert "record at all      : 0" in out


def test_gap_report_names_counties_it_has_no_name_for_by_geoid(capsys):
    """A shape cache written before names were stored has none. A missing name
    is a missing name - it must not turn into a wrong one or a crash."""
    records = [{"geoid": "27999", "agency": "Somewhere PD", "phone": "",
                "website": ""}]
    out = _gap_lines(capsys, state="MN", geoids=["27999"], names={},
                     records=records, chosen={}, match="sheriff")
    assert "27999" in out


def test_osm_keeps_the_website_tag_in_both_spellings(monkeypatch, tmp_path):
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    els = [
        {"type": "node", "id": 1, "lat": 1.0, "lon": 1.0,
         "tags": {"name": "A", "website": "https://a.example"}},
        {"type": "node", "id": 2, "lat": 1.1, "lon": 1.1,
         "tags": {"name": "B", "contact:website": "https://b.example"}},
        {"type": "node", "id": 3, "lat": 1.2, "lon": 1.2, "tags": {"name": "C"}},
    ]
    monkeypatch.setattr(sle, "_overpass_tile",
                        lambda tile, m, t, a, log, deadline=None, locks=None,
                        start=0: (els, False))
    got = sle.fetch_osm("MN", bbox=(0.0, 0.0, 9.0, 9.0), log=lambda *a: None)
    assert [r["website"] for r in got] == ["https://a.example",
                                           "https://b.example", ""]


def test_the_shape_cache_carries_the_county_names(monkeypatch, tmp_path):
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    import build_county_pack as _bcp
    monkeypatch.setattr(_bcp, "fetch_counties", lambda sfp, ep=None, alts=None,
                        log=print: ([{
                            "properties": {"GEOID": "27025", "NAME": "Chisago County"},
                            "geometry": {"type": "Polygon",
                                         "coordinates": [[[0, 0], [5, 0], [5, 5],
                                                          [0, 5], [0, 0]]]}}],
                                    "http://e/1", "Current"))
    shapes, names = sle.county_shapes("27", log=lambda *a: None, with_names=True)
    assert names == {"27025": "Chisago County"}
    # and it survives the round trip through the cache
    shapes2, names2 = sle.county_shapes("27", log=lambda *a: None, with_names=True)
    assert names2 == names and shapes2 == shapes


def test_gap_report_counts_counties_that_have_no_record_at_all(capsys):
    """The regression that made this report useless: the county list was taken
    from the records, so a county with no records could not appear in it and
    'no law-enforcement record at all' was structurally always 0. It reported
    85 counties and 0 gaps for a state with 87 counties and 2 real ones."""
    geoids = [f"270{n:02d}" for n in range(1, 8)]          # 7 counties
    records = [{"geoid": "27001", "agency": "A County Sheriff", "phone": "",
                "website": ""}]
    out = _gap_lines(capsys, state="MN", geoids=geoids, names={},
                     records=records, chosen={"27001": records[0]},
                     match="sheriff")
    assert "GAP REPORT for MN - 7 counties" in out, out
    assert "no law-enforcement record at all      : 6" in out, out


def test_a_shape_cache_without_names_is_refetched_when_names_are_asked_for(
        monkeypatch, tmp_path):
    """Serving 'this state has no county names' out of an older cache is a
    wrong answer dressed as a cache hit."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    import build_county_pack as _bcp
    hits = []

    def fake(sfp, ep=None, alts=None, log=print):
        hits.append(sfp)
        return ([{"properties": {"GEOID": "27025", "NAME": "Chisago County"},
                  "geometry": {"type": "Polygon",
                               "coordinates": [[[0, 0], [5, 0], [5, 5], [0, 5], [0, 0]]]}}],
                "http://e/1", "Current")

    monkeypatch.setattr(_bcp, "fetch_counties", fake)
    sle.county_shapes("27", log=lambda *a: None)
    path = sle._shapes_cache_path("27")
    doc = json.loads(open(path, encoding="utf-8").read())
    del doc["names"]                                  # an older cache
    open(path, "w", encoding="utf-8").write(json.dumps(doc))

    _shapes, names = sle.county_shapes("27", log=lambda *a: None, with_names=True)
    assert names == {"27025": "Chisago County"}
    assert len(hits) == 2

    # a cache that legitimately has no names must NOT refetch forever
    doc = json.loads(open(path, encoding="utf-8").read())
    doc["names"] = {}
    open(path, "w", encoding="utf-8").write(json.dumps(doc))
    sle.county_shapes("27", log=lambda *a: None, with_names=True)
    sle.county_shapes("27", log=lambda *a: None, with_names=True)
    assert len(hits) == 2


def test_a_jail_does_not_outrank_the_sheriff_just_by_arriving_first():
    """With a widened --match a county matches on several records. Order is
    tile order, which has nothing to do with which one is the sheriff."""
    recs = [
        {"geoid": "27005", "agency": "Becker County Jail", "phone": "", "website": ""},
        {"geoid": "27005", "agency": "Becker County Sheriff", "phone": "", "website": ""},
    ]
    m = "sheriff|jail"
    assert sle.pick_sheriffs(recs, m)["27005"]["agency"] == "Becker County Sheriff"
    assert sle.pick_sheriffs(list(reversed(recs)), m)["27005"]["agency"] \
        == "Becker County Sheriff"


def test_a_phone_still_wins_between_two_records_of_the_same_kind():
    recs = [
        {"geoid": "27005", "agency": "X County Sheriff", "phone": "", "website": ""},
        {"geoid": "27005", "agency": "X County Sheriff Annex", "phone": "555",
         "website": ""},
    ]
    assert sle.pick_sheriffs(recs)["27005"]["phone"] == "555"


def test_a_sheriff_without_a_phone_still_beats_a_jail_with_one():
    """The name is what the field claims to be. A phone number on the wrong
    agency is worse than no phone number on the right one."""
    recs = [
        {"geoid": "27005", "agency": "Becker County Jail", "phone": "555",
         "website": ""},
        {"geoid": "27005", "agency": "Becker County Sheriff", "phone": "",
         "website": ""},
    ]
    got = sle.pick_sheriffs(recs, "sheriff|jail")["27005"]
    assert got["agency"] == "Becker County Sheriff" and got["phone"] == ""


def test_the_default_filter_tolerates_the_spelling_the_source_actually_uses():
    """OSM has "Steele County Sherriff's Office and Detention Center". That is
    the same word misspelled by whoever typed it, not a different agency."""
    recs = [{"geoid": "27147", "phone": "", "website": "",
             "agency": "Steele County Sherriff's Office and Detention Center"}]
    got = sle.pick_sheriffs(recs)
    assert "27147" in got
    # and it is written out exactly as the source spells it, typo included
    assert got["27147"]["agency"].startswith("Steele County Sherriff's")


def test_widening_is_suggested_from_the_names_that_actually_came_back():
    """A suggestion computed from this state's own unmatched names can only
    ever recommend a term that reaches a real county here."""
    unmatched = {
        "27131": ["Faribault Police Department", "Rice County Public Safety Center"],
        "27105": ["Adrian Police Department", "Prairie Justice Center"],
        "27001": ["Hill City Police Department"],          # nothing would help
    }
    got = dict(sle.suggest_widening(unmatched, "sherr?iff"))
    assert got.get("county public safety") == 1
    assert got.get("justice cent") == 1
    # a city PD is never a county agency, so no term is offered for it
    assert not any(re.search(p, "Hill City Police Department", re.I) for p in got)


def test_a_term_already_in_the_filter_is_not_suggested_again():
    unmatched = {"27007": ["Beltrami County Law Enforcement Center"]}
    assert sle.suggest_widening(unmatched, "sherr?iff")          # worth offering
    assert not sle.suggest_widening(
        unmatched, "sherr?iff|law enforcement cent")             # already on


def test_the_report_prints_a_runnable_widening_command(capsys):
    names = {"27131": "Rice County", "27001": "Aitkin County"}
    records = [
        {"geoid": "27131", "agency": "Rice County Public Safety Center",
         "phone": "", "website": ""},
        {"geoid": "27001", "agency": "Hill City Police Department",
         "phone": "", "website": ""},
    ]
    out = _gap_lines(capsys, state="MN", geoids=list(names), names=names,
                     records=records, chosen={}, match="sherr?iff")
    assert "would reach 1 of those 2 counties" in out, out
    assert "--match 'sherr?iff|county public safety'" in out, out
    assert "python3 seed_le_contacts.py --state MN --gaps" in out, out
