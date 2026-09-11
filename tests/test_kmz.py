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
