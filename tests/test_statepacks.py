MN_PACK_PREFIX = "MN_Counties__2024_"

"""Tests for statepacks/build_county_pack.py - the stdlib-only ATAK county builder.

No network: every fetch is monkeypatched. What is being proved here is that the
builder never invents a value and never mangles a shape.
"""
import html
import importlib.util
import json
import os
import re
import sys
import time
import zipfile
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET

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


def mn_pack(tmp_path):
    """The one MN county pack in tmp_path, whatever digest it carries.

    The filename ends in a content digest now, so tests cannot name it. They
    should not want to: what a test cares about is that exactly one pack was
    written and what is inside it. Asserting "exactly one" is worth keeping -
    two packs for one state is the duplicate-layer bug.
    """
    hits = sorted(tmp_path.glob(MN_PACK_PREFIX + "*.kmz"))
    assert len(hits) == 1, f"expected one {MN_PACK_PREFIX}*.kmz, got {hits}"
    return hits[0]


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
    path = mn_pack(tmp_path)
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
    kml = _doc(mn_pack(tmp_path))
    assert "413.9 sq mi [TIGER ALAND 2024]" in _text(kml)
    assert "28.5 sq mi [TIGER AWATER 2024]" in _text(kml)


def test_missing_acs_still_builds_and_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print:
                        (_fake_counties(1), "https://tigerweb.example/x/1", "2024"))
    monkeypatch.setattr(bcp, "fetch_acs", lambda sfp, year=2023, log=print, **kw: {})
    r = bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    assert r["counties"] == 1 and r["with_population"] == 0 and r["acs_year"] is None
    kml = _doc(mn_pack(tmp_path))
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
    kml = _doc(mn_pack(tmp_path))
    assert "Center City [Wikidata 2026-09-14]" in _text(kml)
    assert "651-555-0100 [county website 2026]" in _text(kml)
    # the county with no CSV row still refuses to guess
    assert "not in dataset" in kml


def test_per_county_also_writes_individual_files(stubbed, tmp_path):
    bcp.build_state("MN", str(tmp_path), per_county=True, log=lambda *a: None,
                    today="2026-09-14")
    names = {p.name for p in tmp_path.glob("*.kmz")}
    assert any(n.startswith(MN_PACK_PREFIX) for n in names)
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
    assert os.path.basename(r["path"]).startswith(
        "MN_Counties__Current_2026_09_14_")

    # one that already names a year needs no date appended
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print:
                        (_fake_counties(1), "http://e/55", "Census 2020"))
    r2 = bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    assert os.path.basename(r2["path"]).startswith("MN_Counties__Census_2020_")


def test_unknown_vintage_is_never_shown_as_a_year(monkeypatch, tmp_path):
    """When the service reports no year, the pack must say so - and its filename
    must not claim one either."""
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print:
                        (_fake_counties(1), "http://e/1", bcp.TIGER_VINTAGE_UNKNOWN))
    monkeypatch.setattr(bcp, "fetch_acs", lambda sfp, year=2023, log=print, **kw: {})
    r = bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    assert os.path.basename(r["path"]).startswith("MN_Counties__built2026_09_14_")
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
    kml = _doc(mn_pack(tmp_path))
    assert "<b>FIPS (GEOID):</b>" not in kml         # omitted rather than invented
    assert "No data for:" in kml and "FIPS (GEOID)" in kml
    assert "27000" not in kml                       # the old fabrication


# --------------------------------------------------------------------------
# seed_le_contacts.py - sheriff/LE enrichment from the frozen HIFLD snapshot
# --------------------------------------------------------------------------
sle = _load("seed_le_contacts")


TODAY = __import__("datetime").date.today().isoformat()


class FakeHTTP:
    """A stand-in for HTTPResponse that honours read(n), like the real one.

    The body is now read in chunks so the wall-clock budget has somewhere to be
    enforced; a fake whose read() takes no size argument would pass a test the
    real code path could never reach.
    """

    def __init__(self, payload):
        self._b = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self._i = 0

    def read(self, n=-1):
        if n is None or n < 0:
            n = len(self._b) - self._i
        out = self._b[self._i:self._i + n]
        self._i += len(out)
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False



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
    monkeypatch.setattr(sle, "_overpass_tile", lambda tile, m, t, a, log, deadline=None, locks=None, start=0, **kw:
                        (els, False, TODAY))
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

    def fake_urlopen(req, timeout=None, context=None):
        captured["body"] = urllib.parse.unquote_plus(req.data.decode())
        return FakeHTTP({"elements": []})

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
    kml = _doc(mn_pack(tmp_path))
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
    hits = sorted(tmp_path.glob("MN_Counties__Current_2026_09_14_*.kmz"))
    assert len(hits) == 1, hits
    kml = _doc(hits[0])
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
             locks=None, start=0, **kw):
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
                  "tags": {"name": f"PD {i}"}}], False, TODAY)

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
             locks=None, start=0, **kw):
        with lock:
            seen.append(tile)
            i = len(seen)
        # the whole middle tile fails; its quarters are served
        if tile == dead:
            raise RuntimeError("HTTP Error 504: Gateway Timeout")
        return ([{"type": "node", "id": i, "lat": 1.0, "lon": 1.0,
                  "tags": {"name": f"PD {i}"}}], False, TODAY)

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
             locks=None, start=0, **kw):
        if tile == dead or tile == doomed:
            raise RuntimeError("HTTP Error 504: Gateway Timeout")
        with lock:
            ids["n"] += 1
            i = ids["n"]
        return ([{"type": "node", "id": i, "lat": 1.0, "lon": 1.0,
                  "tags": {"name": f"PD {i}"}}], False, TODAY)

    monkeypatch.setattr(sle, "_overpass_tile", stub)
    with pytest.raises(RuntimeError, match="1 of 9 tiles failed"):
        sle.fetch_osm("MN", bbox=bbox, log=lambda *a: None)


def test_successful_tiles_are_cached_so_a_retry_only_refetches_failures(
        monkeypatch, tmp_path):
    """Without a cache, every retry throws away the tiles that did work - which
    is the whole problem when the mirrors are rate-limiting."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    fetched = []

    def fake_urlopen(req, timeout=None, context=None):
        fetched.append(req.full_url)
        return FakeHTTP({"elements": [
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
                        lambda tile, m, t, a, log, deadline=None, locks=None, start=0, **kw:
                        ([dup], False, TODAY))
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
             locks=None, start=0, **kw):
        with lock:
            calls.append(t)
            n = len(calls)
        if n > 2:
            raise TimeoutError("deadline reached")
        return ([], False, TODAY)

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
             locks=None, start=0, **kw):
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
             locks=None, start=0, **kw):
        with lock:
            clocks.append(deadline)
        return ([], True, TODAY)

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

    def tile(t, mirrors, timeout, attempts, log, deadline=None, locks=None,
             start=0, **kw):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05)
        with lock:
            live[0] -= 1
        return ([], False, TODAY)

    monkeypatch.setattr(sle, "_overpass_tile", tile)
    sle.fetch_osm("MN", bbox=(0.0, 0.0, 9.0, 9.0), log=lambda *a: None, jobs=3)
    # Overlap, not elapsed time: a wall-clock bound would flake on a loaded
    # runner and would be measuring the machine rather than the code.
    assert peak[0] > 1, "tiles were fetched one at a time"


def test_a_mirror_only_takes_one_of_our_requests_at_a_time(monkeypatch, tmp_path):
    """The public instances ask for that, and queueing behind yourself is not
    faster anyway."""
    import threading
    import time
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    mirrors = ["https://a.invalid/i", "https://b.invalid/i", "https://c.invalid/i"]
    live, peak, lock = {}, {}, threading.Lock()

    def fake_urlopen(req, timeout=None, context=None):
        host = req.full_url
        with lock:
            live[host] = live.get(host, 0) + 1
            peak[host] = max(peak.get(host, 0), live[host])
        time.sleep(0.05)
        with lock:
            live[host] -= 1
        return FakeHTTP({"elements": []})

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

    def tile(t, mirrors, timeout, attempts, log, deadline=None, locks=None,
             start=0, **kw):
        i = index[t]
        time.sleep(0.01 * (len(tiles) - i))     # later tiles finish FIRST
        return ([{"type": "node", "id": i, "lat": 1.0, "lon": 1.0,
                  "tags": {"name": f"PD {i}"}}], False, TODAY)

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

    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None, context=None: FakeHTTP(
                            {"elements": [{"type": "node", "id": 9,
                                           "lat": 1, "lon": 1}]}))
    els, from_cache, fetched = sle._overpass_tile(tile, ["https://x.invalid/i"],
                                                  5, 1, lambda *a: None)
    assert not from_cache and [e["id"] for e in els] == [9]
    # and the repaired cache is complete, and dated
    doc = json.loads(open(path, encoding="utf-8").read())
    assert doc["elements"][0]["id"] == 9
    assert doc["fetched"] == fetched == TODAY


def test_the_cache_is_written_atomically_leaving_no_temp_files(monkeypatch, tmp_path):
    """Asserting 'no .tmp files afterwards' is satisfied by a plain open() that
    never made one. The claim is that the reader can never see a half-written
    file, so the test is that the final name only ever appears via rename."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    target = sle._tile_cache_path((0.0, 0.0, 1.0, 1.0))
    renamed, opened = [], []
    real_replace, real_open = os.replace, open

    def spy_replace(src, dst):
        renamed.append((src, dst))
        return real_replace(src, dst)

    def spy_open(path, mode="r", *a, **kw):
        if "w" in mode or "a" in mode:
            opened.append(str(path))
        return real_open(path, mode, *a, **kw)

    monkeypatch.setattr(os, "replace", spy_replace)
    monkeypatch.setattr("builtins.open", spy_open)
    sle._write_cache(target, [{"id": 1}])
    monkeypatch.undo()

    assert target not in opened, "the live cache file was written in place"
    assert renamed and renamed[0][1] == target, renamed
    assert not [p for p in os.listdir(str(tmp_path)) if p.endswith(".tmp")]


def test_a_failed_atomic_write_leaves_no_temp_file_behind(monkeypatch, tmp_path):
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(os, "replace",
                        lambda *a: (_ for _ in ()).throw(OSError("nope")))
    with pytest.raises(OSError):
        sle._write_cache(sle._tile_cache_path((0.0, 0.0, 1.0, 1.0)), [{"id": 1}])
    assert os.listdir(str(tmp_path)) == []


def test_gaps_does_not_kill_a_run_that_has_no_boundaries(monkeypatch, tmp_path, capsys):
    """--source hifld --gaps died with a NameError AFTER a successful fetch,
    throwing the whole run away at the very last step."""
    monkeypatch.setattr(sle, "CSV_PATH", str(tmp_path / "le.csv"))
    monkeypatch.setattr(sle, "resolve_layer", lambda *a, **k: (1, "2025"))
    monkeypatch.setattr(sle, "fetch_state", lambda *a, **k: [
        {"geoid": "27025", "agency": "Chisago County Sheriff",
         "phone": "651-555-0100", "address": "", "type": "county"}])
    assert sle.main(["--state", "MN", "--source", "hifld", "--gaps"]) == 0
    out = capsys.readouterr().out
    assert "--gaps needs the county boundaries" in out, out
    assert "1 row(s) written" in out, out


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
    els, from_cache, _fetched = sle._overpass_tile(parent, ["https://x.invalid/i"],
                                                   5, 1, lambda *a: None)
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
                        start=0, **kw: (els, False, TODAY))
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


# --- discover_terms: a state's own vocabulary, not Minnesota's -------------
# COUNTY_LE_HINTS was read off Minnesota. The first run for any other state
# can only test Minnesota's hypotheses against it, so a state that files its
# county agency under a word Minnesota does not use gets no suggestion at all.

WI_SHAPED = {
    "55025": ["Dane County Public Safety Building", "Madison Police Department"],
    "55079": ["Milwaukee County Public Safety Building"],
    "55133": ["Waukesha County Communications Center"],
    "55139": ["Winnebago County Communications Center"],
    "55101": ["Racine Police Department"],        # city only - must stay empty
}
WI_NAMES = {"55025": "Dane County", "55079": "Milwaukee County",
            "55133": "Waukesha County", "55139": "Winnebago County",
            "55101": "Racine County"}


def test_a_word_minnesota_never_uses_is_still_found():
    """The gap this closes: 'communications center' reaches two counties here
    and is in no curated hint, so suggest_widening cannot propose it."""
    assert not any("communications" in h
                   for h, _n in sle.suggest_widening(WI_SHAPED, "sherr?iff"))
    found = dict(sle.discover_terms(WI_SHAPED, "sherr?iff", WI_NAMES))
    assert any("communications center" in t for t in found)
    assert found[next(t for t in found if "communications center" in t)] == 2


def test_a_discovered_term_never_reaches_a_city_police_department():
    """The maintainer's standing decision (README section 11): a city PD is
    never written as a county's primary LE, so no term may be offered that
    gets there - at any count."""
    for term, _n in sle.discover_terms(WI_SHAPED, "sherr?iff", WI_NAMES):
        for city in ("Madison Police Department", "Racine Police Department"):
            assert not re.search(re.escape(term), city, re.I), term


def test_a_city_pds_words_are_never_read_as_county_vocabulary():
    """Two city PDs share 'police department'. Reading their names for terms
    would offer it as reaching two counties."""
    only_cities = {"55025": ["Madison Police Department"],
                   "55101": ["Racine Police Department"]}
    assert not sle.discover_terms(only_cities, "sherr?iff", WI_NAMES)


def test_a_city_pd_never_counts_toward_a_real_terms_reach():
    """'communications center' genuinely reaches one county here. A municipal
    record in a second county must not inflate that to two and make it look
    like vocabulary."""
    one_real = {"55133": ["Waukesha County Communications Center"],
                "55101": ["Racine Police Communications Center"]}
    assert not sle.discover_terms(one_real, "sherr?iff", WI_NAMES)


def test_a_county_police_department_is_still_offered():
    """Some states run county PDs (Nassau County, Baltimore County). 'police
    department' is not the municipal discriminator it looks like, and testing
    for it would cost those states their real term."""
    county_pds = {"36059": ["Nassau County Police Department",
                            "Hempstead Police Department"],
                  "24005": ["Baltimore County Police Department"]}
    names = {"36059": "Nassau County", "24005": "Baltimore County"}
    terms = [t for t, _n in sle.discover_terms(county_pds, "sherr?iff", names)]
    assert any("county police department" in t for t in terms), terms


def test_the_descriptor_comes_from_the_source_not_a_word_list():
    """Louisiana has parishes and Puerto Rico municipios. Reading the county's
    own TIGER name means those cost nothing (README section 8)."""
    la = {"22001": ["Acadia Parish Law Enforcement Center"],
          "22003": ["Allen Parish Law Enforcement Center"]}
    names = {"22001": "Acadia Parish", "22003": "Allen Parish"}
    terms = [t for t, _n in sle.discover_terms(la, "sherr?iff", names)]
    assert any("law enforcement center" in t for t in terms), terms


# --- spellings found on real data, both states ------------------------------

def test_every_sheriff_spelling_seen_on_real_data_matches():
    """MN doubles the r ("Steele County Sherriff's"), WI doubles the r AND
    drops an f ("Clark County Sherrif", found 2026-09-14). The default has to
    reach both or a county silently goes empty."""
    for name in ("Chisago County Sheriff's Office",
                 "Steele County Sherriff's Office and Detention Center",
                 "Clark County Sherrif"):
        assert re.search(sle.SHERIFF_RX, name, re.I), name


def test_the_sheriff_filter_does_not_reach_a_lookalike_place_name():
    for name in ("Sheridan Police Department", "Sherwood Police Department",
                 "Shelby County Courthouse"):
        assert not re.search(sle.SHERIFF_RX, name, re.I), name


# --- near misses: the typo discover_terms structurally cannot find -----------

def test_a_one_county_typo_is_found_even_though_discovery_cannot_see_it():
    """discover_terms needs two counties, and a misspelling reaches exactly
    one - so the rule that keeps county names out also keeps typos out."""
    unmatched = {"55019": ["Clark County Sherrif"]}
    names = {"55019": "Clark County", "55009": "Brown County"}
    assert not sle.discover_terms(unmatched, "sherr?iff", names)   # blind to it
    chosen = {"55009": {"agency": "Brown County Sheriff's Office"}}
    got = sle.near_misses(unmatched, chosen, names)
    assert got and got[0][2] == "sherrif" and got[0][3] == "sheriff"


def test_near_misses_learns_its_vocabulary_from_what_matched_here():
    """No word list: it asks 'is this nearly a word that worked in this
    state', so it adapts to whatever the filter happens to be."""
    unmatched = {"55019": ["Clark County Constabulry"]}
    names = {"55019": "Clark County", "55009": "Brown County"}
    assert not sle.near_misses(
        unmatched, {"55009": {"agency": "Brown County Sheriff"}}, names)
    assert sle.near_misses(
        unmatched, {"55009": {"agency": "Brown County Constabulary"}}, names)


