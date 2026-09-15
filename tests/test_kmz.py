import xml.dom.minidom as minidom
import zipfile

from overlaybuilder.model import Feature, LayerResult, Provenance
from overlaybuilder.convert import layer_kml, combined_kml, write_kmz
from overlaybuilder.convert.kmz import _eval_cond, style_for
from overlaybuilder.normalize import apply_to_layer


def _prov():
    return Provenance("Test", "http://x", "public", "2026-01-01", "test")


def _roads():
    f = [
        Feature({"type": "LineString", "coordinates": [[-92.9, 45.5], [-92.88, 45.52]]},
                {"RTTYP": "S", "FULLNAME": "MN-8"}),
        Feature({"type": "LineString", "coordinates": [[-92.91, 45.50], [-92.90, 45.50]]},
                {"RTTYP": "C", "FULLNAME": "Cty Rd 1"}),
        Feature({"type": "LineString", "coordinates": [[-92.82, 45.40], [-92.80, 45.41]]},
                {"RTTYP": "S", "FULLNAME": "MN-95"}),
    ]
    return LayerResult("roads", f, _prov(), group_by=["RTTYP"])


def _parcels():
    f = [Feature({"type": "Polygon",
                  "coordinates": [[[-92.9, 45.5], [-92.89, 45.5], [-92.89, 45.51], [-92.9, 45.5]]]},
                 {"PIN": f"12.{i}", "CITY": ("Wyoming" if i % 2 else "Lindstrom")})
         for i in range(6)]
    return LayerResult("parcels", f, _prov(), group_by=["CITY"])


def _lines():
    f = [Feature({"type": "LineString", "coordinates": [[-93.0, 45.4], [-92.7, 45.7]]},
                 {"VOLTAGE": 345, "OWNER": "GRE", "STATUS": "IN SERVICE"}),
         Feature({"type": "LineString", "coordinates": [[-93.1, 45.4], [-92.8, 45.6]]},
                 {"VOLTAGE": 115, "OWNER": "Xcel"}),
         Feature({"type": "MultiLineString", "coordinates": [[[-93.1, 45.4], [-92.8, 45.6]], [[-93.0, 45.4], [-92.9, 45.6]]]},
                 {"VOLTAGE": 69})]
    return apply_to_layer(LayerResult("transmission_lines", f, _prov(), group_by=["voltage_kv"]))


RULES = {"style_rules": [{"when": "voltage_kv >= 345", "color": "ff0000ff", "width": 4},
                         {"when": "voltage_kv >= 100", "color": "ff00a5ff", "width": 2}]}


def test_roads_grouped_wellformed():
    kml, icons = layer_kml(_roads())
    minidom.parseString(kml)  # raises if malformed
    assert "S (2)" in kml and "C (1)" in kml
    assert kml.count("<Placemark>") == 3
    assert "45.500000" in kml
    assert "icons/roads.png" in icons and icons["icons/roads.png"][:4] == b"\x89PNG"


def test_parcels_hidden_and_grouped():
    kml, _ = layer_kml(_parcels())
    minidom.parseString(kml)
    assert "Lindstrom (3)" in kml and "Wyoming (3)" in kml
    assert "<visibility>0</visibility>" in kml  # dense layer starts hidden
    assert "<name>12.0</name>" in kml          # PIN used as the placemark name


def test_style_rules_and_group_units():
    kml, icons = layer_kml(_lines(), RULES)
    minidom.parseString(kml)
    assert '<Style id="transmission_lines_r0">' in kml and "<width>4</width>" in kml
    assert "#transmission_lines_r0" in kml and "#transmission_lines_r1" in kml
    assert "#transmission_lines</styleUrl>" in kml       # 69 kV matched no rule -> base style
    assert "345 kV (1)" in kml and "115 kV (1)" in kml    # group folders carry the unit
    assert kml.index("345 kV (1)") < kml.index("69 kV (1)")   # numeric groups sorted descending
    assert "icons/transmission_lines_r1.png" in icons
    assert "<b>Voltage</b></td><td>345 kV" in kml           # headline row with unit
    assert '<Data name="voltage_kv"><displayName>Voltage</displayName><value>345 kV</value>' in kml


def test_eval_cond():
    assert _eval_cond({"voltage_kv": 345}, "voltage_kv >= 345")
    assert not _eval_cond({"voltage_kv": 100}, "voltage_kv >= 345")
    assert _eval_cond({"substance": "natural gas"}, "substance ~ gas")
    assert _eval_cond({"a": 1, "b": "x"}, "a = 1 and b == x")
    assert not _eval_cond({}, "a = 1")


def test_style_for_defaults_and_override():
    assert style_for("hospitals")[4] is False and style_for("parcels")[4] is True
    c, f, w, icon, hidden, sector = style_for("hospitals", {"hidden": True, "style": {"width": 5}})
    assert hidden and w == 5 and sector == "Emergency & Health"
    assert style_for("unknown_layer")[5] == "Other"


def test_combined_sectors():
    kml, icons = combined_kml([_roads(), _parcels(), _lines()], {"transmission_lines": RULES})
    minidom.parseString(kml)
    assert "<name>Base</name>" in kml and "<name>Energy - Electric</name>" in kml
    assert kml.count("<Placemark>") == 12
    assert len(icons) == 5   # roads, parcels, lines + 2 rules


