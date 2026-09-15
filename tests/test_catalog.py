from conftest import CATALOG
from overlaybuilder import catalog
from overlaybuilder.aoi import parse_aoi


def _aoi(s):
    return parse_aoi(s, county_name="Chisago")


def test_catalog_validates_offline():
    assert catalog.validate(CATALOG) == []


def test_national_baseline_present():
    nat = catalog.national_sources(CATALOG)
    layers = {s["layer"] for s in nat}
    assert {"roads", "city_boundaries", "county_boundary", "state_boundary", "building_footprints"} <= layers


def test_global_tier_covers_core_ci_layers():
    layers = {s["layer"] for s in catalog.global_sources(CATALOG)}
    assert {"power_plants", "substations", "transmission_lines", "pipelines", "dams",
            "water_treatment", "wastewater_treatment", "comm_towers", "hospitals",
            "fire_stations", "police", "airports"} <= layers


def test_chisago_entry_resolves():
    srcs = catalog.resolve_sources(CATALOG, _aoi("county:27025"))
    layers = [s["layer"] for s in srcs]
    assert "parcels" in layers              # county source merged in
    assert "roads" in layers                # national baseline present
    assert "power_plants" in layers         # global tier present
    assert srcs[0]["layer"] == "county_boundary"   # boundary first
    parcels = next(s for s in srcs if s["layer"] == "parcels")
    assert parcels["driver"] == "arcgis" and parcels["_tier"] == "county"


def test_state_aoi_skips_county_only_sources():
    srcs = catalog.resolve_sources(CATALOG, _aoi("state:MN"))
    layers = {s["layer"] for s in srcs}
    assert "roads" not in layers and "parcels" not in layers and "building_footprints" not in layers
    assert "state_boundary" in layers and "power_plants" in layers


def test_country_aoi_is_global_only():
    srcs = catalog.resolve_sources(CATALOG, _aoi("country:CA"))
    assert {s["_tier"] for s in srcs} == {"global"}
    assert all(s["driver"] in ("overpass", "osm_pbf") for s in srcs)


def test_layer_and_sector_filters():
    srcs = catalog.resolve_sources(CATALOG, _aoi("county:27025"), only_layers=["roads"])
    assert [s["layer"] for s in srcs] == ["roads"]
    srcs = catalog.resolve_sources(CATALOG, _aoi("county:27025"), sectors=["energy"])
    assert srcs and all(str(s.get("sector", "")).startswith("Energy") for s in srcs)
    srcs = catalog.resolve_sources(CATALOG, _aoi("county:27025"), exclude_layers=["roads", "parcels"])
    assert not {"roads", "parcels"} & {s["layer"] for s in srcs}


def test_unknown_county_gets_global_plus_national():
    srcs = catalog.resolve_sources(CATALOG, parse_aoi("county:48201", county_name="Harris"))
    tiers = {s["_tier"] for s in srcs}
    assert tiers == {"global", "national"}


def test_style_rules_and_fields_parse():
    srcs = catalog.global_sources(CATALOG)
    tl = next(s for s in srcs if s["layer"] == "transmission_lines")
    assert tl["style_rules"][0]["when"].startswith("voltage_kv")
    assert tl["fields"]["voltage_kv"]["from"] == ["voltage@V"]


def test_bbox_inside_mn_gets_state_and_national_tiers():
    srcs = catalog.resolve_sources(CATALOG, parse_aoi("bbox:-93.2,45.3,-92.6,45.8"))
    tiers = {s["_tier"] for s in srcs}
    assert {"global", "national", "state"} <= tiers and "county" not in tiers


def test_us_aoi_skips_overpass_sources():
    srcs = catalog.resolve_sources(CATALOG, parse_aoi("us"))
    assert srcs and all(s["driver"] != "overpass" for s in srcs)
    srcs = catalog.resolve_sources(CATALOG, parse_aoi("region:mn-neighbors"))
    assert any(s["driver"] == "overpass" for s in srcs)