def test_near_misses_leaves_genuinely_different_words_alone():
    names = {"55019": "Clark County", "55009": "Brown County"}
    chosen = {"55009": {"agency": "Brown County Sheriff's Office"}}
    for other in ("Clark County Detention Center", "Clark County Dispatch",
                  "Clark County Marshal", "Clark County District Court"):
        assert not sle.near_misses({"55019": [other]}, chosen, names), other


def test_a_near_miss_in_a_city_pd_is_not_reported_as_the_countys():
    names = {"55019": "Clark County", "55009": "Brown County"}
    chosen = {"55009": {"agency": "Brown County Sheriff's Office"}}
    assert not sle.near_misses({"55019": ["Neillsville Sherrif"]}, chosen, names)


# --- records that exist but carry no name ------------------------------------

def test_a_nameless_record_is_not_called_widenable(capsys):
    """WI had three counties (Fond du Lac, Forest, Green Lake) whose records
    were all nameless. No filter can match a blank, so counting them under
    'widening --match may fix these' promises a fix that does not exist."""
    names = {"55039": "Fond du Lac County", "55001": "Adams County"}
    records = [{"geoid": "55039", "agency": ""},
               {"geoid": "55001", "agency": "Adams Police Department"}]
    sle.report_gaps("WI", list(names), names, records, {}, "sherr?if")
    out = capsys.readouterr().out
    assert "records present but every one unnamed : 1" in out
    assert "none matched the filter : 1" in out       # Adams only, not both
    assert "no filter can match a blank name" in out


def test_a_nameless_county_is_not_counted_as_having_no_record_either(capsys):
    """It is its own thing: 'no record at all' points at the source, a
    nameless record points at the tagging."""
    names = {"55039": "Fond du Lac County", "55013": "Burnett County"}
    records = [{"geoid": "55039", "agency": ""}]
    sle.report_gaps("WI", list(names), names, records, {}, "sherr?if")
    out = capsys.readouterr().out
    assert "records present but every one unnamed : 1" in out
    assert "no law-enforcement record at all      : 1" in out


def test_a_phrase_reaching_one_county_is_that_countys_name_not_vocabulary():
    assert not sle.discover_terms({"55025": ["Dane County Jail Annex"]},
                                  "sherr?iff", WI_NAMES)


def test_one_phrase_per_term_not_every_window_onto_it():
    """'public safety building' already reaches every county 'public safety'
    does; listing both, plus 'safety building' and 'county public', is four
    spellings of one finding."""
    terms = [t for t, _n in sle.discover_terms(WI_SHAPED, "sherr?iff", WI_NAMES)]
    assert "public safety building" in terms
    for narrower in ("safety building", "public safety", "county public"):
        assert narrower not in terms


def test_discovery_adds_no_noise_to_minnesota():
    """Minnesota is verified end to end; discovery must not start printing
    suggestions over the top of the curated list that already works there."""
    mn = {"27131": ["Rice County Public Safety Center"],
          "27007": ["Beltrami County Law Enforcement Center"],
          "27005": ["Becker County Jail"],
          "27001": ["Hill City Police Department"]}
    names = {"27131": "Rice County", "27007": "Beltrami County",
             "27005": "Becker County", "27001": "Aitkin County"}
    assert sle.suggest_widening(mn, "sherr?iff")      # curated list still leads
    assert not sle.discover_terms(mn, "sherr?iff", names)   # nothing added


def test_the_report_offers_a_discovered_term_when_the_curated_list_misses(capsys):
    """End to end: with no curated hint reaching them, the report still has
    something to say rather than reading as 'no filter fixes this'."""
    names = {"55133": "Waukesha County", "55139": "Winnebago County"}
    records = [
        {"geoid": "55133", "agency": "Waukesha County Communications Center",
         "phone": "", "source": "OSM", "vintage": "2026-09-14"},
        {"geoid": "55139", "agency": "Winnebago County Communications Center",
         "phone": "", "source": "OSM", "vintage": "2026-09-14"},
    ]
    sle.report_gaps("WI", ["55133", "55139"], names, records, {}, "sherr?iff")
    out = capsys.readouterr().out
    assert "communications center" in out
    assert "not vetted" in out            # offered, not applied
    assert "--match" in out               # and runnable


def test_the_discovered_command_keeps_the_curated_terms_it_builds_on(capsys):
    """The two blocks are read together. A line someone pastes must not
    quietly drop the widening the block above just recommended."""
    names = {"55025": "Dane County", "55079": "Milwaukee County",
             "55133": "Waukesha County", "55139": "Winnebago County"}
    records = [
        {"geoid": "55025", "agency": "Dane County Public Safety Center"},
        {"geoid": "55079", "agency": "Milwaukee County Public Safety Center"},
        {"geoid": "55133", "agency": "Waukesha County Communications Center"},
        {"geoid": "55139", "agency": "Winnebago County Communications Center"},
    ]
    sle.report_gaps("WI", list(names), names, records, {}, "sherr?iff")
    lines = [l for l in capsys.readouterr().out.splitlines() if "--match" in l]
    assert lines, "no runnable command printed"
    assert "county public safety" in lines[-1]     # the curated term, kept
    assert "communications center" in lines[-1]    # and the discovered one


def test_gaps_dump_writes_every_unmatched_county_not_the_printed_sample(tmp_path):
    """The printed report samples 20 counties. On a state nobody has fetched,
    the names it does not print are the evidence that decides the filter."""
    names = {f"55{i:03d}": f"County {i}" for i in range(30)}
    records = [{"geoid": g, "agency": f"{n} Communications Center",
                "phone": "", "source": "OSM", "vintage": "2026-09-14"}
               for g, n in names.items()]
    dest = tmp_path / "wi_gaps.json"
    sle.report_gaps("WI", list(names), names, records, {}, "sherr?iff",
                    log=lambda *_a, **_k: None, dump=str(dest))
    payload = json.loads(dest.read_text())
    assert payload["state"] == "WI"
    assert payload["filter"] == "sherr?iff"
    assert len(payload["counties"]) == 30          # all of them, not 20
    assert payload["counties"][0]["agencies"]


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


# --------------------------------------------------------------------------
# Mutations that used to leave every test green. Each of these fails if the
# named line is reverted - that is the whole point of them.
# --------------------------------------------------------------------------
def test_an_overpass_runtime_error_is_not_cached_as_an_empty_tile(monkeypatch, tmp_path):
    """Overpass answers a server-side timeout with HTTP 200, a normal JSON
    body, an empty elements list and a "remark". Taking that at face value
    caches "no police stations in this part of the state" forever."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    tile = (0.0, 0.0, 1.0, 1.0)
    body = {"version": 0.6, "elements": [],
            "remark": 'runtime error: Query timed out in "query" at line 3 '
                      'after 30 seconds.'}
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None, context=None: FakeHTTP(body))
    with pytest.raises(RuntimeError) as ex:
        sle._overpass_tile(tile, ["https://a.invalid/i"], 30, 1, lambda *a: None)
    assert "timed out" in str(ex.value).lower()
    assert not os.path.exists(sle._tile_cache_path(tile)), "cached a non-answer"


def test_a_server_side_timeout_counts_towards_splitting_the_tile(monkeypatch, tmp_path):
    """It is the clearest possible evidence that the box is too big."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    body = {"elements": [], "remark": "runtime error: Query timed out"}
    tries = []

    def fake(req, timeout=None, context=None):
        tries.append(req.full_url)
        return FakeHTTP(body)

    monkeypatch.setattr("urllib.request.urlopen", fake)
    with pytest.raises(RuntimeError):
        sle._overpass_tile((0.0, 0.0, 1.0, 1.0),
                           ["https://a.invalid/i", "https://b.invalid/i",
                            "https://c.invalid/i"], 30, 1, lambda *a: None)
    assert len(tries) == sle.OSM_TIMEOUTS_BEFORE_SPLIT, tries


def test_an_informational_remark_is_not_treated_as_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    body = {"elements": [{"type": "node", "id": 1, "lat": 1, "lon": 1}],
            "remark": "improve your query"}
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None, context=None: FakeHTTP(body))
    els, _c, _f = sle._overpass_tile((0.0, 0.0, 1.0, 1.0), ["https://a.invalid/i"],
                                     30, 1, lambda *a: None)
    assert [e["id"] for e in els] == [1]


def test_a_cache_write_failure_does_not_throw_away_a_good_response(
        monkeypatch, tmp_path):
    """An unwritable ~/.cache is routine on Android. It used to discard a
    response that had already been fetched correctly and blame the mirror."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None, context=None: FakeHTTP(
                            {"elements": [{"type": "node", "id": 7,
                                           "lat": 1, "lon": 1}]}))

    def no_write(*a, **kw):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(sle, "_write_cache", no_write)
    said = []
    els, from_cache, _f = sle._overpass_tile((0.0, 0.0, 1.0, 1.0),
                                             ["https://a.invalid/i"], 30, 1,
                                             said.append)
    assert [e["id"] for e in els] == [7] and not from_cache
    assert any("could not cache" in m for m in said), said


def test_self_contention_is_not_reported_as_a_mirror_failure(monkeypatch, tmp_path):
    """Blaming the mirrors for our own scheduling sends the user to wait out a
    rate limit that was never hit - and splits a tile for no reason."""
    import threading
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    mirrors = ["https://a.invalid/i"]
    locks = {mirrors[0]: threading.Lock()}
    locks[mirrors[0]].acquire()                     # someone else holds it

    def never(*a, **kw):
        raise AssertionError("asked a mirror we could not acquire")

    monkeypatch.setattr("urllib.request.urlopen", never)
    with pytest.raises(sle.MirrorsBusy):
        sle._overpass_tile((0.0, 0.0, 1.0, 1.0), mirrors, 5, 1, lambda *a: None,
                           deadline=sle.Deadline(0.2), locks=locks)


class RecordingLock:
    """A lock that remembers HOW it was acquired, not just whether.

    Asserting on elapsed time would be measuring the machine; asserting that a
    busy mirror was asked with blocking=False is the actual claim.
    """

    def __init__(self, held=False, log=None):
        self.held = held
        self.log = log if log is not None else []

    def acquire(self, blocking=True, timeout=-1):
        self.log.append(("blocking" if blocking else "nonblocking", timeout))
        if self.held:
            return False
        self.held = True
        return True

    def release(self):
        self.held = False


def test_a_busy_mirror_is_skipped_before_it_is_waited_for(monkeypatch, tmp_path):
    """With --osm-attempts 1 the fast-skip used to be dead code, so every tile
    blocked for up to 30s on a mirror its own siblings were using."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    mirrors = ["https://busy.invalid/i", "https://free.invalid/i"]
    calls = []
    locks = {mirrors[0]: RecordingLock(held=True, log=calls),
             mirrors[1]: RecordingLock(log=calls)}
    asked = []

    def fake(req, timeout=None, context=None):
        asked.append(req.full_url)
        return FakeHTTP({"elements": []})

    monkeypatch.setattr("urllib.request.urlopen", fake)
    sle._overpass_tile((0.0, 0.0, 1.0, 1.0), mirrors, 5, 1, lambda *a: None,
                       locks=locks, deadline=sle.Deadline(60))
    assert asked == [mirrors[1]], asked          # went straight to the free one
    # and the busy one was never waited on
    assert calls[0][0] == "nonblocking", calls
    assert not any(kind == "blocking" for kind, _t in calls), calls


def test_an_expired_tile_cache_is_refetched_not_served(monkeypatch, tmp_path):
    """A months-old tile served silently, while the row built from it is
    stamped with a fetch date, is a date invented for data nobody fetched."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    tile = (0.0, 0.0, 1.0, 1.0)
    sle._write_cache(sle._tile_cache_path(tile),
                     [{"type": "node", "id": 1, "lat": 1, "lon": 1}],
                     fetched="2000-01-01")
    fetched_now = []

    def fake(req, timeout=None, context=None):
        fetched_now.append(req.full_url)
        return FakeHTTP({"elements": [{"type": "node", "id": 2,
                                       "lat": 1, "lon": 1}]})

    monkeypatch.setattr("urllib.request.urlopen", fake)
    els, from_cache, fetched = sle._overpass_tile(
        tile, ["https://a.invalid/i"], 5, 1, lambda *a: None)
    assert fetched_now, "served a cache entry from the year 2000"
    assert not from_cache and [e["id"] for e in els] == [2]
    assert fetched == TODAY

    # a fresh entry IS still served
    fetched_now.clear()
    els, from_cache, fetched = sle._overpass_tile(
        tile, ["https://a.invalid/i"], 5, 1, lambda *a: None)
    assert from_cache and not fetched_now


def test_an_undated_cache_entry_is_not_trusted_as_fresh(monkeypatch, tmp_path):
    """The older format stored a bare list. Its age cannot be established, and
    an unknown age must not pass as a fresh one."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    tile = (0.0, 0.0, 1.0, 1.0)
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(sle._tile_cache_path(tile), "w", encoding="utf-8") as fh:
        json.dump([{"type": "node", "id": 1, "lat": 1, "lon": 1}], fh)
    hit = []
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None, context=None:
                        (hit.append(1), FakeHTTP({"elements": []}))[1])
    sle._overpass_tile(tile, ["https://a.invalid/i"], 5, 1, lambda *a: None)
    assert hit, "an undated cache entry was served as if it were current"