def test_kmz_roundtrip(tmp_path):
    p = tmp_path / "roads.kmz"
    kml, icons = layer_kml(_roads())
    n = write_kmz(str(p), kml, icons)
    assert n == 3
    z = zipfile.ZipFile(str(p))
    assert set(z.namelist()) == {"doc.kml", "icons/roads.png"}
    minidom.parseString(z.read("doc.kml"))


def test_degenerate_geometry_never_crashes_the_writer():
    from overlaybuilder.convert.kmz import _geom
    for g in ({"type": "Point", "coordinates": ()}, {"type": "Point", "coordinates": [None, None]},
              {"type": "Point", "coordinates": ["NaN", "NaN"]}, {"type": "LineString", "coordinates": [[0, 0]]},
              {"type": "Polygon", "coordinates": [[]]}, {"type": "MultiPolygon", "coordinates": [[[]]]},
              {"type": "MultiPoint", "coordinates": []}, {"type": "LineString", "coordinates": []}):
        assert _geom(g, 6) == ""
    f = [Feature({"type": "Point", "coordinates": ()}, {"NAME": "null shape"}),
         Feature({"type": "Point", "coordinates": [-92.9, 45.5]}, {"NAME": "real"})]
    kml, _ = layer_kml(LayerResult("gas_processing", f, _prov()))
    minidom.parseString(kml)
    assert kml.count("<Placemark>") == 1


def test_kml_element_order_matches_the_schema_sequence():
    kml, _ = layer_kml(_parcels())
    doc = kml[kml.index("<Document>"):]
    order = [doc.index(t) for t in ("<name>", "<visibility>", "<open>", "<description>")]
    assert order == sorted(order)
    pm = kml[kml.index("<Placemark>"):kml.index("</Placemark>")]
    assert pm.index("<name>") < pm.index("<description>") < pm.index("<styleUrl>") < pm.index("<ExtendedData>")
    fol = kml[kml.index("<Folder>"):]
    assert fol.index("<name>") < fol.index("<visibility>") < fol.index("<open>")


def test_combined_keeps_each_source_specs_separate():
    a = _lines()
    b = _lines()
    a.doc_key, b.doc_key = "transmission_lines", "transmission_lines__osm"
    specs = {"transmission_lines": dict(RULES, title="lines (HIFLD)"),
             "transmission_lines__osm": {"title": "lines (OSM)", "hidden": True,
                                         "style": {"color": "ff00ff00", "width": 9}}}
    kml, icons = combined_kml([a, b], specs)
    minidom.parseString(kml)
    assert "lines (HIFLD)" in kml and "lines (OSM)" in kml
    assert "#transmission_lines_r0" in kml            # rules apply to the first document only
    assert "transmission_lines__osm_r0" not in kml
    assert '<Style id="transmission_lines__osm">' in kml and "<width>9</width>" in kml
    assert "icons/transmission_lines__osm.png" in icons
    osm_folder = kml[kml.index("lines (OSM)"):]
    assert osm_folder.index("<visibility>0</visibility>") < osm_folder.index("<open>")


def test_combined_pack_carries_every_sources_provenance_and_hides_when_all_hidden():
    """A sector pack is the default output, so it must satisfy the provenance rule
    on its own, and inherit a layer's hidden-by-default behaviour."""
    from overlaybuilder.convert.kmz import combined_kml
    from overlaybuilder.model import Feature, LayerResult, Provenance

    def _r(layer, src):
        p = Provenance(f"Source {src}", f"http://{src}/x", "ODbL" if src == "osm" else "public domain",
                       "2026-09-14", "_t")
        return LayerResult(layer, [Feature({"type": "Point", "coordinates": [-93.0, 45.5]},
                                           {"name": f"{layer}-{src}"})], p)

    # two sources, one visible layer -> document visible, both sources named
    visible = [_r("power_plants", "eia"), _r("power_plants", "osm")]
    for r, k in zip(visible, ("power_plants", "power_plants__osm")):
        r.doc_key = k
    kml, _ = combined_kml(visible, {"power_plants": {}, "power_plants__osm": {}},
                          title="T", sector_folders=False)
    head = kml[:kml.index("<Style")] if "<Style" in kml else kml
    assert "2 sources in this pack" in head
    assert "Source eia" in head and "Source osm" in head
    assert "http://eia/x" in head and "http://osm/x" in head
    assert "ODbL" in head and "2026-09-14" in head
    assert "<visibility>0</visibility>" not in head
    assert "<name>Energy - Electric</name>" not in kml        # no redundant sector folder

    # every layer hidden by default -> the whole pack imports switched off
    hidden = [_r("parcels", "county"), _r("address_points", "county")]
    for r, k in zip(hidden, ("parcels", "address_points")):
        r.doc_key = k
    kml2, _ = combined_kml(hidden, {"parcels": {}, "address_points": {}},
                           title="T2", sector_folders=False)
    assert "<name>T2</name><visibility>0</visibility>" in kml2

    # sector_folders=True still nests by sector (ALL.kmz keeps that level)
    kml3, _ = combined_kml(visible, {"power_plants": {}, "power_plants__osm": {}}, title="T3")
    assert "<name>Energy - Electric</name>" in kml3