def test_validate_rejects_unknown_canonical_keys_and_units(tmp_path):
    bad = tmp_path / "catalog" / "global"
    bad.mkdir(parents=True)
    (bad / "x.yaml").write_text(
        "sources:\n"
        "  - layer: power_plants\n"
        "    driver: overpass\n"
        "    tags: ['power=plant']\n"
        "    license: test\n"
        "    fields:\n"
        "      megawatts: {from: [MW]}\n"          # not a canonical key
        "      length_ft: {from: ['MILES@furlongs']}\n"   # no conversion
        "      capacity_mw: {from: []}\n"          # no candidates
    )
    problems = catalog.validate(str(tmp_path / "catalog"))
    joined = " ".join(problems)
    assert "megawatts" in joined and "not a canonical field" in joined
    assert "furlongs" in joined and "no `from:`" in joined


def test_validate_accepts_known_units():
    # the shipped catalog maps MILES@mi, VOLTAGE@kV, height@m and so on
    assert catalog.validate(CATALOG) == []


def test_disabled_sources_still_respect_coverage():
    from overlaybuilder.aoi import parse_aoi as _p
    ca = catalog.resolve_sources(CATALOG, _p("country:CA"), include_disabled=True)
    assert all(s["_tier"] == "global" for s in ca)     # US-only candidates stay out of a Canada build
    mn = catalog.resolve_sources(CATALOG, _p("state:MN"), include_disabled=True)
    assert any(not s.get("enabled", True) for s in mn)


def test_new_sectors_are_present_and_styled():
    from overlaybuilder.convert.kmz import LAYER_STYLE
    from overlaybuilder.normalize import HEADLINES
    layers = {s["layer"] for s in catalog.global_sources(CATALOG)}
    assert {"chemical_plants", "hazmat_storage", "grain_storage", "food_processing", "mines"} <= layers
    for key in ("chemical_plants", "hazmat_storage", "grain_storage", "food_processing", "mines"):
        assert key in LAYER_STYLE and key in HEADLINES
    sectors = {s.get("sector") for s in catalog.global_sources(CATALOG)}
    assert {"Chemical & Hazmat", "Agriculture & Food", "Mining"} <= sectors


def test_duplicate_yaml_keys_are_rejected_not_silently_merged(tmp_path):
    """YAML keeps the LAST of a repeated key and discards the first without a
    word. An edit that appends a second `notes:` to a source therefore deletes
    the original note while the file still parses and validate still says zero
    problems. A catalog is a record; losing part of one quietly is worse than
    failing loudly."""
    import pytest
    import yaml
    from overlaybuilder import catalog

    d = tmp_path / "national" / "us"
    d.mkdir(parents=True)
    good = d / "ok.yaml"
    good.write_text(
        "sources:\n"
        "  - layer: hospitals\n"
        "    driver: arcgis\n"
        "    url: http://example/0\n"
        '    notes: "the original note"\n'
    )
    assert len(catalog.all_sources(str(tmp_path))) == 1

    good.write_text(
        "sources:\n"
        "  - layer: hospitals\n"
        "    driver: arcgis\n"
        "    url: http://example/0\n"
        '    notes: "the original note"\n'
        '    notes: "an appended note that would erase it"\n'
    )
    with pytest.raises(yaml.YAMLError, match="duplicate key"):
        catalog.all_sources(str(tmp_path))


def test_no_catalog_source_points_at_a_host_known_dead():
    """maps.nccs.nasa.gov stopped resolving on 2026-09-14. Anything still
    pointing at it as its PRIMARY url must be disabled, or a build spends its
    retries on a host that no longer exists."""
    from overlaybuilder import catalog
    dead_host = "nccs.nasa.gov"
    live = [s for s in catalog.all_sources("catalog")
            if dead_host in str(s.get("url", "")) and s.get("enabled", True)]
    assert not live, (
        "these sources still point at a dead host while enabled: "
        + ", ".join(s["id"] for s in live))