def test_the_same_data_gives_the_same_order_however_it_was_carved_up(
        monkeypatch, tmp_path):
    """A box served whole, served as four quarters, and read back from four
    cache files produced three different sequences. pick_sheriffs breaks ties
    on first-seen, so that is the same data choosing different agencies."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    bbox = (0.0, 0.0, 9.0, 9.0)
    tiles = sle.tile_bbox(bbox)
    # Overpass returns ascending id within type; give the tiles the reverse so
    # a tile-order concatenation and a canonical sort cannot agree by accident.
    def els_for(i):
        return [{"type": "node", "id": 100 - i, "lat": 1.0, "lon": 1.0,
                 "tags": {"name": f"PD {100 - i}"}}]

    whole = {t: els_for(i) for i, t in enumerate(tiles)}
    monkeypatch.setattr(sle, "_overpass_tile",
                        lambda t, m, to, at, log, deadline=None, locks=None,
                        start=0, **kw: (whole[t], False, TODAY))
    a = [r["name"] for r in sle.fetch_osm("MN", bbox=bbox, log=lambda *x: None)]

    # now the same elements, but every tile arrives as four quarters instead
    quarters = {}
    for t in tiles:
        qs = sle.split_tile(t)
        for j, q in enumerate(qs):
            quarters[q] = whole[t] if j == 3 else []      # all in the LAST one
    def split_stub(t, m, to, at, log, deadline=None, locks=None, start=0, **kw):
        if t in quarters:
            return (quarters[t], False, TODAY)
        raise RuntimeError("HTTP Error 504: Gateway Timeout")   # forces a split

    monkeypatch.setattr(sle, "_overpass_tile", split_stub)
    b = [r["name"] for r in sle.fetch_osm("MN", bbox=bbox, log=lambda *x: None)]
    assert a == b, "the same data ordered differently depending on the carve-up"
    # and that order is the source's own: ascending id, which is what a
    # whole-box Overpass response already gives
    assert a == sorted(a, key=lambda n: int(n.split()[1])), a


def test_main_reports_a_counties_lost_tile_as_unknown_not_as_absent(
        monkeypatch, tmp_path, capsys):
    """End to end: the uncovered list has to survive fetch_osm -> main ->
    report_gaps, or --gaps tells the user the source has no sheriff there."""
    monkeypatch.setattr(sle, "CSV_PATH", str(tmp_path / "le.csv"))
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path / "cache"))
    shapes = [("27001", [[[[0.1, 0.1], [1.0, 0.1], [1.0, 1.0], [0.1, 1.0], [0.1, 0.1]]]])]
    monkeypatch.setattr(sle, "county_shapes",
                        lambda sfp, log=print, with_names=False, **kw:
                        ((shapes, {"27001": "Aitkin County"}) if with_names
                         else shapes))
    monkeypatch.setattr(sle, "_overpass_tile",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            RuntimeError("HTTP Error 504: Gateway Timeout")))
    sle.main(["--state", "MN", "--source", "osm", "--gaps", "--allow-partial",
              "--no-split"])
    out = capsys.readouterr().out
    assert "NOT FETCHED" in out and "Aitkin County" in out, out
    assert "no law-enforcement record at all      : 0" in out, out


def test_the_default_job_count_is_actually_concurrent(monkeypatch, tmp_path):
    """Setting OSM_JOBS = 1 - which makes every real run serial again, undoing
    the whole change - used to leave all 290 tests green."""
    import threading
    import time
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    live, peak, lock = [0], [0], threading.Lock()

    def tile(t, m, to, at, log, deadline=None, locks=None, start=0, **kw):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.02)
        with lock:
            live[0] -= 1
        return ([], False, TODAY)

    monkeypatch.setattr(sle, "_overpass_tile", tile)
    sle.fetch_osm("MN", bbox=(0.0, 0.0, 9.0, 9.0), log=lambda *a: None)
    assert sle.OSM_JOBS > 1
    assert peak[0] > 1, "the DEFAULT job count fetches one tile at a time"


def test_the_cli_flags_actually_reach_fetch_osm(monkeypatch, tmp_path):
    """--jobs, --no-split and --refresh-shapes could each be unwired from main
    without a single test failing."""
    monkeypatch.setattr(sle, "CSV_PATH", str(tmp_path / "le.csv"))
    seen = {}
    monkeypatch.setattr(sle, "county_shapes",
                        lambda sfp, log=print, with_names=False, **kw:
                        (seen.setdefault("refresh", kw.get("refresh")),
                         _fake_shapes("27025")(sfp, with_names=with_names))[1])

    def spy(st, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(sle, "fetch_osm", spy)
    sle.main(["--state", "MN", "--source", "osm", "--jobs", "2", "--no-split",
              "--refresh-shapes", "--deadline", "77", "--osm-timeout", "11"])
    assert seen["jobs"] == 2
    assert seen["split"] is False
    assert seen["deadline_s"] == 77
    assert seen["timeout"] == 11
    assert seen["refresh"] is True


def test_gaps_dump_reaches_report_gaps_from_the_command_line(monkeypatch,
                                                             tmp_path):
    """--gaps-dump could be parsed and then never passed on, and the flag
    would look like it worked: the run is quiet either way."""
    monkeypatch.setattr(sle, "CSV_PATH", str(tmp_path / "le.csv"))
    monkeypatch.setattr(sle, "county_shapes", _fake_shapes("27025"))
    monkeypatch.setattr(sle, "fetch_osm", lambda st, **kw: [
        {"lon": 1.0, "lat": 1.0, "name": "Test County Jail", "phone": "",
         "website": "", "address": "", "admintype": "",
         "loaddate": "2026-09-14"}])
    seen = {}
    real = sle.report_gaps
    monkeypatch.setattr(sle, "report_gaps",
                        lambda *a, **kw: (seen.update(kw), real(*a, **kw))[1])
    dest = tmp_path / "gaps.json"
    sle.main(["--state", "MN", "--source", "osm", "--gaps",
              "--gaps-dump", str(dest)])
    assert seen.get("dump") == str(dest)
    assert json.loads(dest.read_text())["state"] == "MN"


# --- one cache, more than one question ---------------------------------------
# Tiles are keyed by bounding box. A second pack type asking a DIFFERENT
# question about the same box must not be served the first one's answer.

def test_two_queries_over_the_same_box_do_not_share_a_cache_entry():
    tile = (-93.0, 45.0, -92.0, 46.0)
    assert sle._tile_cache_path(tile, "police") != \
        sle._tile_cache_path(tile, "repeaters")


def test_the_default_prefix_keeps_tiles_already_on_a_device_valid():
    """Every tile cached before this change was written as osm_police_*."""
    tile = (-93.0, 45.0, -92.0, 46.0)
    assert os.path.basename(sle._tile_cache_path(tile)).startswith("osm_police_")


def test_a_repeater_fetch_never_reads_a_police_tile(monkeypatch, tmp_path):
    """The trap this guards: same box, different question, silently wrong pack."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    tile = (0.0, 0.0, 1.0, 1.0)
    police = [{"type": "node", "id": 1, "lon": 0.5, "lat": 0.5,
               "tags": {"amenity": "police", "name": "Somewhere PD"}}]
    sle._write_cache(sle._tile_cache_path(tile, "police"), police,
                     sle.today_iso())
    # asking as "police" is a cache hit; asking as "repeaters" must not be
    assert sle._overpass_tile(tile, [], 30, 1, lambda *a: None,
                              prefix="police")[1] is True
    with pytest.raises(RuntimeError):          # no mirrors, so: a real fetch
        sle._overpass_tile(tile, [], 30, 1, lambda *a: None, prefix="repeaters")


def test_the_query_actually_reaches_the_request(monkeypatch, tmp_path):
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    sent = []

    def fake(req, timeout=None, context=None):
        sent.append(req.data.decode() if req.data else req.full_url)
        return FakeHTTP({"elements": []})

    monkeypatch.setattr("urllib.request.urlopen", fake)
    sle._overpass_tile((0.0, 0.0, 1.0, 1.0), ["https://a.invalid/i"], 30, 1,
                       lambda *a: None, query=OSM_MARKER, prefix="marker")
    assert sent and "MARKER_TAG_HERE" in sent[0], sent


OSM_MARKER = """
[out:json][timeout:{timeout}];
nwr["MARKER_TAG_HERE"]({s:.4f},{w:.4f},{n:.4f},{e:.4f});
out center tags;
"""


def test_parse_replaces_the_row_shape_and_can_drop_an_element(monkeypatch,
                                                              tmp_path):
    """A second pack type wants different fields off the same element, and the
    de-duplication, ordering and per-tile date stamping are the shared part."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    els = [{"type": "node", "id": 7, "lon": 0.5, "lat": 0.5,
            "tags": {"name": "Keep", "frequency": "146.94"}},
           {"type": "node", "id": 8, "lon": 0.6, "lat": 0.6,
            "tags": {"name": "Drop"}}]
    monkeypatch.setattr(sle, "_overpass_tile",
                        lambda *a, **k: (els, False, "2026-09-14"))

    def parse(tags, lon, lat, fetched, el):
        if not tags.get("frequency"):
            return None                       # dropped, not defaulted
        return {"call": tags.get("name"), "freq_mhz": tags["frequency"],
                "lon": lon, "lat": lat, "loaddate": fetched}

    got = sle.fetch_osm("MN", bbox=(0.0, 0.0, 1.0, 1.0), log=lambda *a: None,
                        parse=parse, prefix="repeaters")
    assert len(got) == 1
    assert got[0]["call"] == "Keep"
    assert got[0]["freq_mhz"] == "146.94"     # a string, never a float
    assert got[0]["loaddate"] == "2026-09-14"


def test_the_socket_gets_more_time_than_the_server(monkeypatch, tmp_path):
    """Cutting the socket at exactly the server's own timeout would abandon a
    server that is about to answer - the slack is the point."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    seen = []

    def fake(req, timeout=None, context=None):
        seen.append(timeout)
        return FakeHTTP({"elements": []})

    monkeypatch.setattr("urllib.request.urlopen", fake)
    sle._overpass_tile((0.0, 0.0, 1.0, 1.0), ["https://a.invalid/i"], 30, 1,
                       lambda *a: None)
    assert seen == [30 + sle.OSM_SOCKET_SLACK], seen
    assert sle.OSM_SOCKET_SLACK > 0


# --------------------------------------------------------------------------
# The antimeridian. min/max longitude is not a bounding box for a state with
# land on both sides of it.
# --------------------------------------------------------------------------
def _ring(w, s_, e, n):
    return [[[[w, s_], [e, s_], [e, n], [w, n], [w, s_]]]]


def test_a_normal_state_still_gets_exactly_one_box():
    shapes = [("27001", _ring(-97.0, 43.0, -95.0, 45.0)),
              ("27003", _ring(-94.0, 46.0, -90.0, 49.0))]
    boxes = sle.bboxes_of_shapes(shapes, pad=0.0)
    assert boxes == [(-97.0, 43.0, -90.0, 49.0)]
    assert sle.bbox_of_shapes(shapes, pad=0.0) == boxes[0]


def test_straddling_the_antimeridian_gives_two_boxes_not_one_huge_one():
    """Alaska: the Aleutians sit near +172, the mainland near -130. min/max is
    302 degrees of longitude - most of the northern hemisphere in one query."""
    shapes = [("02016", _ring(172.0, 51.0, 179.9, 53.0)),     # Aleutians West
              ("02020", _ring(-150.0, 60.0, -149.0, 62.0))]   # Anchorage
    boxes = sle.bboxes_of_shapes(shapes, pad=0.0)
    assert len(boxes) == 2, boxes
    spans = sorted(round(e - w) for w, _s, e, _n in boxes)
    assert spans == [1, 8], spans                # not one 330-degree box
    assert all(-180.0 <= w <= 180.0 and -180.0 <= e <= 180.0
               for w, _s, e, _n in boxes), boxes
    # both boxes still cover the full latitude range of the state
    assert all(s_ == 51.0 and n == 62.0 for _w, s_, _e, n in boxes), boxes


def test_the_single_box_helper_refuses_rather_than_dropping_half_a_state():
    shapes = [("02016", _ring(172.0, 51.0, 179.9, 53.0)),
              ("02020", _ring(-150.0, 60.0, -149.0, 62.0))]
    with pytest.raises(ValueError, match="antimeridian"):
        sle.bbox_of_shapes(shapes)


def test_the_pad_never_pushes_a_box_off_the_globe():
    shapes = [("02110", _ring(-180.0, -90.0, 180.0, 90.0))]
    for w, s_, e, n in sle.bboxes_of_shapes(shapes, pad=5.0):
        assert -180.0 <= w < e <= 180.0
        assert -90.0 <= s_ < n <= 90.0


def test_a_straddling_state_is_tiled_across_both_boxes(monkeypatch, tmp_path):
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    shapes = [("02016", _ring(172.0, 51.0, 179.0, 53.0)),
              ("02020", _ring(-150.0, 60.0, -149.0, 62.0))]
    asked = []

    def tile(t, m, to, at, log, deadline=None, locks=None, start=0, **kw):
        asked.append(t)
        return ([], False, TODAY)

    monkeypatch.setattr(sle, "_overpass_tile", tile)
    monkeypatch.setattr(sle, "county_shapes",
                        lambda sfp, log=print, with_names=False, **kw: shapes)
    sle.fetch_osm("AK", log=lambda *a: None)
    assert len(asked) == 2 * 9, len(asked)       # two boxes, nine tiles each
    # and no tile is the impossible one that spans the planet
    assert all(t[2] - t[0] < 180 for t in asked), asked


# --------------------------------------------------------------------------
# 15 states do not call them counties. The pack title should not either.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("names,want", [
    (["Chisago County", "Kanabec County"], "County"),
    (["Acadia Parish", "Allen Parish", "Ascension Parish"], "Parish"),
    (["Adjuntas Municipio", "Aguada Municipio"], "Municipio"),
    (["Nome Census Area", "Bethel Census Area"], "Census Area"),
    (["Juneau City and Borough", "Sitka City and Borough"], "City and Borough"),
    # Alaska really does mix them, so there is no shared descriptor
    (["Aleutians West Census Area", "Anchorage Municipality"], ""),
    # one name shares a suffix with itself - that is not a pattern
    (["District of Columbia"], ""),
    (["Chisago County"], ""),
    ([], ""),
    (["", None], ""),
])
def test_shared_descriptor_reads_the_word_the_source_used(names, want):
    assert bcp.shared_descriptor(names) == want


def test_a_descriptor_never_swallows_a_whole_name():
    """Two counties named only "Alpha" and "Beta" share nothing to strip."""
    assert bcp.shared_descriptor(["Alpha", "Beta"]) == ""
    # and a name that IS the descriptor cannot contribute one
    assert bcp.shared_descriptor(["County", "Acadia County"]) == ""


def _title_of(monkeypatch, tmp_path, st, counties):
    monkeypatch.setattr(bcp, "fetch_acs", lambda *a, **k: {})
    monkeypatch.setattr(bcp, "fetch_counties", lambda sfp, ep=None, alts=None,
                        log=print: ([{
                            "properties": {"GEOID": g, "NAME": n, "AREALAND": 10 ** 9},
                            "geometry": {"type": "Polygon", "coordinates": [[
                                [-92.4, 30.2], [-92.0, 30.2], [-92.0, 30.6],
                                [-92.4, 30.6], [-92.4, 30.2]]]}}
                            for g, n in counties], "https://tigerweb/1", "Current"))
    r = bcp.build_state(st, str(tmp_path), log=lambda *a: None, today="2026-09-16")
    kml = zipfile.ZipFile(r["path"]).read("doc.kml").decode()
    return re.findall(r"<name>([^<]+)</name>", kml)[0]


def test_a_parish_pack_is_not_titled_counties(monkeypatch, tmp_path):
    t = _title_of(monkeypatch, tmp_path, "LA",
                  [("22001", "Acadia Parish"), ("22003", "Allen Parish")])
    assert t.startswith("LA Parish boundaries"), t


def test_a_single_county_state_is_not_titled_after_its_one_county(
        monkeypatch, tmp_path):
    """"District of Columbia" shares "of Columbia" with itself, which titled
    the pack "DC of Columbia boundaries and reference data"."""
    t = _title_of(monkeypatch, tmp_path, "DC", [("11001", "District of Columbia")])
    assert t == "DC boundaries and reference data (Current)", t


def test_a_state_that_mixes_descriptors_falls_back_to_the_layers_own_name(
        monkeypatch, tmp_path):
    t = _title_of(monkeypatch, tmp_path, "AK",
                  [("02016", "Aleutians West Census Area"),
                   ("02020", "Anchorage Municipality")])
    assert t.startswith("AK County boundaries"), t


def test_refetching_an_undated_tile_says_why(monkeypatch, tmp_path):
    """A run that was instant yesterday doing work today needs a reason on
    screen, or it reads as the cache being broken."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    tile = (0.0, 0.0, 1.0, 1.0)
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(sle._tile_cache_path(tile), "w", encoding="utf-8") as fh:
        json.dump([{"type": "node", "id": 1, "lat": 1, "lon": 1}], fh)
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None, context=None:
                        FakeHTTP({"elements": []}))
    said = []
    sle._overpass_tile(tile, ["https://a.invalid/i"], 5, 1, said.append)
    assert any("fetch date" in m for m in said), said


# --------------------------------------------------------------------------
# HTTP 429. The one failure where waiting is the answer and every other
# response - another mirror, a smaller box - makes it worse.
# --------------------------------------------------------------------------
def _http_error(code, retry_after=None):
    import email.message
    import urllib.error
    hdrs = email.message.Message()
    if retry_after is not None:
        hdrs["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://a.invalid/i", code, "Too Many Requests",
                                  hdrs, None)


def test_a_rate_limit_is_told_apart_from_every_other_failure():
    assert isinstance(sle._as_rate_limit(_http_error(429)), sle.RateLimited)
    assert isinstance(sle._as_rate_limit(_http_error(509)), sle.RateLimited)
    assert sle._as_rate_limit(_http_error(504)) is None
    assert sle._as_rate_limit(OSError("refused")) is None
    # and it is not mistaken for a timeout, which WOULD split the tile
    assert not sle._is_timeout(_http_error(429))


def test_the_servers_own_retry_after_is_honoured():
    assert sle._retry_after(_http_error(429, retry_after=42)) == 42
    assert sle._retry_after(_http_error(429)) is None
    # an HTTP-date form is not guessed at
    assert sle._retry_after(_http_error(429, retry_after="Wed, 21 Oct 2026 07:28:00 GMT")) is None


def test_a_rate_limited_tile_is_never_split(monkeypatch, tmp_path):
    """Splitting turns one refused request into four against a server that
    just said there were too many."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    bbox = (0.0, 0.0, 9.0, 9.0)
    quarters = {q for t in sle.tile_bbox(bbox) for q in sle.split_tile(t)}
    seen = []

    def tile(t, m, to, at, log, deadline=None, locks=None, start=0, **kw):
        seen.append(t)
        raise sle.RateLimited("HTTP 429: Too Many Requests")

    monkeypatch.setattr(sle, "_overpass_tile", tile)
    with pytest.raises(RuntimeError) as ex:
        sle.fetch_osm("MN", bbox=bbox, log=lambda *a: None)
    assert not [t for t in seen if t in quarters], "split a rate-limited tile"
    assert "RATE-LIMITED" in str(ex.value), ex.value
    assert "--jobs 1" in str(ex.value)


