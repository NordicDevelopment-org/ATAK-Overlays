from overlaybuilder.normalize import (normalize_props, feature_name, parse_number, fmt_value,
                                      headline, apply_to_layer)
from overlaybuilder.model import Feature, LayerResult, Provenance


def test_parse_number_osm_conventions():
    assert parse_number("115000;34500", "V") == 115000.0
    assert parse_number("1.2 MW", "W") == 1200000.0
    assert parse_number("345 kV", "kV") == 345.0
    assert parse_number("1,146.4") == 1146.4
    assert parse_number(None) is None and parse_number("n/a") is None and parse_number(True) is None


def test_eia_style_plant():
    p = {"Plant_Name": "Prairie Island", "Total_MW": "1146.4", "PrimSource": "nuclear",
         "Utility_Name": "Northern States Power Co", "Plant_Code": 1925}
    c = normalize_props(p)
    assert c["capacity_mw"] == 1146.4 and c["fuel"] == "nuclear" and c["source_id"] == "1925"
    assert feature_name(dict(p, **c), None, "power_plants") == "Prairie Island (1,146.4 MW)"


def test_osm_substation_volts_to_kv_exact_case_first():
    o = {"name": "Chisago Sub", "voltage": "345000;115000", "operator": "Xcel", "power": "substation"}
    c = normalize_props(o)
    assert c["voltage_kv"] == 345.0
    assert feature_name(dict(o, **c), None, "substations") == "Chisago Sub (345 kV)"


def test_hifld_substation_and_line():
    c = normalize_props({"NAME": "X SUB", "MAX_VOLT": 345, "MIN_VOLT": 115, "LINES": 4, "STATUS": "IN SERVICE"})
    assert c["max_voltage_kv"] == 345 and c["min_voltage_kv"] == 115 and c["lines"] == 4
    assert c["voltage_kv"] == 345           # derived from max
    l = normalize_props({"VOLTAGE": 345, "VOLT_CLASS": "345", "OWNER": "GRE"})
    assert l["voltage_kv"] == 345 and l["owner"] == "GRE"


def test_spec_fields_override_and_const():
    p = {"CAP": "50 MW", "OP": "Co"}
    c = normalize_props(p, {"fields": {"capacity_mw": {"from": ["CAP@W"]}, "operator": {"from": ["OP"]},
                                       "type": {"const": "peaker"}}})
    assert c["capacity_mw"] == 50 and c["operator"] == "Co" and c["type"] == "peaker"
    # unit conversion m -> ft
    assert normalize_props({"height": "30"}, {"fields": {"height_ft": {"from": ["height@m"]}}})["height_ft"] == round(30 * 3.28084, 4)


def test_apply_to_layer_keeps_raw_on_collision():
    f = Feature({"type": "Point", "coordinates": [0, 0]}, {"name": "A", "voltage": "115000", "type": "raw"})
    r = LayerResult("substations", [f], Provenance("t", "u", "l", "d", "x"))
    apply_to_layer(r, {"fields": {"type": {"from": ["substation"], "defaults": False}}})
    assert f.properties["voltage_kv"] == 115
    assert f.properties["type"] == "raw"          # no canonical type found -> raw untouched
    f2 = Feature(None, {"name": "B", "type": "raw", "substation": "transmission"})
    r2 = LayerResult("substations", [f2], Provenance("t", "u", "l", "d", "x"))
    apply_to_layer(r2)
    assert f2.properties["type"] == "transmission" and f2.properties["src_type"] == "raw"


def test_headline_and_fmt():
    rows = headline({"capacity_mw": 120.5, "fuel": "gas", "operator": "X"}, "power_plants")
    assert rows[0] == ("Capacity", "120.5 MW") and ("Primary fuel", "gas") in rows
    assert fmt_value("storage_acre_ft", 12345.6) == "12,346 acre-ft"
    assert fmt_value("voltage_kv", 34.5) == "34.5 kV"


def test_name_template():
    props = {"name": "Foo", "capacity_mw": 10, "fuel": "wind"}
    assert feature_name(props, {"name": "{name} - {fuel} ({capacity_mw})"}, "power_plants") == "Foo - wind (10.0 MW)"
    assert feature_name({"name": "Bar"}, {"name": "{name} ({capacity_mw})"}, "power_plants") == "Bar"
