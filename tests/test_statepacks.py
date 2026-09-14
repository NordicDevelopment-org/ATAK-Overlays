MN_PACK = "MN_Counties_2024.kmz"

"""Tests for statepacks/build_county_pack.py - the stdlib-only ATAK county builder.

No network: every fetch is monkeypatched. What is being proved here is that the
builder never invents a value and never mangles a shape.
"""
import importlib.util
import json
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
    monkeypatch.setattr(bcp, "fetch_acs", lambda sfp, year=2023, log=print, **kw: {
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
    monkeypatch.setattr(bcp, "fetch_acs", lambda sfp, year=2023, log=print, **kw: {})
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
    assert os.path.basename(r["path"]) == "MN_Counties_Current_2026_09_14.kmz"

    # one that already names a year needs no date appended
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print:
                        (_fake_counties(1), "http://e/55", "Census 2020"))
    r2 = bcp.build_state("MN", str(tmp_path), log=lambda *a: None, today="2026-09-14")
    assert os.path.basename(r2["path"]) == "MN_Counties_Census_2020.kmz"


def test_unknown_vintage_is_never_shown_as_a_year(monkeypatch, tmp_path):
    """When the service reports no year, the pack must say so - and its filename
    must not claim one either."""
    monkeypatch.setattr(bcp, "fetch_counties",
                        lambda sfp, ep=None, alts=None, log=print:
                        (_fake_counties(1), "http://e/1", bcp.TIGER_VINTAGE_UNKNOWN))
    monkeypatch.setattr(bcp, "fetch_acs", lambda sfp, year=2023, log=print, **kw: {})
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


# --------------------------------------------------------------------------
# seed_le_contacts.py - sheriff/LE enrichment from the frozen HIFLD snapshot
# --------------------------------------------------------------------------
sle = _load("seed_le_contacts")


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

    assert sle.main(["--state", "MN"]) == 0
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
    assert sle.main(["--state", "MN", "--overwrite"]) == 0
    rows2, _ = sle.read_existing(str(csv_path))
    assert rows2["27025"]["phone"] == "000-000-0000"


def test_unreachable_source_writes_nothing_and_says_why(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("host unreachable")
    monkeypatch.setattr(sle, "resolve_layer", boom)
    assert sle.main(["--state", "MN"]) == 2
    err = capsys.readouterr().err
    assert "Nothing was written" in err and "stay empty" in err


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
    assert "County0 Sheriff&#39;s Office  [HIFLD LE Locations (frozen snapshot) 2025]" in kml \
        or "County0 Sheriff's Office  [HIFLD LE Locations (frozen snapshot) 2025]" in kml
    assert "651-555-0100  [HIFLD LE Locations (frozen snapshot) 2025]" in kml


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
    kml = _doc(tmp_path / "MN_Counties_Current_2026_09_14.kmz")
    assert "15,900  [ACS 5-year 2023]" in kml
    assert "11,000  [ACS 5-year 2023]" in kml