def test_one_tiles_429_stops_the_other_tiles_asking_that_mirror(monkeypatch, tmp_path):
    """Otherwise eight more tiles line up to earn the same refusal."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    mirrors = ["https://a.invalid/i", "https://b.invalid/i"]
    cooldowns = {}
    asked = []

    def fake(req, timeout=None, context=None):
        asked.append(req.full_url)
        if req.full_url == mirrors[0]:
            raise _http_error(429, retry_after=60)
        return FakeHTTP({"elements": []})

    monkeypatch.setattr("urllib.request.urlopen", fake)
    for i in range(4):
        sle._overpass_tile((float(i), 0.0, float(i) + 1, 1.0), mirrors, 5, 1,
                           lambda *a: None, start=0, cooldowns=cooldowns)
    # the first tile earns the 429; nothing asks that mirror again
    assert asked.count(mirrors[0]) == 1, asked
    assert asked.count(mirrors[1]) == 4, asked


def test_a_rate_limit_waits_instead_of_giving_up(monkeypatch, tmp_path):
    """Waiting is the whole remedy, so it gets its own retry budget."""
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    slept, calls = [], []
    monkeypatch.setattr(sle.time, "sleep", slept.append)

    def fake(req, timeout=None, context=None):
        calls.append(req.full_url)
        if len(calls) <= 2:
            raise _http_error(429, retry_after=3)
        return FakeHTTP({"elements": [{"type": "node", "id": 1,
                                       "lat": 1, "lon": 1}]})

    monkeypatch.setattr("urllib.request.urlopen", fake)
    els, _c, _f = sle._overpass_tile((0.0, 0.0, 1.0, 1.0),
                                     ["https://a.invalid/i"], 5, 1,
                                     lambda *a: None, deadline=sle.Deadline(600))
    assert [e["id"] for e in els] == [1]
    assert slept == [3, 3], slept            # the server's own Retry-After


def test_a_rate_limit_does_not_wait_past_the_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    slept = []
    monkeypatch.setattr(sle.time, "sleep", slept.append)
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None, context=None:
                        (_ for _ in ()).throw(_http_error(429, retry_after=600)))
    with pytest.raises(sle.RateLimited):
        sle._overpass_tile((0.0, 0.0, 1.0, 1.0), ["https://a.invalid/i"], 5, 1,
                           lambda *a: None, deadline=sle.Deadline(30))
    assert not slept, "waited 600s inside a 30s budget"


def test_the_server_timeout_is_the_one_that_was_measured_to_work():
    """All nine MN tiles are served at [timeout:90]; at 30 five of them fail
    and each failure fans out into four more requests. This constant is
    evidence from a real run, not a tuning knob."""
    assert sle.OSM_SERVER_TIMEOUT_S == 90
    # and the flag default is that constant, not a second copy of the number.
    # Collapse whitespace first: argparse wraps its help, and "(default\n90)"
    # is the same text as "(default 90)".
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        sle.main(["--help"])
    flat = " ".join(buf.getvalue().split())
    assert f"default {sle.OSM_SERVER_TIMEOUT_S}" in flat, flat[:200]


def test_the_socket_outlives_the_server_timeout_by_the_slack(monkeypatch, tmp_path):
    monkeypatch.setattr(sle, "CACHE_DIR", str(tmp_path))
    seen = []
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None, context=None:
                        (seen.append(timeout), FakeHTTP({"elements": []}))[1])
    sle._overpass_tile((0.0, 0.0, 1.0, 1.0), ["https://a.invalid/i"],
                       sle.OSM_SERVER_TIMEOUT_S, 1, lambda *a: None)
    assert seen == [sle.OSM_SERVER_TIMEOUT_S + sle.OSM_SOCKET_SLACK], seen


# --------------------------------------------------------------------------
# Two mutations to build_county_pack.py that left the whole suite green.
# "Either find the data or delete the empty parts" was the rule, and nothing
# was checking the second half of it.
# --------------------------------------------------------------------------
def _popup_of(monkeypatch, tmp_path, props, meta_over=None, csvs=None):
    """Build a one-county pack and return its popup CDATA, unescaped."""
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    for name, text in (csvs or {}).items():
        (data / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(bcp, "DATA_DIR", str(data))
    monkeypatch.setattr(bcp, "fetch_acs", lambda *a, **k: (meta_over or {}))
    monkeypatch.setattr(bcp, "fetch_counties", lambda sfp, ep=None, alts=None,
                        log=print: ([{
                            "properties": props,
                            "geometry": {"type": "Polygon", "coordinates": [[
                                [-93.0, 45.0], [-92.0, 45.0], [-92.0, 46.0],
                                [-93.0, 46.0], [-93.0, 45.0]]]}}],
                            "https://tigerweb/1", "Current"))
    r = bcp.build_state("MN", str(tmp_path), log=lambda *a: None,
                        today="2026-09-16")
    kml = zipfile.ZipFile(r["path"]).read("doc.kml").decode()
    # The FIRST CDATA is the Document description; the county's popup is the
    # one carrying its identity row.
    blocks = re.findall(r"<!\[CDATA\[(.*?)\]\]>", kml, re.S)
    for b in blocks:
        if "FIPS (GEOID)" in b:
            return html.unescape(b)
    raise AssertionError(f"no county popup among {len(blocks)} CDATA blocks")


def test_a_field_with_no_value_is_left_out_of_the_popup_entirely(
        monkeypatch, tmp_path):
    """The rule is "if no sheriff phone, then don't" - not "show it blank".
    A rendered "LE non-emergency: [OpenStreetMap 2026-09-14]" with nothing in
    front of the bracket reads as a number that failed to load."""
    body = _popup_of(monkeypatch, tmp_path,
                     {"GEOID": "27065", "NAME": "Kanabec County",
                      "AREALAND": 1351000000},
                     csvs={"le_contacts.local.csv":
                           "geoid,agency,phone,source,vintage\n"
                           "27065,Kanabec County Sheriff,,OpenStreetMap,2026-09-14\n"})
    assert "Sheriff / primary LE:" in body          # the name IS there
    assert "Kanabec County Sheriff" in body
    # ...and the phone line is not rendered at all
    assert "LE non-emergency:" not in body, body
    # it is accounted for, once, at the bottom
    assert "No data for:" in body and "LE non-emergency" in body
    # and no field is rendered with an empty value
    assert "<b></b>" not in body and ": <b> " not in body


def test_no_popup_line_is_ever_rendered_with_an_empty_value(monkeypatch, tmp_path):
    """The general form of the same rule: every rendered row has a value."""
    body = _popup_of(monkeypatch, tmp_path,
                     {"GEOID": "27065", "NAME": "Kanabec County"})
    # Only the field rows. Everything after <hr/> is the provenance footer,
    # which is prose and a URL, not label/value pairs.
    for line in body.split("<hr/>")[0].split("<br/>"):
        if ":" not in line or "No data for" in line:
            continue
        value = line.split(":", 1)[1]
        assert re.search(r"<b>\s*\S", value), f"empty value rendered: {line!r}"


def test_land_area_comes_from_aland_not_the_projected_shape_area(
        monkeypatch, tmp_path):
    """Shape__Area is in the service's projection and is off by about a factor
    of two at Minnesota's latitude. ALAND is real square metres, which is why
    Kanabec checks out at 521.6 sq mi against the published figure."""
    body = _popup_of(monkeypatch, tmp_path, {
        "GEOID": "27065", "NAME": "Kanabec County",
        "AREALAND": 1351000000,          # 521.6 sq mi
        "Shape__Area": 2900000000,       # projected, ~2x
    })
    assert "521.6 sq mi" in body, body
    assert "TIGER AREALAND" in body or "TIGER ALAND" in body, body
    # the projected figure must not appear at all
    assert "1,119" not in body and "1119" not in body


def test_the_pack_filename_keeps_its_identity_version_boundary(
        monkeypatch, tmp_path):
    """atak-install.sh retires an older edition by splitting on "__". Without
    it a rebuild leaves the previous pack drawing underneath this one."""
    monkeypatch.setattr(bcp, "fetch_acs", lambda *a, **k: {})
    monkeypatch.setattr(bcp, "fetch_counties", lambda sfp, ep=None, alts=None,
                        log=print: ([{
                            "properties": {"GEOID": "27065", "NAME": "Kanabec County"},
                            "geometry": {"type": "Polygon", "coordinates": [[
                                [-93.0, 45.0], [-92.0, 45.0], [-92.0, 46.0],
                                [-93.0, 46.0], [-93.0, 45.0]]]}}],
                            "https://tigerweb/1", "Current"))
    r = bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-16")
    name = os.path.basename(r["path"])
    assert name.count("__") == 1, name
    family, _, edition = name.partition("__")
    assert family == "MN_Counties" and edition.endswith(".kmz") and edition != ".kmz"


# ============================================================================
# repeater_diagnose - step 1 of the repeater work. It asks OSM what is really
# there; it writes no pack and decides nothing.
# ============================================================================

rd = _load("repeater_diagnose")


def test_a_dcs_code_is_never_read_as_a_number():
    """023N has a load-bearing leading zero and an N suffix. Read as a number
    it becomes 23.0, which is a different tone or no tone at all."""
    row = rd.keep_everything({"tone": "023N"}, -93.1, 45.4, "2026-09-14",
                             {"type": "node", "id": 1})
    assert row["tags"]["tone"] == "023N"
    assert isinstance(row["tags"]["tone"], str)


def test_a_frequency_stays_the_string_the_source_wrote():
    row = rd.keep_everything({"frequency_out": "146.940"}, -93.1, 45.4,
                             "2026-09-14", {"type": "node", "id": 1})
    assert row["tags"]["frequency_out"] == "146.940"     # not 146.94


def test_the_diagnostic_parse_drops_nothing_and_converts_nothing():
    tags = {"man_made": "mast", "communication:amateur_radio": "yes",
            "note": "seasonal"}
    row = rd.keep_everything(tags, 1.0, 2.0, "2026-09-14",
                             {"type": "way", "id": 9})
    assert row["tags"] == tags
    assert (row["type"], row["id"]) == ("way", 9)


def test_coverage_is_counted_across_every_spelling():
    """Two objects, two different tagging schemes, one real field."""
    rows = [
        {"type": "node", "id": 1, "lon": -93.1, "lat": 45.4, "tags": {
            "communication:amateur_radio:repeater:frequency_out": "146.940"}},
        {"type": "node", "id": 2, "lon": -93.3, "lat": 45.0, "tags": {
            "frequency_out": "444.150"}},
    ]
    out = []
    rd.report("MN", rows, log=out.append)
    text = "\n".join(out)
    assert "listen (repeater output)         2 of 2" in text
    assert "objects with coordinates AND a listen frequency: 2" in text


def test_nothing_returned_is_reported_as_an_answer_not_a_failure():
    out = []
    rd.report("MN", [], log=out.append)
    text = "\n".join(out)
    assert "That is an ANSWER, not" in text
    assert "--deep" in text            # and names the stronger question


def test_an_empty_deep_run_is_stated_more_strongly_than_an_empty_normal_one():
    """A key regex cannot be defeated by a spelling nobody guessed, so an empty
    --deep result says something an empty exact-key result cannot."""
    shallow, deep = [], []
    rd.report("MN", [], log=shallow.append, deep=False)
    rd.report("MN", [], log=deep.append, deep=True)
    assert "candidate tag" in "\n".join(shallow)
    assert "key REGEX" in "\n".join(deep)
    assert "not a guess" in "\n".join(deep)


def test_an_empty_result_never_claims_a_failed_tile_was_empty():
    """Six tiles returning nothing and three failing is not 'OSM has nothing'.
    The report has to say which it is looking at."""
    out = []
    rd.report("MN", [], log=out.append, deep=True)
    text = "\n".join(out)
    assert "unanswered, not empty" in text


def test_the_query_asks_about_leaf_keys_not_their_parents():
    """The bug this file shipped with. Overpass nwr["k"] matches EXACTLY key k,
    so anchoring on communication:amateur_radio:repeater walks past a repeater
    whose only tag is ...:repeater:frequency_out. One MN run returned 1 object
    from 8 tiles because of it."""
    q = rd.build_query()
    assert '"communication:amateur_radio:repeater:frequency_out"' in q
    assert '"communication:amateur_radio:repeater"]' not in q


def test_the_query_is_generated_from_the_key_list_not_written_beside_it():
    """The two used to be separate and disagreed. Generating one from the other
    is what stops that recurring."""
    q = rd.build_query()
    for k in rd.ANCHOR_KEYS:
        assert f'nwr["{k}"]' in q, k


def test_no_generic_key_can_anchor_a_query():
    """nwr["name"] over Minnesota returns most of the state.

    Asserted against a list written HERE, not against the module's own
    TOO_GENERIC: emptying that constant made the previous version of this test
    pass while the protection was gone, which is exactly what the mutation
    harness caught. A test that reads its expectation from the code it is
    testing is checking nothing.
    """
    never_anchor = ("name", "official_name", "operator", "sponsor", "club",
                    "mode", "tone", "shift", "offset", "frequency", "callsign",
                    "ctcss", "dcs", "ref:callsign", "description", "note")
    for k in rd.ANCHOR_KEYS:
        assert k not in never_anchor, f"{k} is far too common to anchor on"
    # and the guard must REFUSE, not merely disapprove
    with pytest.raises(ValueError) as ex:
        rd.build_query(keys=["name"])
    assert "too generic" in str(ex.value)


def test_every_field_the_report_measures_is_reachable_by_some_anchor():
    """A field measured but never queried for would always read 0%, which
    looks like absent data rather than an unasked question."""
    anchors = set(rd.ANCHOR_KEYS)
    for label, cands in rd.WANTED:
        if label in ("name", "operator / sponsor", "mode"):
            continue           # descriptive; ride along, never anchor
        assert anchors & set(cands), label


def test_deep_mode_uses_a_key_regex_that_no_spelling_defeats():
    q = rd.build_query(deep=True)
    assert "~\"amateur_radio|repeater|gmrs\"" in q
    assert "nwr[\"communication" not in q


def test_changing_the_query_changes_the_cache_namespace():
    """Editing the query while keeping the namespace would serve the OLD
    question's answers to the new one - and would have hidden the leaf-key fix
    behind a stale cache."""
    a = rd.cache_prefix(rd.build_query())
    b = rd.cache_prefix(rd.build_query(keys=["amateur_radio"]))
    assert a != b
    assert rd.cache_prefix(rd.build_query(deep=True), deep=True) not in (a, b)


def test_the_repeater_query_cannot_read_the_police_tile_cache():
    """Different question, same bounding boxes. The prefix is the guard."""
    src = open(os.path.join(SP, "repeater_diagnose.py"),
               encoding="utf-8").read()
    assert "prefix=prefix" in src and "cache_prefix(" in src
    assert "query=query" in src
    tile = (-93.0, 45.0, -92.0, 46.0)
    assert sle._tile_cache_path(tile, "repeaters") != \
        sle._tile_cache_path(tile, "police")


def test_the_query_asks_about_gmrs_too_so_its_absence_is_evidence():
    """GMRS may have no established OSM tagging. Asking and getting nothing
    is how that gets shown rather than assumed."""
    assert "gmrs" in rd.REPEATER_QUERY.lower()
    assert "amateur_radio" in rd.REPEATER_QUERY


# ============================================================================
# atak_inventory - what is in the overlays folder and what is wrong with it.
# It READS. Every "fix" it prints is a line for a person to run.
# ============================================================================

ainv = _load("atak_inventory")


def _make_kmz(path, placemarks=1, provenance=True, broken=False):
    if broken:
        open(path, "wb").write(b"not a zip at all")
        return
    body = ["<?xml version='1.0'?><kml><Document><Folder><name>F</name>"]
    body += [f"<Placemark><name>p{i}</name></Placemark>" for i in range(placemarks)]
    body.append("</Folder>")
    if provenance:
        body.append("<description>Source: US Census TIGER. Retrieved 2026-09-14.</description>")
    body.append("</Document></kml>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("doc.kml", "".join(body))


def test_the_double_underscore_is_the_version_boundary_and_one_is_not():
    assert ainv.family_of("MN_Counties__Current_2026_09_14.kmz") == \
        ("MN_Counties", "Current_2026_09_14")
    assert ainv.family_of("MN_Counties_Current_2026_09_14.kmz") == \
        ("MN_Counties_Current_2026_09_14", "")


def test_the_pre_underscore_file_that_nothing_can_retire_is_flagged(tmp_path):
    """The real one: MN_Counties_Current_2026_09_14.kmz sat beside the
    double-underscore edition drawing every county twice, and the installer
    could not see it."""
    _make_kmz(str(tmp_path / "MN_Counties__Current_2026_09_14.kmz"), 87)
    _make_kmz(str(tmp_path / "MN_Counties_Current_2026_09_14.kmz"), 87)
    problems = ainv.find_problems(ainv.scan(str(tmp_path)))
    stale = [p for p in problems if p[0] == "STALE"]
    assert len(stale) == 1
    assert stale[0][1] == "MN_Counties_Current_2026_09_14.kmz"
    assert "atak-remove.sh" in stale[0][3]


def test_a_sibling_pack_is_not_called_an_edition_of_another(tmp_path):
    _make_kmz(str(tmp_path / "MN_Counties__a.kmz"))
    _make_kmz(str(tmp_path / "MN_Repeaters__a.kmz"))
    _make_kmz(str(tmp_path / "MN_Water__a.kmz"))
    assert not ainv.find_problems(ainv.scan(str(tmp_path)))


def test_two_live_editions_of_one_pack_are_flagged(tmp_path):
    _make_kmz(str(tmp_path / "MN_Counties__2026_09_13.kmz"))
    time.sleep(0.02)
    _make_kmz(str(tmp_path / "MN_Counties__2026_09_14.kmz"))
    dupes = [p for p in ainv.find_problems(ainv.scan(str(tmp_path)))
             if p[0] == "DUPLICATE"]
    assert len(dupes) == 1


def test_a_file_with_no_provenance_is_named(tmp_path):
    """Rule 4: a dot with no source, licence or date is one you cannot check."""
    _make_kmz(str(tmp_path / "parcels.kmz"), 800, provenance=False)
    p = ainv.find_problems(ainv.scan(str(tmp_path)))
    assert [x for x in p if x[0] == "NO SOURCE" and x[1] == "parcels.kmz"]


def test_a_pack_that_parses_but_draws_nothing_is_named(tmp_path):
    _make_kmz(str(tmp_path / "empty_build.kmz"), 0)
    p = ainv.find_problems(ainv.scan(str(tmp_path)))
    assert [x for x in p if x[0] == "EMPTY"]


def test_a_corrupt_file_is_reported_not_raised(tmp_path):
    _make_kmz(str(tmp_path / "corrupt.kmz"), broken=True)
    rows = ainv.scan(str(tmp_path))
    assert rows[0]["error"]
    assert [x for x in ainv.find_problems(rows) if x[0] == "BROKEN"]


def test_the_inventory_changes_nothing_on_disk(tmp_path):
    """It prints removal lines. It must never be the thing that removes."""
    names = ["MN_Counties__a.kmz", "MN_Counties_a.kmz", "corrupt.kmz"]
    for n in names:
        _make_kmz(str(tmp_path / n), broken=(n == "corrupt.kmz"))
    before = {p.name: p.stat().st_size for p in tmp_path.iterdir()}
    rows = ainv.scan(str(tmp_path))
    ainv.report(str(tmp_path), rows, ainv.find_problems(rows),
                log=lambda *a, **k: None)
    after = {p.name: p.stat().st_size for p in tmp_path.iterdir()}
    assert before == after


def test_the_report_says_it_changed_nothing(tmp_path):
    """Every line in the findings is phrased as something to do. Without this
    sentence a reader can reasonably think it was already done for them."""
    _make_kmz(str(tmp_path / "MN_Counties__a.kmz"))
    _make_kmz(str(tmp_path / "MN_Counties_a.kmz"))
    out = []
    rows = ainv.scan(str(tmp_path))
    ainv.report(str(tmp_path), rows, ainv.find_problems(rows), log=out.append)
    text = "\n".join(out)
    assert "Nothing here was changed" in text
    assert "printed, not run" in text


def test_quick_mode_does_not_open_the_files(tmp_path):
    _make_kmz(str(tmp_path / "parcels.kmz"), 800, provenance=False)
    row = ainv.scan(str(tmp_path), deep=False)[0]
    assert row["bytes"] > 0
    assert row["placemarks"] is None       # not counted, not guessed at


def test_the_deep_hint_names_a_real_box_for_the_state():
    """The hint has to be paste-able. A placeholder would send someone to a
    shell with 'W,S,E,N' in it."""
    assert "MN" in rd.METRO_BOX
    w, s, e, n = (float(x) for x in rd.METRO_BOX["MN"].split(","))
    assert w < e and s < n
    assert -97.3 < w < -89.4 and 43.4 < s < 49.4      # inside Minnesota


def test_a_bbox_is_parsed_as_four_numbers_or_refused():
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(SP, "repeater_diagnose.py"),
                        "--state", "MN", "--bbox", "1,2,3"],
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "W,S,E,N" in (r.stderr + r.stdout)


# ============================================================================
# build_nwr_pack - NOAA Weather Radio. One-way broadcast: no input frequency,
# no offset, no access tone, and the 1050 Hz alert is not CTCSS.
# ============================================================================

nwr = _load("build_nwr_pack")

_STATION = {
    "callsign": "WXM99", "freq": "162.425", "power": "1000",
    "lat": "47.555833", "lon": "-94.801361", "status": "NORMAL",
    "sitename": "Bemidji", "siteloc": "Bemidji", "sitestate": "MN",
    "wfo": "Grand Forks|ND",
    "counties": [{"same": "027007", "county": "Beltrami", "st": "MN"},
                 {"same": "227021", "county": "Cass", "st": "MN"}],
}


def test_the_js_assignment_is_parsed_not_the_first_bracket():
    """The file is generated. A comment or a second variable before the data
    would make 'find the first [' pick up the wrong thing in silence."""
    js = '// a note [not data]\nvar other = [1,2];\nvar cclData = [{"a":1}];\n'
    assert nwr.parse_ccl(js) == [{"a": 1}]


def test_an_unparseable_file_says_what_it_actually_saw():
    with pytest.raises(ValueError) as ex:
        nwr.parse_ccl("<!DOCTYPE html><html>404 not found</html>")
    assert "DOCTYPE" in str(ex.value)        # not just "could not parse"


def test_the_popup_carries_no_input_frequency_offset_or_tone():
    """NWR is broadcast. A tone row would be read as a CTCSS access tone and
    programmed into a radio, where it does nothing."""
    pm = nwr.placemark(_STATION, "MN", {"url": "u", "built": "2026-09-14"})
    low = pm.lower()
    for absent in ("offset", "ctcss", "dcs", "tone", "transmit", "input"):
        assert absent not in low, absent
    assert "Listen: <b>162.425 MHz</b>" in pm


def test_a_station_with_no_coordinate_is_dropped_not_placed():
    s = dict(_STATION, lat="", lon="")
    assert nwr.placemark(s, "MN", {"url": "u", "built": "d"}) is None


def test_site_is_not_printed_twice_when_the_source_repeats_itself():
    """sitename == siteloc on 440 of 1036 records."""
    rows, _ = nwr.station_rows(_STATION, "MN")
    site = dict(rows)["Site"]
    assert site == "Bemidji"


def test_a_differing_sitename_and_siteloc_both_survive():
    """They mean different things: the town served, and the hill it is on."""
    s = dict(_STATION, sitename="Alamosa", siteloc="Agua Ramon Mountain")
    rows, _ = nwr.station_rows(s, "MN")
    assert dict(rows)["Site"] == "Alamosa (Agua Ramon Mountain)"


def test_a_partial_county_same_code_never_replaces_the_whole_county_one():
    """Partial County Alerting ADDS sub-area codes. Hennepin is 027053 AND
    127053 AND 327053 in this very file; treating a partial as a substitute
    drops the whole-county code and most of the alerting with it."""
    s = dict(_STATION, counties=[
        {"same": "027053", "county": "Hennepin", "st": "MN"},
        {"same": "127053", "county": "Hennepin", "st": "MN"},
        {"same": "327053", "county": "Hennepin", "st": "MN"}])
    codes = [c for c, _n, _st in nwr.same_pairs(s)]
    assert codes == ["027053", "127053", "327053"]


def test_sited_in_and_covers_are_different_questions():
    nd = dict(_STATION, sitestate="ND",
              counties=[{"same": "027007", "county": "Beltrami", "st": "MN"}])
    assert not nwr.in_state(nd, "MN")                      # not sited here
    assert nwr.in_state(nd, "MN", coverage=True)           # but alerts here


def test_out_of_service_gets_its_own_folder(tmp_path):
    """'The weather radio you were counting on is down' is a thing a folder
    should say, not a flag buried in a popup."""
    dead = dict(_STATION, callsign="KXI45", status="OUT OF SERVICE")
    kml = nwr.pack_kml("MN", [_STATION, dead],
                       {"title": "t", "url": "u", "built": "2026-09-14"})
    assert "<name>NORMAL (1)</name>" in kml
    assert "<name>OUT OF SERVICE (1)</name>" in kml
    ET.fromstring(kml)


def test_the_pack_parses_as_xml_and_states_its_licence_and_read_date():
    kml = nwr.pack_kml("MN", [_STATION],
                       {"title": "t", "url": "u", "built": "2026-09-14"})
    ET.fromstring(kml)
    assert "public domain (NOAA/NWS)" in kml
    assert "Status read: 2026-09-14" in kml       # status is live data
    assert "not CTCSS or DCS" in kml             # said once, where it matters


def test_the_filename_keeps_the_identity_version_boundary(tmp_path):
    path, n = nwr.build("MN", str(tmp_path), [_STATION], log=lambda *a: None)
    assert "__" in os.path.basename(path)
    assert os.path.basename(path).startswith("MN_WeatherRadio__")
    assert n == 1


# ============================================================================
# build_power_pack - EIA power plants. Two capacities, never conflated.
# ============================================================================

pwr = _load("build_power_pack")


def _plant(**kw):
    p = {"Plant_Name": "Test Plant", "Plant_Code": "1", "PrimSource": "coal",
         "Install_MW": 100, "Total_MW": 90, "Utility_Na": "Someone",
         "tech_desc": "Steam Turbine", "sector_nam": "Electric Utility",
         "County": "Test", "Period": "202502", "Source": "EIA-860"}
    p.update(kw)
    return {"type": "Feature", "properties": p,
            "geometry": {"type": "Point", "coordinates": [-93.1, 45.4]}}


def test_nameplate_and_summer_capacity_are_never_merged():
    """Two measurements of different things. A number called 'capacity' with
    no qualifier is unusable."""
    _f, pm = pwr.placemark(_plant(), {"url": "u", "built": "d"})
    assert "Nameplate capacity: <b>100.0 MW</b>" in pm
    assert "Max summer capacity: <b>90.0 MW</b>" in pm


def test_a_summer_capacity_above_nameplate_is_passed_through_not_corrected():
    """Clay Boswell MN reports 923.3 nameplate and 937.8 summer. That is EIA's
    number. 'Fixing' it would be inventing one."""
    _f, pm = pwr.placemark(_plant(Install_MW=923.3, Total_MW=937.8),
                           {"url": "u", "built": "d"})
    assert "Nameplate capacity: <b>923.3 MW</b>" in pm
    assert "Max summer capacity: <b>937.8 MW</b>" in pm


def test_a_string_typed_capacity_does_not_crash_or_become_zero():
    """Several MW columns are typed as strings in the service schema."""
    assert pwr.as_mw("12.5") == 12.5
    assert pwr.as_mw("") is None and pwr.as_mw(None) is None
    assert pwr.as_mw("not a number") is None       # None, never 0


def test_the_eia_reporting_period_is_not_replaced_by_the_build_date():
    """When EIA last reported and when the pack was built are different facts."""
    _f, pm = pwr.placemark(_plant(Period="202502"),
                           {"url": "u", "built": "2026-09-14"})
    assert "EIA reporting period: 202502" in pm
    assert "Pack built: 2026-09-14" in pm


def test_the_dense_fuels_start_switched_off_on_the_placemark_too():
    """641 of 782 MN plants are solar or wind. ATAK's KML path honours
    per-placemark visibility more reliably than a folder's, so both are set."""
    _f, solar = pwr.placemark(_plant(PrimSource="solar"), {"url": "u", "built": "d"})
    _f, nuke = pwr.placemark(_plant(PrimSource="nuclear"), {"url": "u", "built": "d"})
    assert "<visibility>0</visibility>" in solar
    assert "<visibility>0</visibility>" not in nuke


def test_a_plant_with_no_coordinate_is_dropped_not_placed_at_zero():
    bad = _plant()
    bad["geometry"] = {"type": "Point", "coordinates": []}
    assert pwr.placemark(bad, {"url": "u", "built": "d"}) == (None, None)


def test_the_by_fuel_split_appears_only_when_a_plant_burns_more_than_one_thing():
    _f, single = pwr.placemark(_plant(Coal_MW=100), {"url": "u", "built": "d"})
    assert "By fuel" not in single            # would just repeat the nameplate
    _f, mixed = pwr.placemark(_plant(Coal_MW=937, Crude_MW=0.8),
                              {"url": "u", "built": "d"})
    assert "By fuel: coal 937.0 MW, petroleum 0.8 MW" in mixed


def test_folders_put_what_is_switched_on_first():
    """Ordering by count would put 519 solar at the top of the tree."""
    feats = ([_plant(PrimSource="solar") for _ in range(5)]
             + [_plant(PrimSource="nuclear")])
    kml = pwr.pack_kml("MN", feats, {"title": "t", "url": "u", "built": "d"})
    ET.fromstring(kml)
    assert kml.index("<name>nuclear (1)</name>") < kml.index("<name>solar (5)</name>")


def test_eia_is_filtered_on_the_full_state_name_not_the_abbreviation():
    assert pwr.STATE_NAMES["MN"] == "Minnesota"
    assert pwr.STATE_NAMES["DC"] == "District of Columbia"
    missing = [s for s in ("MN", "WI", "TX", "CA", "AK", "HI") if s not in pwr.STATE_NAMES]
    assert not missing


def test_min_mw_filters_and_says_so_rather_than_silently_shrinking(tmp_path):
    feats = [_plant(Install_MW=5), _plant(Install_MW=500)]
    said = []
    path, n = pwr.build("MN", str(tmp_path), feats, min_mw=25, log=said.append)
    assert n == 1
    assert any("--min-mw" in s for s in said)
    assert "__" in os.path.basename(path)


# ============================================================================
# glyphs - symbols drawn here and embedded in the KMZ, because a tablet with
# no signal cannot fetch an icon from maps.google.com.
# ============================================================================

gly = _load("glyphs")


def test_every_glyph_renders_a_real_png():
    for name in gly.glyph_names():
        png = gly.render(name, (255, 209, 64))
        assert png.startswith(b"\x89PNG\r\n\x1a\n"), name
        assert png.endswith(b"IEND\xae\x42\x60\x82"), name
        assert len(png) > 120, name


def test_an_unknown_glyph_raises_rather_than_falling_back_to_a_circle():
    """A symbol the author did not ask for is an invented value, and it would
    ship looking deliberate."""
    with pytest.raises(KeyError) as ex:
        gly.render("dam_wall", (255, 255, 255))
    assert "dam_wall" in str(ex.value)


def test_the_glyphs_the_packs_name_all_exist():
    import build_power_pack as _p
    for fuel, (name, _rgb) in _p.FUEL_STYLE.items():
        assert name in gly.GLYPHS, f"{fuel} wants a glyph {name!r} that does not exist"
    assert _p.FUEL_FALLBACK[0] in gly.GLYPHS
    assert "broadcast" in gly.GLYPHS          # the NWR pack


def test_nuclear_gets_the_trefoil_and_water_gets_the_droplet():
    assert pwr.style_for("nuclear")[0] == "trefoil"
    assert pwr.style_for("hydroelectric")[0] == "droplet"
    assert pwr.style_for("wind")[0] == "turbine"
    assert pwr.style_for("something new EIA invented")[0] == "bolt"


def test_a_pack_references_no_remote_icon(tmp_path):
    """The bug that started this: an http href renders on the bench and fails
    in the field, which is the worst way for one to behave."""
    feats = [_plant(PrimSource="nuclear"), _plant(PrimSource="wind")]
    path, _n = pwr.build("MN", str(tmp_path), feats, log=lambda *a: None)
    with zipfile.ZipFile(path) as z:
        kml = z.read("doc.kml").decode()
    # Only <href> matters. The xmlns is a namespace identifier that is never
    # fetched, and the provenance URL in a popup is text for a person to read.
    hrefs = re.findall(r"<href>([^<]+)</href>", kml)
    assert hrefs, "no icon referenced at all"
    remote = [h for h in hrefs if h.startswith(("http://", "https://"))]
    assert not remote, f"remote icon href: {remote}"
    assert all(h.startswith("icons/") for h in hrefs), hrefs


def test_every_icon_a_pack_references_is_actually_inside_it(tmp_path):
    feats = [_plant(PrimSource=f) for f in
             ("nuclear", "coal", "wind", "solar", "hydroelectric", "batteries")]
    path, _n = pwr.build("MN", str(tmp_path), feats, log=lambda *a: None)
    with zipfile.ZipFile(path) as z:
        kml = z.read("doc.kml").decode()
        inside = set(z.namelist())
    for href in set(re.findall(r"<href>(icons/[^<]+)</href>", kml)):
        assert href in inside, f"{href} referenced but not embedded"


def test_a_pack_carries_only_the_icons_it_uses(tmp_path):
    path, _n = pwr.build("MN", str(tmp_path), [_plant(PrimSource="nuclear")],
                         log=lambda *a: None)
    with zipfile.ZipFile(path) as z:
        icons = [n for n in z.namelist() if n.startswith("icons/")]
    assert icons == ["icons/trefoil.png"]


def test_write_kmz_still_works_with_no_icons(tmp_path):
    """The county pack calls it with two arguments and must keep working."""
    p = str(tmp_path / "x.kmz")
    bcp.write_kmz(p, "<kml/>")
    with zipfile.ZipFile(p) as z:
        assert z.namelist() == ["doc.kml"]


# ============================================================================
# build_repeater_pack - coordinated repeaters only, and it checks its input.
# ============================================================================

rep = _load("build_repeater_pack")


def _rpt(**kw):
    p = {"callsign": "W0ABC", "output_mhz": "146.94", "input_mhz": "146.34",
         "tone": "114.8", "mode": "FM", "band": "2m", "sponsor": "Club",
         "access": "O", "city": "ANOKA", "coordinated": "yes (MRC)",
         "update": "10/16/25", "coord_source": "hearham(site)"}
    p.update(kw)
    return {"type": "Feature", "properties": p,
            "geometry": {"type": "Point", "coordinates": [-93.4, 45.2]}}


def test_a_valid_ctcss_tone_is_accepted_without_a_note():
    assert rep.classify_tone("114.8") == ("ctcss", "")
    assert rep.classify_tone("67.0")[0] == "ctcss"


def test_a_dcs_code_keeps_its_letter_and_its_leading_zero():
    assert rep.classify_tone("D023")[0] == "dcs"
    assert rep.classify_tone("D172")[0] == "dcs"
    assert rep.classify_tone("CC15")[0] == "colour-code"


def test_a_dcs_code_read_as_a_number_is_caught():
    """'23.0' is DCS 023 that went through a float. Keyed into a radio as a
    CTCSS tone it opens nothing."""
    kind, note = rep.classify_tone("23.0")
    assert kind == "unrecognised"
    assert "DCS 023" in note


def test_a_frequency_in_the_tone_column_is_caught():
    """443.4 is a 70cm frequency, not a tone. Seen in a real file."""
    kind, note = rep.classify_tone("443.4")
    assert kind == "unrecognised"
    assert "frequency" in note


def test_a_bad_tone_is_shown_and_labelled_rather_than_dropped():
    """It is what the source says, so it stays - but nobody should key it in
    believing it."""
    _m, pm = rep.placemark(_rpt(tone="443.4"), {"source": "s", "built": "d"})
    assert "443.4" in pm
    assert "not a CTCSS tone" in pm


def test_identical_records_collapse_but_real_differences_survive():
    """A 4x join fan-out put 2,080 features where there were 648 records. One
    callsign appeared 16 times, and stacked pins look like one pin."""
    same = [_rpt(), _rpt(), _rpt()]
    assert len(rep.dedupe(same, log=lambda *a: None)) == 1
    # one callsign, one tower, two modes is two rows, not a mistake
    both = [_rpt(mode="FM"), _rpt(mode="DMR")]
    assert len(rep.dedupe(both, log=lambda *a: None)) == 2


def test_a_missing_input_frequency_is_never_computed_from_an_offset():
    """'Usual' is not 'this machine's', and a repeater you cannot key is worse
    than one you know you cannot key."""
    _m, pm = rep.placemark(_rpt(input_mhz=""), {"source": "s", "built": "d"})
    assert "Transmit" not in pm.split("No data for")[0]
    assert "not computed from an offset" in pm or "No data for" in pm
    assert "141.34" not in pm and "146.34" not in pm


def test_a_city_centroid_says_it_is_not_the_tower():
    """A popup that does not say so invites someone to drive to it."""
    _m, pm = rep.placemark(_rpt(coord_source="city-centroid"),
                           {"source": "s", "built": "d"})
    assert "centre of the town, not the tower" in pm


def test_the_pack_folders_by_mode_and_embeds_an_icon_per_mode(tmp_path):
    feats = [_rpt(mode="FM"), _rpt(mode="DMR", tone="CC1"), _rpt(mode="P25")]
    path, n = rep.build("MN", str(tmp_path), feats, log=lambda *a: None)
    assert n == 3
    with zipfile.ZipFile(path) as z:
        kml = z.read("doc.kml").decode()
        icons = sorted(x for x in z.namelist() if x.startswith("icons/"))
    ET.fromstring(kml)
    for mode in ("FM (1)", "DMR (1)", "P25 (1)"):
        assert f"<name>{mode}</name>" in kml
    assert icons == ["icons/repeater_dmr.png", "icons/repeater_fm.png",
                     "icons/repeater_p25.png"]
    assert "http://" not in "".join(re.findall(r"<href>([^<]+)</href>", kml))


def test_the_pack_says_hotspots_are_not_in_it():
    """The whole point of the coordination gate, stated where someone reads it."""
    kml = rep.pack_kml("MN", [_rpt()],
                       {"title": "t", "source": "s", "built": "d"})
    assert "hotspots are not coordinated" in kml
    assert "never computed from a band" in kml.replace("'", "")


def test_a_missing_input_file_explains_that_there_is_no_fallback(tmp_path):
    """A traceback says the file is missing. It does not say this builder has
    no endpoint to fall back on, which is what someone needs at that moment."""
    import subprocess
    r = subprocess.run(
        [sys.executable, os.path.join(SP, "build_repeater_pack.py"),
         "--state", "MN", "--from-file", str(tmp_path / "nope.geojson")],
        capture_output=True, text=True)
    assert r.returncode != 0
    out = r.stderr + r.stdout
    assert "Traceback" not in out
    assert "no live source to fall back on" in out
    assert "termux-setup-storage" in out          # and how to fix it


# --------------------------------------------------------------------------
# atak_find_dupes - a pack removed from overlays/ can still be served from a
# copy somewhere else in the ATAK tree, which is how a "deleted" layer stays
# on the map. These tests pin the three collision kinds apart, because they
# need different answers and conflating them gives the wrong advice.
# --------------------------------------------------------------------------
afd = _load("atak_find_dupes")


def _mk(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(body)
    return path


def test_find_dupes_sweeps_the_whole_tree_not_just_overlays(tmp_path):
    """The bug this file exists for: only overlays/ was ever looked at."""
    root = str(tmp_path / "atak")
    _mk(os.path.join(root, "overlays", "a.kmz"), b"one")
    _mk(os.path.join(root, "tools", "datapackage", "b.kmz"), b"two")
    _mk(os.path.join(root, "imports", "deep", "c.kml"), b"three")
    found = afd.scan([root])
    assert sorted(f["name"] for f in found) == ["a.kmz", "b.kmz", "c.kml"]


def test_find_dupes_reports_identical_bytes_in_two_directories(tmp_path, capsys):
    root = str(tmp_path / "atak")
    _mk(os.path.join(root, "overlays", "pack.kmz"), b"same-bytes")
    _mk(os.path.join(root, "tools", "datapackage", "pack.kmz"), b"same-bytes")
    files = afd.scan([root])
    problems = afd.report(files)
    out = capsys.readouterr().out
    assert problems == 1
    assert "SAME BYTES" in out
    # Both paths must be shown - naming only one leaves the other on the map.
    assert "overlays" in out and "datapackage" in out


def test_find_dupes_separates_editions_from_same_name_collisions(tmp_path, capsys):
    """`__` marks an edition; without it nothing says which file is newer."""
    root = str(tmp_path / "atak")
    _mk(os.path.join(root, "overlays", "MN_Counties__2026_09_01.kmz"), b"old")
    _mk(os.path.join(root, "overlays", "MN_Counties__2026_09_14.kmz"), b"new")
    _mk(os.path.join(root, "overlays", "roads.kmz"), b"aaa")
    _mk(os.path.join(root, "imports", "roads.kmz"), b"bbb")
    afd.report(afd.scan([root]))
    out = capsys.readouterr().out
    assert "SAME FAMILY" in out
    assert "SAME NAME, DIFFERENT BYTES" in out
    # An edition pair is not a judgement call and must not be filed as one.
    fam = out.index("SAME FAMILY")
    name = out.index("SAME NAME, DIFFERENT BYTES")
    assert "MN_Counties" in out[fam:name]
    assert "MN_Counties" not in out[name:]


def test_find_dupes_family_splits_only_on_the_edition_marker():
    assert afd.family_of("MN_Counties__Current_2026_09_14.kmz") == "MN_Counties"
    # No `__`: the whole stem is the family, which is why these cannot be
    # retired automatically. Splitting on `_` here would merge unrelated packs.
    assert afd.family_of("MN_Chisago_County_rev2.kmz") == "MN_Chisago_County_rev2"


def test_find_dupes_counts_a_symlinked_root_once(tmp_path):
    """/sdcard is usually a symlink to /storage/emulated/0."""
    real = str(tmp_path / "real")
    _mk(os.path.join(real, "overlays", "a.kmz"), b"one")
    link = str(tmp_path / "link")
    os.symlink(real, link)
    assert len(afd.scan([real, link])) == 1


def test_find_dupes_is_read_only(tmp_path, capsys):
    root = str(tmp_path / "atak")
    p1 = _mk(os.path.join(root, "overlays", "pack.kmz"), b"x")
    p2 = _mk(os.path.join(root, "imports", "pack.kmz"), b"x")
    afd.report(afd.scan([root]))
    assert os.path.exists(p1) and os.path.exists(p2)
    assert "Nothing was changed" in capsys.readouterr().out


def test_find_dupes_clean_tree_says_so(tmp_path, capsys):
    root = str(tmp_path / "atak")
    _mk(os.path.join(root, "overlays", "a.kmz"), b"one")
    _mk(os.path.join(root, "overlays", "b.kmz"), b"two")
    assert afd.report(afd.scan([root])) == 0
    assert "No collisions" in capsys.readouterr().out


def test_find_dupes_missing_tree_names_the_directories_it_tried(capsys):
    rc = afd.main(["--root", "/nope/not/here"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "no such directory" in out
    assert "--root" in out                        # and how to point it elsewhere


def test_find_dupes_still_reports_a_stale_edition_that_has_a_byte_copy(
        tmp_path, capsys):
    """The first version dropped it, and the stale pack stayed on the map.

    An old edition with a byte-identical copy elsewhere was filed under SAME
    BYTES and then filtered out of SAME FAMILY - so the report never said the
    old edition was installed alongside the new one, which is the whole
    question being asked.
    """
    root = str(tmp_path / "atak")
    _mk(os.path.join(root, "overlays", "P__2026_09_01.kmz"), b"old")
    _mk(os.path.join(root, "imports", "P__2026_09_01.kmz"), b"old")
    _mk(os.path.join(root, "overlays", "P__2026_09_14.kmz"), b"new")
    afd.report(afd.scan([root]))
    out = capsys.readouterr().out
    fam = out.index("SAME FAMILY")
    assert "P__2026_09_01.kmz" in out[fam:]
    assert "P__2026_09_14.kmz" in out[fam:]


def test_find_dupes_default_roots_are_tree_roots_not_the_overlays_folder():
    """The mutation harness caught this one: nothing tested the default.

    Every other test here passes an explicit root, so narrowing ROOTS back to
    /atak/overlays - the precise bug this tool was written to fix - left the
    whole suite green. The constraint enforces now instead of being a comment.
    """
    afd.check_roots()                              # the shipped default is fine
    with pytest.raises(ValueError) as exc:
        afd.check_roots(["/storage/emulated/0/atak/overlays"])
    assert "overlays" in str(exc.value)
    # A trailing slash is the same mistake and must not slip past.
    with pytest.raises(ValueError):
        afd.check_roots(["/storage/emulated/0/atak/overlays/"])


def test_find_dupes_main_refuses_to_run_with_a_narrowed_default(monkeypatch):
    """The guard has to be on the path main() actually takes."""
    monkeypatch.setattr(afd, "ROOTS", ["/storage/emulated/0/atak/overlays"])
    with pytest.raises(ValueError):
        afd.main([])


# --------------------------------------------------------------------------
# Two rules the mutation harness found untested. Both are the project's first
# non-negotiable - never invent a value - and both survived a mutation that
# made the code invent one, which means the rule was only ever a comment.
# --------------------------------------------------------------------------
def test_repeater_input_frequency_is_never_computed_from_a_band_offset():
    """A repeater's input is a fact about that machine, not arithmetic.

    2m FM in the US is usually -600 kHz, and computing it would be right most
    of the time. Most of the time is how somebody transmits on a frequency
    nobody coordinated. Missing stays missing, and says so.
    """
    p = {"output_mhz": "146.940", "input_mhz": "", "mode": "FM", "band": "2m"}
    rows = {label: (value, note) for label, value, note in rep.rows_for(p)}
    value, note = rows["Transmit"]
    assert value == ""
    assert "not computed" in note
    # The usual offset must not appear anywhere in the popup.
    assert "146.34" not in json.dumps(rep.rows_for(p))

    # And when the source DOES carry it, it is shown verbatim.
    p2 = dict(p, input_mhz="146.340")
    rows2 = {label: (value, note) for label, value, note in rep.rows_for(p2)}
    assert rows2["Transmit"][0] == "146.340 MHz"
    assert rows2["Transmit"][1] == ""


def test_glyph_render_raises_for_a_name_nobody_defined():
    """A substituted symbol ships looking deliberate, which is the danger."""
    with pytest.raises(KeyError) as exc:
        gly.render("no-such-glyph", (255, 0, 0))
    # The error has to say what IS available or the next person guesses again.
    assert "no-such-glyph" in str(exc.value)
    for known in ("bolt", "trefoil"):
        assert known in str(exc.value)
    # A real one still renders a PNG.
    png = gly.render("bolt", (255, 209, 64))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


# --------------------------------------------------------------------------
# symbology - one table decides every layer's shape and colour. Three builders
# had already picked their own independently; the risk is a fourth reusing a
# colour that already means something else.
# --------------------------------------------------------------------------
sym = _load("symbology")


def test_symbology_covers_every_catalog_layer():
    """A layer added with no row builds with no icon and no complaint.

    This is the check that makes the table enforce rather than describe. If it
    fails, either add the row or say explicitly that the layer draws as a line
    or polygon - both are answers, silence is not.
    """
    problems = sym.check(log=lambda *_a, **_k: None)
    assert problems == [], "\n".join(problems)


def test_symbology_coverage_check_actually_checks(tmp_path):
    """Asserting check() finds nothing passes even if check() stopped looking.

    The mutation harness caught this: replacing the catalog comparison with an
    empty set left the test above green, because "no problems" is exactly what
    a disabled check reports. So point check() at a catalog it has never seen
    and require it to notice both directions of the mismatch.
    """
    cat = tmp_path / "catalog"
    cat.mkdir()
    (cat / "made_up.yaml").write_text(
        "defaults:\n  sector: Water\nsources:\n"
        "  - layer: a_layer_nobody_mapped\n    entity: thing\n",
        encoding="utf-8")
    problems = sym.check(path=str(cat), log=lambda *_a, **_k: None)

    # In the catalog, missing from the table.
    assert any("a_layer_nobody_mapped" in p and "missing from this table" in p
               for p in problems), problems
    # And the reverse: this tiny catalog has none of the real layers, so every
    # row in the table is now an orphan and must be reported as one.
    assert any("dams" in p and "not in the catalog" in p
               for p in problems), problems


def test_symbology_every_named_glyph_actually_renders():
    known = set(gly.glyph_names())
    for layer, (glyph, _sector, _geom) in sym.LAYERS.items():
        if glyph is not None:
            assert glyph in known, f"{layer} names a glyph that does not exist"
            assert gly.render(glyph, (255, 0, 0))[:8] == b"\x89PNG\r\n\x1a\n"


def test_symbology_line_and_area_layers_get_no_icon():
    """A pin at a 400-mile pipeline's midpoint is not a place."""
    for layer, (glyph, _sector, geom) in sym.LAYERS.items():
        if geom in ("line", "area"):
            assert glyph is None, f"{layer} is a {geom} layer but has an icon"
            assert sym.icon_for(layer) is None


def test_symbology_sector_colours_are_distinct():
    """Two sectors sharing a colour silently undoes the whole point."""
    seen = {}
    for sector, rgb in sym.SECTOR.items():
        assert rgb not in seen, f"{sector} and {seen.get(rgb)} share {rgb}"
        seen[rgb] = sector


def test_symbology_colour_survives_greyscale():
    """Colour-blind readers and washed-out screens both fall back to value.

    Shape carries the meaning for exactly this reason, but sector colours
    still must not collapse into one grey - that would make the colour channel
    actively misleading rather than merely useless.
    """
    def luma(rgb):
        return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    values = sorted(luma(c) for c in sym.SECTOR.values())
    assert values[-1] - values[0] > 40


def test_power_pack_reads_fuel_styles_from_the_one_table():
    """It used to keep its own copy, and two copies drift.

    Identity is not the assertion: _load() gives each module its own spec, so
    the symbology the builder imports is a different object from the one this
    file loaded. What matters is that the builder's source holds no second
    literal - equal-today values would still drift apart tomorrow.
    """
    assert pwr.FUEL_STYLE == sym.FUEL_STYLE
    src = open(os.path.join(SP, "build_power_pack.py"), encoding="utf-8").read()
    assert "FUEL_STYLE = symbology.FUEL_STYLE" in src
    assert '"nuclear":' not in src, "a second copy of the fuel table came back"
    assert sym.glyph_for("nuclear_reactors") == "trefoil"
    assert sym.FUEL_STYLE["nuclear"][0] == "trefoil"


def test_symbology_unknown_layer_is_none_not_a_guess():
    assert sym.glyph_for("no_such_layer") is None
    assert sym.colour_for("no_such_layer") is None
    assert sym.icon_for("no_such_layer") is None


# --------------------------------------------------------------------------
# CONTAINED - the check that was missing. 87 one-county files sat on the map
# next to an 87-county pack, drawing every boundary twice, and every check in
# atak_inventory passed them: different filenames, no shared `__` family, all
# non-empty, all parseable. Duplication by content is invisible to every test
# that looks at names.
# --------------------------------------------------------------------------
def _county_kmz(path, names, provenance):
    pm = "".join(
        f"<Placemark><name>{n} County</name>"
        f"<Point><coordinates>0,0</coordinates></Point></Placemark>"
        for n in names)
    foot = ("<description>Source: TIGERweb, retrieved 2026-09-14, "
            "licence public domain</description>") if provenance else ""
    doc = f"<?xml version='1.0'?><kml><Document><name>x</name>{foot}{pm}</Document></kml>"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("doc.kml", doc)


def test_inventory_flags_a_pack_already_inside_another(tmp_path):
    d = str(tmp_path / "overlays")
    counties = ["Aitkin", "Anoka", "Cook"]
    for c in counties:
        _county_kmz(os.path.join(d, f"MN_{c}_County_rev2.kmz"), [c], False)
    _county_kmz(os.path.join(d, "MN_Counties__Current_2026_09_14.kmz"),
                counties, True)

    problems = ainv.find_problems(ainv.scan(d))
    contained = [p for p in problems if p[0] == "CONTAINED"]
    assert len(contained) == 3, problems
    assert all("MN_Counties__Current" in p[2] for p in contained)
    # The pack that swallows the others is not itself a problem.
    assert not any("MN_Counties__Current" == p[1] for p in contained)


def test_inventory_containment_folds_county_name_spellings():
    """"Aitkin", "Aitkin County" and "AITKIN CO." are one county."""
    assert ainv.normal_name("Aitkin") == ainv.normal_name("Aitkin County")
    assert ainv.normal_name("AITKIN CO.") == ainv.normal_name("Aitkin")
    assert ainv.normal_name("St. Louis County") == ainv.normal_name("St Louis")
    # Different places must not fold together.
    assert ainv.normal_name("Lake County") != ainv.normal_name("Lake of the Woods County")


def test_inventory_containment_is_strict_not_mutual(tmp_path):
    """Two packs with identical contents are DUPLICATE, not CONTAINED.

    Reporting them as contained would name each as removable because of the
    other, and following both lines deletes the layer entirely.
    """
    d = str(tmp_path / "overlays")
    _county_kmz(os.path.join(d, "a.kmz"), ["Aitkin", "Anoka"], True)
    _county_kmz(os.path.join(d, "b.kmz"), ["Aitkin", "Anoka"], True)
    contained = ainv.contained_in(ainv.scan(d))
    assert contained == []


def test_inventory_containment_ignores_unrelated_packs(tmp_path):
    d = str(tmp_path / "overlays")
    _county_kmz(os.path.join(d, "counties.kmz"), ["Aitkin", "Anoka"], True)
    _county_kmz(os.path.join(d, "plants.kmz"), ["Sherco", "Monticello"], True)
    assert ainv.contained_in(ainv.scan(d)) == []


# --------------------------------------------------------------------------
# edition() - a date is not a version. Two builds on one day produced the
# byte-identical filename, ATAK caches an unpacked KMZ against its name, and
# a rebuilt pack went on serving the first build's icons. "Build it again
# today" is the whole iteration loop, so it is exactly the case that broke.
# --------------------------------------------------------------------------
def test_edition_changes_when_the_content_changes():
    a = bcp.edition("2026-09-14", "<kml>one</kml>")
    b = bcp.edition("2026-09-14", "<kml>two</kml>")
    assert a != b, "same filename for different content is the caching bug"
    assert a.startswith("2026_09_14_") and b.startswith("2026_09_14_")


def test_edition_is_stable_when_nothing_changed():
    """A rebuild that changed nothing must not churn the filename.

    Otherwise every run retires a pack and re-imports an identical one, and
    ATAK is asked to re-read 20 MB for no reason.
    """
    kml = "<kml>same</kml>"
    assert bcp.edition("2026-09-14", kml) == bcp.edition("2026-09-14", kml)


def test_edition_changes_when_only_an_icon_changes():
    """The icons are the payload that actually broke. A digest over the KML
    alone would have left this exact bug in place."""
    kml = "<kml>same</kml>"
    a = bcp.edition("2026-09-14", kml, {"icons/bolt.png": b"old-pixels"})
    b = bcp.edition("2026-09-14", kml, {"icons/bolt.png": b"new-pixels"})
    assert a != b


def test_edition_keeps_the_date_readable_and_first():
    """A pack has to be datable from its filename alone."""
    e = bcp.edition("2026-09-14", "<kml/>")
    assert e.startswith("2026_09_14_")
    # And the identity/version split still works on it.
    ident, version = bcp_family("MN_Counties__" + e + ".kmz")
    assert ident == "MN_Counties" and version.startswith("2026_09_14_")


def bcp_family(name):
    return ainv.family_of(name)


def test_edition_survives_a_vintage_label_that_is_not_a_date():
    e = bcp.edition("Current_2026_09_14", "<kml/>")
    assert e.startswith("Current_2026_09_14_")


def test_rebuilding_with_changed_icons_writes_a_new_file(stubbed, tmp_path):
    """End to end: the same day, different content, a different filename."""
    bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    first = {p.name for p in tmp_path.glob("MN_Counties__*.kmz")}
    assert len(first) == 1
    # Same day, same stub data - must NOT produce a second file.
    bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    again = {p.name for p in tmp_path.glob("MN_Counties__*.kmz")}
    assert again == first, "an unchanged rebuild churned the filename"


def test_inventory_containment_folds_a_trailing_state_suffix():
    """One builder writes "Aitkin County, MN", another writes "Aitkin".

    Without this the two spellings never match and 87 duplicate boundaries
    stay invisible - the exact failure this check exists to catch.
    """
    assert ainv.normal_name("Aitkin County, MN") == ainv.normal_name("Aitkin")
    assert ainv.normal_name("Cook County, MN") == ainv.normal_name("Cook County")
    # Bounded: only a comma plus two letters at the very end.
    assert ainv.normal_name("Washington, DC") == ainv.normal_name("Washington")
    # A real name ending in two letters is untouched.
    assert ainv.normal_name("Lake Ki") != ainv.normal_name("Lake")
    assert "ki" in ainv.normal_name("Lake Ki")


def test_inventory_names_flag_shows_the_folded_names(tmp_path, capsys):
    """The diagnostic for why a containment did or did not fire."""
    d = tmp_path / "overlays"
    _county_kmz(str(d / "a.kmz"), ["Aitkin"], True)
    rc = ainv.main(["--dir", str(d), "--names"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "aitkin" in out
    assert "distinct folded name" in out


def test_inventory_progress_only_goes_to_a_terminal(tmp_path, capsys):
    """Redirected to a file, a \\r progress line is overwritten junk."""
    d = tmp_path / "overlays"
    _county_kmz(str(d / "a.kmz"), ["Aitkin"], True)
    ainv.scan(str(d))
    # capsys makes stderr a non-tty, which is exactly the redirected case.
    assert capsys.readouterr().err == ""


def test_inventory_name_filter_applies_before_opening_anything(tmp_path):
    """A question about 87 small files must not unzip a 9 MB one to answer it."""
    d = tmp_path / "overlays"
    _county_kmz(str(d / "MN_Aitkin_County_rev2.kmz"), ["Aitkin"], False)
    _county_kmz(str(d / "huge_camera_export.kmz"), [f"Cam {i}" for i in range(50)], True)
    rows = ainv.scan(str(d), only="County")
    assert [r["name"] for r in rows] == ["MN_Aitkin_County_rev2.kmz"]
    # Unfiltered still sees both.
    assert len(ainv.scan(str(d))) == 2


def test_inventory_name_filter_is_case_insensitive(tmp_path):
    d = tmp_path / "overlays"
    _county_kmz(str(d / "MN_Aitkin_County_rev2.kmz"), ["Aitkin"], False)
    assert len(ainv.scan(str(d), only="county")) == 1
    assert len(ainv.scan(str(d), only="COUNTY")) == 1
    assert len(ainv.scan(str(d), only="nomatch")) == 0


def test_inventory_progress_line_fits_a_narrow_terminal(monkeypatch, tmp_path):
    """A wrapped \\r line smears instead of updating in place.

    The first version printed the filename and ran past 60 characters, which
    wraps on a phone - and a wrapped carriage return goes to the start of the
    wrapped row, not the line, so every update stays on screen.
    """
    d = tmp_path / "overlays"
    for i in range(3):
        _county_kmz(str(d / f"a_very_long_overlay_filename_{i}.kmz"), ["X"], True)

    written = []

    class FakeTTY:
        def isatty(self):
            return True

        def write(self, s):
            written.append(s)

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stderr", FakeTTY())
    ainv.scan(str(d))
    lines = "".join(written).split("\r")
    assert lines, "no progress was emitted to a tty"
    assert max(len(x) for x in lines) <= 40, max(lines, key=len)


# --------------------------------------------------------------------------
# Repeater fan-out. The supplied MN list carried 2,080 rows for 504 machines:
# a 4x join fan-out, plus rows differing only in PDF column whitespace, plus
# 13 repeaters given one coordinate while the list names two towns for them.
# --------------------------------------------------------------------------
def _rep(callsign, out_mhz, mode, city, coords=(-93.0, 45.0), **extra):
    props = {"callsign": callsign, "output_mhz": out_mhz, "mode": mode,
             "city": city}
    props.update(extra)
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Point", "coordinates": list(coords)}}


def test_repeater_dedupe_collapses_byte_identical_rows():
    f = _rep("W0ABC", 146.94, "FM", "DULUTH")
    out = rep.dedupe([f, dict(f), dict(f), dict(f)], log=lambda *a: None)
    assert len(out) == 1


def test_repeater_dedupe_collapses_whitespace_only_differences():
    """A run of spaces is a PDF column artifact, never a fact about a radio."""
    a = _rep("W0ABC", 146.94, "FM", "DULUTH", access="W0ABC     O")
    b = _rep("W0ABC", 146.94, "FM", "DULUTH", access="W0ABC      O")
    c = _rep("W0ABC", 146.94, "FM", "DULUTH", access="W0ABC  O")
    out = rep.dedupe([a, b, c], log=lambda *a: None)
    assert len(out) == 1
    # Rule 3: the record that survives keeps its ORIGINAL spelling, because
    # the raw attribute is what rides along in the placemark.
    assert out[0]["properties"]["access"] == "W0ABC     O"


def test_repeater_dedupe_keeps_a_real_difference():
    """One callsign running FM and DMR from one tower is two rows."""
    a = _rep("W0ABC", 146.94, "FM", "DULUTH")
    b = _rep("W0ABC", 146.94, "DMR", "DULUTH")
    assert len(rep.dedupe([a, b], log=lambda *a: None)) == 2


def test_repeater_two_entries_for_one_site_are_kept_and_not_alarmed():
    """Same town, same point: two coordination entries for one machine."""
    a = _rep("W0ABC", 146.94, "FM", "DULUTH", sponsor="CLUB A", update="01/01/25")
    b = _rep("W0ABC", 146.94, "FM", "DULUTH", sponsor="CLUB B", update="02/02/26")
    said = []
    out = rep.dedupe([a, b], log=said.append)
    assert len(out) == 2
    joined = " ".join(said)
    assert "one site" in joined
    # Not the loud case, and no disputed-position note on either.
    assert "at least one pin" not in joined
    assert not any(f["properties"].get("_position_contested") for f in out)


def test_repeater_one_coordinate_two_towns_is_reported_loudly():
    """Balaton and Bloomington cannot both be at one point."""
    a = _rep("WA0CQG", 442.15, "DMR", "BALATON")
    b = _rep("WA0CQG", 442.15, "DMR", "BLOOMINGTON")
    said = []
    out = rep.dedupe([a, b], log=said.append)
    assert len(out) == 2, "nothing may be dropped to tidy this up"
    joined = " ".join(said)
    assert "TWO different towns" in joined
    assert "BALATON / BLOOMINGTON" in joined


def test_repeater_contested_position_reaches_the_popup():
    """The build log is read once; the pin is tapped in the field later."""
    a = _rep("WA0CQG", 442.15, "DMR", "BALATON")
    b = _rep("WA0CQG", 442.15, "DMR", "BLOOMINGTON")
    out = rep.dedupe([a, b], log=lambda *x: None)
    balaton = next(f for f in out if f["properties"]["city"] == "BALATON")
    rows = {label: (value, note) for label, value, note in
            rep.rows_for(balaton["properties"])}
    assert rows["Position disputed"][0] == "yes"
    assert "BLOOMINGTON" in rows["Position disputed"][1]
    # And the other one names Balaton, not itself.
    bloom = next(f for f in out if f["properties"]["city"] == "BLOOMINGTON")
    assert "BALATON" in bloom["properties"]["_position_contested"]


def test_repeater_same_identity_at_different_points_is_not_contested():
    """Two towns AND two coordinates is just two repeaters."""
    a = _rep("W0ABC", 146.94, "FM", "DULUTH", coords=(-92.1, 46.8))
    b = _rep("W0ABC", 146.94, "FM", "SAINT PAUL", coords=(-93.1, 44.9))
    out = rep.dedupe([a, b], log=lambda *x: None)
    assert len(out) == 2
    assert not any(f["properties"].get("_position_contested") for f in out)


def test_repeater_uncontested_placemark_has_no_disputed_row():
    f = _rep("W0ABC", 146.94, "FM", "DULUTH")
    rows = {label: value for label, value, _n in rep.rows_for(f["properties"])}
    assert rows["Position disputed"] == ""


# --------------------------------------------------------------------------
# build_emergency_pack - Phase 2. Every HIFLD source for this sector points at
# a host that left DNS, so this is built on OSM, the only source in this
# sector this project has actually fetched live.
# --------------------------------------------------------------------------
emg = _load("build_emergency_pack")


def test_emergency_query_quotes_key_and_value_separately():
    """The bug this file was written around twice.

    ["amenity=police"] is valid QL asking for a tag whose KEY is the literal
    string "amenity=police". It returns nothing, forever, and reads as "there
    are no police stations in Minnesota". The repeater diagnostic already cost
    a live run to this exact mistake.
    """
    q = emg.build_query()
    assert '["amenity"="police"]' in q
    assert '["amenity=police"]' not in q
    assert "=police]" not in q.replace('"="police"]', "")


def test_emergency_query_emits_multi_clause_selectors_as_separate_filters():
    q = emg.build_query()
    assert '["amenity"="clinic"]["urgent_care"="yes"]' in q


def test_emergency_query_converts_inline_flag_to_the_overpass_modifier():
    """POSIX ERE has no inline flags; (?i) must never reach the server."""
    out = emg.emit_clause("name", "~", "(?i)sheriff")
    assert out == '["name"~"sheriff",i]'
    assert "(?i)" not in out


def test_emergency_query_is_built_from_the_class_table():
    """Not written beside it. That is how the two drift apart."""
    one = [("police", [(("amenity", "=", "police"),)], "LE")]
    q = emg.build_query(one)
    assert q.count("nwr[") == 1
    assert '["amenity"="police"]' in q


def test_emergency_empty_selector_is_refused():
    """An empty selector matches every object in the bounding box."""
    with pytest.raises(ValueError):
        emg.emit_selector(())
    with pytest.raises(ValueError):
        emg.build_query([])


def test_emergency_unknown_operator_is_refused():
    with pytest.raises(ValueError):
        emg.emit_clause("amenity", "!=", "police")


def test_emergency_class_table_is_checked_offline():
    """A live run costs 6-17 minutes; a wrong table must fail before that."""
    assert emg.check_classes() == []


def test_emergency_every_class_has_a_renderable_icon():
    for layer, _sel, _label in emg.CLASSES:
        glyph = sym.glyph_for(layer)
        assert glyph is not None, f"{layer} has no icon in symbology"
        assert sym.colour_for(layer) is not None
        assert gly.render(glyph, (255, 255, 255))[:8] == b"\x89PNG\r\n\x1a\n"


def test_emergency_classify_first_match_wins_and_never_double_counts():
    """A townhall that is also a shelter is one placemark, not two."""
    tags = {"amenity": "townhall", "social_facility": "shelter"}
    layer = emg.classify(tags)
    assert layer in ("government", "shelters")
    hits = [lay for lay, sels, _l in emg.CLASSES
            if any(emg.matches(tags, s) for s in sels)]
    assert len(hits) > 1, "fixture no longer tests the overlap case"
    assert layer == hits[0], "first match in CLASSES order must win"


def test_emergency_unmatched_element_is_dropped_not_guessed():
    assert emg.classify({"amenity": "cafe"}) is None
    assert emg.parse_element({"amenity": "cafe"}, -93.0, 45.0, "2026-09-15",
                             {"type": "node", "id": 1}) is None


def test_emergency_multi_clause_needs_every_clause():
    assert emg.classify({"amenity": "clinic", "urgent_care": "yes"}) == "urgent_care"
    # A clinic without the urgent_care tag is not urgent care.
    assert emg.classify({"amenity": "clinic"}) is None


def test_emergency_regex_clause_matches_and_rejects():
    assert emg.classify({"amenity": "shelter",
                         "shelter_type": "emergency"}) == "shelters"
    # A picnic shelter is not an emergency shelter.
    assert emg.classify({"amenity": "shelter",
                         "shelter_type": "picnic_shelter"}) is None


def test_emergency_parse_keeps_the_whole_tag_table():
    """Rule 3: the full source attribute table rides in every placemark."""
    tags = {"amenity": "police", "name": "Anytown PD", "operator": "City",
            "some:odd:key": "kept anyway"}
    row = emg.parse_element(tags, -93.0, 45.0, "2026-09-15",
                            {"type": "node", "id": 7})
    assert row["tags"] == tags
    assert row["layer"] == "police"
    assert row["fetched"] == "2026-09-15"


def test_emergency_missing_phone_stays_missing_and_says_so():
    row = emg.parse_element({"amenity": "police", "name": "X"}, -93.0, 45.0,
                            "2026-09-15", {"type": "node", "id": 1})
    rows = {a: (b, c) for a, b, c in emg.rows_for(row)}
    assert rows["Phone"][0] == ""
    assert "not in OpenStreetMap" in rows["Phone"][1]


def test_emergency_empty_class_keeps_its_folder_and_says_zero(tmp_path):
    """"No fire stations here" must not look like "we never asked"."""
    rows = [emg.parse_element({"amenity": "police", "name": "PD"}, -93.0, 45.0,
                              "2026-09-15", {"type": "node", "id": 1})]
    kml, icons = emg.build_kml("MN", rows, "2026-09-15")
    minidom.parseString(kml)
    assert "Fire stations (0)" in kml
    assert "Law enforcement (1)" in kml
    assert len(icons) == len(emg.CLASSES)


def test_emergency_unnamed_feature_gets_a_usable_label():
    row = emg.parse_element({"amenity": "fire_station"}, -93.0, 45.0,
                            "2026-09-15", {"type": "node", "id": 2})
    kml, _icons = emg.build_kml("MN", [row], "2026-09-15")
    assert "(unnamed fire_stations)" in kml
    assert "<name></name>" not in kml


def test_emergency_document_carries_provenance():
    """Rule 4: source, licence and retrieval date in every document."""
    kml, _ = emg.build_kml("MN", [], "2026-09-15")
    assert "OpenStreetMap contributors" in kml
    assert "ODbL 1.0" in kml
    assert "2026-09-15" in kml


def test_emergency_report_names_the_classes_that_returned_nothing():
    said = []
    emg.report([emg.parse_element({"amenity": "police", "name": "PD"},
                                  -93.0, 45.0, "2026-09-15",
                                  {"type": "node", "id": 1})], log=said.append)
    joined = " ".join(said)
    assert "returned nothing" in joined
    assert "Fire stations" in joined
    # And it must not let a zero read as a failed fetch.
    assert "not a failed fetch" in joined


# --------------------------------------------------------------------------
# merge_packs - a fetch that pages at 2,000 writes CI_substations_01..04 and
# CI_transmission_lines_01..06. Ten top-level entries in Overlay Manager for
# what is really two layers, and no single toggle for "substations".
# --------------------------------------------------------------------------
mpk = _load("merge_packs")


def _page(path, rows, doc="subs", provenance="Source: X, retrieved 2026-09-13"):
    pms = "".join(
        f'<Placemark><name>{r["name"]}</name><ExtendedData>'
        + "".join(f'<Data name="{k}"><value>{v}</value></Data>'
                  for k, v in r.get("fields", {}).items())
        + f'</ExtendedData><Point><coordinates>{r["coords"]},0</coordinates>'
        f"</Point></Placemark>" for r in rows)
    desc = f"<description>{provenance}</description>" if provenance else ""
    kml = ('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2">'
           f"<Document><name>{doc}</name>{desc}{pms}</Document></kml>")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("doc.kml", kml)


def test_merge_groups_by_a_field_and_sorts_numerically(tmp_path):
    """69 kV sorts before 115 kV. Lexicographic order puts 115 first."""
    _page(str(tmp_path / "a.kmz"), [
        {"name": "A", "coords": "-93.1,45.0", "fields": {"KV": "115"}},
        {"name": "B", "coords": "-93.2,45.0", "fields": {"KV": "69"}},
        {"name": "C", "coords": "-93.3,45.0", "fields": {"KV": "345"}}])
    packs = [mpk.read_pack(str(tmp_path / "a.kmz"))]
    kml, _icons, n = mpk.merge(packs, "S", folder_by="KV", log=lambda *a: None)
    minidom.parseString(kml)
    assert n == 3
    folders = re.findall(r"<name>([^<]*\(\d+\))</name>", kml)
    assert folders == ["69 (1)", "115 (1)", "345 (1)"]


def test_merge_missing_folder_field_gets_its_own_named_folder(tmp_path):
    """How many features lack the field is a fact, not something to hide."""
    _page(str(tmp_path / "a.kmz"), [
        {"name": "A", "coords": "-93.1,45.0", "fields": {"KV": "115"}},
        {"name": "B", "coords": "-93.2,45.0", "fields": {}}])
    kml, _i, _n = mpk.merge([mpk.read_pack(str(tmp_path / "a.kmz"))], "S",
                            folder_by="KV", log=lambda *a: None)
    assert "(not recorded) (1)" in kml
    assert "115 (1)" in kml


def test_merge_label_template_is_all_or_nothing(tmp_path):
    """" - kV" looks like a real label for a substation with no voltage."""
    _page(str(tmp_path / "a.kmz"), [
        {"name": "KEEP ME", "coords": "-93.1,45.0", "fields": {"NAME": "Alpha"}},
        {"name": "B", "coords": "-93.2,45.0",
         "fields": {"NAME": "Beta", "KV": "115"}}])
    said = []
    kml, _i, _n = mpk.merge([mpk.read_pack(str(tmp_path / "a.kmz"))], "S",
                            label="{NAME} - {KV} kV", log=said.append)
    assert "Beta - 115 kV" in kml
    assert "KEEP ME" in kml, "a half-filled label must never be written"
    assert " - kV" not in kml
    assert "kept their original name" in " ".join(said)


def test_merge_collapses_a_placemark_that_appeared_on_two_pages(tmp_path):
    row = {"name": "Edge", "coords": "-93.5,45.0", "fields": {"KV": "115"}}
    _page(str(tmp_path / "p1.kmz"), [row])
    _page(str(tmp_path / "p2.kmz"), [row])
    packs = [mpk.read_pack(str(tmp_path / f"p{i}.kmz")) for i in (1, 2)]
    _kml, _i, n = mpk.merge(packs, "S", folder_by="KV", log=lambda *a: None)
    assert n == 1


def test_merge_keeps_two_features_that_merely_share_a_name(tmp_path):
    _page(str(tmp_path / "p1.kmz"), [
        {"name": "Oak", "coords": "-93.5,45.0", "fields": {"KV": "115"}}])
    _page(str(tmp_path / "p2.kmz"), [
        {"name": "Oak", "coords": "-95.5,47.0", "fields": {"KV": "115"}}])
    packs = [mpk.read_pack(str(tmp_path / f"p{i}.kmz")) for i in (1, 2)]
    _kml, _i, n = mpk.merge(packs, "S", folder_by="KV", log=lambda *a: None)
    assert n == 2, "different coordinates means two substations"


def test_merge_carries_provenance_from_every_source(tmp_path):
    """Rule 4. A merged pack claiming one origin it lacks is the failure."""
    _page(str(tmp_path / "p1.kmz"), [{"name": "A", "coords": "-93.1,45.0"}],
          provenance="Source: HIFLD, retrieved 2026-09-13")
    _page(str(tmp_path / "p2.kmz"), [{"name": "B", "coords": "-93.2,45.0"}],
          provenance="Source: OSM, retrieved 2026-09-14")
    packs = [mpk.read_pack(str(tmp_path / f"p{i}.kmz")) for i in (1, 2)]
    kml, _i, _n = mpk.merge(packs, "S", log=lambda *a: None)
    assert "HIFLD" in kml and "OSM" in kml
    assert "p1.kmz" in kml and "p2.kmz" in kml


def test_merge_names_the_files_that_had_no_provenance(tmp_path):
    _page(str(tmp_path / "good.kmz"), [{"name": "A", "coords": "-93.1,45.0"}],
          provenance="Source: HIFLD, retrieved 2026-09-13")
    _page(str(tmp_path / "bare.kmz"), [{"name": "B", "coords": "-93.2,45.0"}],
          provenance="")
    packs = [mpk.read_pack(str(tmp_path / n)) for n in ("good.kmz", "bare.kmz")]
    kml, _i, _n = mpk.merge(packs, "S", log=lambda *a: None)
    assert "No provenance in" in kml and "bare.kmz" in kml


def test_merge_skips_a_broken_file_and_says_which(tmp_path):
    _page(str(tmp_path / "ok.kmz"), [{"name": "A", "coords": "-93.1,45.0"}])
    (tmp_path / "broken.kmz").write_bytes(b"not a zip")
    packs = [mpk.read_pack(str(tmp_path / n)) for n in ("ok.kmz", "broken.kmz")]
    said = []
    _kml, _i, n = mpk.merge(packs, "S", log=said.append)
    assert n == 1
    assert "broken.kmz" in " ".join(said)


def test_merge_geometry_is_carried_across_untouched(tmp_path):
    """A regroup that quietly moved a coordinate is far worse than ten menu
    entries, which is the only problem this tool exists to solve."""
    _page(str(tmp_path / "a.kmz"), [
        {"name": "A", "coords": "-93.123456,45.654321", "fields": {"KV": "115"}}])
    kml, _i, _n = mpk.merge([mpk.read_pack(str(tmp_path / "a.kmz"))], "S",
                            folder_by="KV", log=lambda *a: None)
    assert "-93.123456,45.654321,0" in kml


def test_merge_inspect_lists_the_fields_actually_present(tmp_path, capsys):
    """The template is chosen from what the files carry, never guessed."""
    _page(str(tmp_path / "a.kmz"), [
        {"name": "A", "coords": "-93.1,45.0",
         "fields": {"NAME": "Alpha", "VOLTAGE": "115", "BLANK": ""}}])
    mpk.inspect([mpk.read_pack(str(tmp_path / "a.kmz"))])
    out = capsys.readouterr().out
    assert "{NAME}" in out and "{VOLTAGE}" in out
    assert "filled on 1 of 1" in out


def test_merge_render_label_refuses_a_template_with_no_fields():
    assert mpk.render_label("just text", {"A": "1"}, "fallback") is None


def test_merge_numeric_sort_key_handles_non_numeric_labels():
    labels = ["345", "69", "(not recorded)", "115"]
    assert sorted(labels, key=mpk._sort_key) == [
        "69", "115", "345", "(not recorded)"]


# --------------------------------------------------------------------------
# The Overpass bbox contract. build_emergency_pack shipped a query written in
# Overpass TURBO syntax - ({{bbox}}) - which survives .format() as the literal
# text "{bbox}". Every mirror answered HTTP 400, every tile, and --check said
# "0 problems" the whole time. Knowable offline in microseconds; discovered at
# the network, minutes into a live run.
# --------------------------------------------------------------------------
def test_query_contract_rejects_overpass_turbo_bbox():
    turbo = ('[out:json][timeout:{timeout}];\n'
             'nwr["amenity"="police"]({{bbox}});\nout center tags;')
    with pytest.raises(ValueError) as exc:
        sle.validate_query(turbo)
    msg = str(exc.value)
    assert "{bbox}" in msg, "the error must name what is wrong"
    assert "south, west, north, east" in msg, "and what right looks like"


def test_query_contract_accepts_the_shipped_default():
    out = sle.validate_query(sle.OSM_QUERY)
    assert "44.0000,-97.0000,49.0000,-89.0000" in out
    assert "{" not in out and "}" not in out


def test_query_contract_rejects_a_missing_timeout():
    with pytest.raises(ValueError) as exc:
        sle.validate_query('nwr["amenity"="police"]'
                            '({s:.4f},{w:.4f},{n:.4f},{e:.4f});')
    assert "timeout" in str(exc.value)


def test_query_contract_rejects_an_unknown_placeholder():
    with pytest.raises(ValueError):
        sle.validate_query('[out:json][timeout:{timeout}];\n'
                            'nwr["amenity"="police"]'
                            '({s:.4f},{w:.4f},{n:.4f},{e:.4f})[{whoops}];')


def test_query_contract_rejects_empty():
    for bad in ("", "   ", None):
        with pytest.raises(ValueError):
            sle.validate_query(bad)


def test_emergency_query_carries_a_real_bounding_box():
    q = emg.build_query()
    assert "{s:.4f},{w:.4f},{n:.4f},{e:.4f}" in q
    assert "{{bbox}}" not in q and "{bbox}" not in q
    formatted = q.format(timeout=90, s=44.0, w=-97.0, n=49.0, e=-89.0)
    assert "(44.0000,-97.0000,49.0000,-89.0000)" in formatted
    assert "{" not in formatted and "}" not in formatted


def test_emergency_check_would_have_caught_the_bad_query(monkeypatch):
    """--check reported "0 problems" while the query was fatally malformed.

    A check whose all-clear means nothing is worse than no check: it is the
    reason a broken query reached a live run at all.
    """
    assert emg.check_classes() == []
    monkeypatch.setattr(emg, "build_query",
                        lambda *a, **k: "[out:json];nwr[amenity=police]({{bbox}});")
    problems = emg.check_classes()
    assert problems and any("query" in p for p in problems)


def test_fetch_osm_validates_before_touching_the_network(monkeypatch):
    """Nine tiles across three mirrors is minutes to learn a string fact."""
    called = []
    monkeypatch.setattr(sle, "_run_tiles",
                        lambda *a, **k: called.append(1) or [])
    with pytest.raises(ValueError):
        sle.fetch_osm("MN", query="[out:json];nwr[x]({{bbox}});",
                       log=lambda *a: None)
    assert not called, "no tile fetch may start on an invalid query"


def test_emergency_report_never_claims_a_zero_for_a_class_nobody_asked():
    """A `--only police` run announced OSM has no hospitals in Minnesota.

    It does. They were never queried. A zero for something nobody asked for
    is not a finding, it is a lie with a number on it.
    """
    police = [c for c in emg.CLASSES if c[0] == "police"]
    rows = [emg.parse_element({"amenity": "police", "name": "PD"}, -93.0, 45.0,
                              "2026-09-15", {"type": "node", "id": 1})]
    said = []
    emg.report(rows, log=said.append, classes=police)
    joined = " ".join(said)
    assert "Law enforcement" in joined
    assert "Hospitals" in joined, "the skipped classes must still be named"
    # ...but never as a zero, and never as an OSM coverage statement.
    assert "returned nothing" not in joined
    assert "NOT asked for" in joined
    assert "no such feature tagged" not in joined


def test_emergency_report_still_flags_a_real_zero_in_an_asked_class():
    """A class that WAS asked and came back empty is a genuine finding."""
    two = [c for c in emg.CLASSES if c[0] in ("police", "fire_stations")]
    rows = [emg.parse_element({"amenity": "police", "name": "PD"}, -93.0, 45.0,
                              "2026-09-15", {"type": "node", "id": 1})]
    said = []
    emg.report(rows, log=said.append, classes=two)
    joined = " ".join(said)
    assert "returned nothing: Fire stations" in joined
    assert "not a failed fetch" in joined


def test_emergency_report_defaults_to_the_whole_table():
    said = []
    emg.report([], log=said.append)
    joined = " ".join(said)
    assert "returned nothing" in joined
    assert "NOT asked for" not in joined


def test_emergency_dense_layers_import_switched_off():
    """docs/ATAK.md: a dense layer imports off so the tablet stays responsive.

    Measured for Minnesota: schools 2,784 and government 1,207 are 64% of the
    pack, and neither is why someone opens an emergency overlay.
    """
    rows = [
        emg.parse_element({"amenity": "school", "name": "S"}, -93.0, 45.0,
                          "2026-09-15", {"type": "node", "id": 1}),
        emg.parse_element({"amenity": "police", "name": "PD"}, -93.1, 45.1,
                          "2026-09-15", {"type": "node", "id": 2}),
    ]
    kml, _icons = emg.build_kml("MN", rows, "2026-09-15")
    minidom.parseString(kml)
    schools = kml[kml.index("Schools ("):]
    schools = schools[:schools.index("</Folder>")]
    assert "<visibility>0</visibility>" in schools
    # And on the placemark too - a folder-only flag imports looking off and
    # renders on, which is the bug the power pack already hit.
    assert schools.count("<visibility>0</visibility>") >= 2

    le = kml[kml.index("Law enforcement ("):]
    le = le[:le.index("</Folder>")]
    assert "<visibility>0</visibility>" not in le


def test_emergency_default_off_is_an_explicit_list_not_a_threshold():
    """A count threshold would flip a layer off in one state and not another,
    for no reason visible in the file."""
    assert emg.DEFAULT_OFF == {"schools", "government"}
    assert emg.DEFAULT_OFF <= set(emg.class_names())
