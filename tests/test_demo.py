import json
import os
import zipfile
import xml.dom.minidom as minidom

import pytest

from overlaybuilder.aoi import parse_aoi
from overlaybuilder.demo import build_demo, demo_specs_and_results, rescale_to_bbox

MN = (-97.24, 43.50, -89.49, 49.39)


def _coords(kml):
    import re
    out = []
    for blob in re.findall(r"<coordinates>([^<]+)</coordinates>", kml):
        for tok in blob.split():
            x, y = (float(v) for v in tok.split(",")[:2])
            out.append((x, y))
    return out


def test_rescale_moves_every_vertex_inside_the_target_and_keeps_shape():
    _, results = demo_specs_and_results()
    rescale_to_bbox(results, MN)
    seen = 0
    for r in results:
        for f in r.features:
            if not f.geometry:
                continue
            stack = [f.geometry["coordinates"]]
            while stack:
                c = stack.pop()
                if c and isinstance(c[0], (int, float)):
                    seen += 1
                    assert MN[0] <= c[0] <= MN[2], f"{r.logical} lon {c[0]} outside MN"
                    assert MN[1] <= c[1] <= MN[3], f"{r.logical} lat {c[1]} outside MN"
                else:
                    stack.extend(c)
    assert seen > 100                                   # the whole grid really was walked

    # rings stay closed after the transform
    for r in results:
        for f in r.features:
            g = f.geometry
            if g and g["type"] == "Polygon":
                for ring in g["coordinates"]:
                    assert ring[0] == ring[-1]


def test_demo_pack_for_mn_is_sector_split_and_labelled_synthetic(tmp_path):
    aoi = parse_aoi("state:MN")
    m = build_demo(str(tmp_path), aoi=aoi, log=lambda *a: None)
    files = set(os.listdir(tmp_path))

    assert {"SAMPLE_MN_Energy-Electric.kmz", "SAMPLE_MN_Water.kmz",
            "SAMPLE_MN_Communications.kmz", "SAMPLE_MN_Mining.kmz"} <= files
    assert "power_plants.kmz" not in files              # sector grouping is the default

    # every pack a user could load is named so it cannot pass for real data
    for f in files:
        if f.endswith(".kmz"):
            assert f.startswith("SAMPLE_") or f == "DEMO_SAMPLE_ALL.kmz", f

    z = zipfile.ZipFile(tmp_path / "SAMPLE_MN_Energy-Electric.kmz")
    kml = z.read("doc.kml").decode()
    minidom.parseString(kml)                            # valid XML
    assert "SAMPLE" in kml and "NOT real infrastructure" in kml
    assert any(n.startswith("icons/") for n in z.namelist())
    pts = _coords(kml)
    assert pts and all(MN[0] <= x <= MN[2] and MN[1] <= y <= MN[3] for x, y in pts)

    assert m["aoi"] == aoi.describe()
    assert "Minnesota" in m["placement"]
    readme = (tmp_path / "README.txt").read_text()
    assert "SYNTHETIC" in readme and "SAMPLE_MN_Energy-Electric.kmz" in readme
    man = json.loads((tmp_path / "manifest.json").read_text())
    assert man["group_by"] == "sector"


def test_demo_without_aoi_still_builds_near_chisago(tmp_path):
    m = build_demo(str(tmp_path), log=lambda *a: None)
    assert m["aoi"] is None and "Chisago" in m["placement"]
    files = set(os.listdir(tmp_path))
    assert "SAMPLE_Energy-Electric.kmz" in files and "DEMO_SAMPLE_ALL.kmz" in files
    kml = zipfile.ZipFile(tmp_path / "SAMPLE_Energy-Electric.kmz").read("doc.kml").decode()
    assert all(-94 < x < -92 and 45 < y < 46 for x, y in _coords(kml))


def test_demo_group_by_layer_and_both(tmp_path):
    build_demo(str(tmp_path / "l"), group_by="layer", log=lambda *a: None)
    lf = set(os.listdir(tmp_path / "l"))
    assert "SAMPLE_power_plants.kmz" in lf
    assert "power_plants.kmz" not in lf        # never the name a real build writes

    build_demo(str(tmp_path / "b"), group_by="both", log=lambda *a: None)
    bf = set(os.listdir(tmp_path / "b"))
    assert "SAMPLE_power_plants.kmz" in bf and "SAMPLE_Energy-Electric.kmz" in bf

    with pytest.raises(ValueError, match="group_by"):
        build_demo(str(tmp_path / "x"), group_by="nope", log=lambda *a: None)


def test_every_demo_kmz_is_marked_synthetic_in_every_mode(tmp_path):
    """A demo file must never be named what a real build would name it."""
    for mode in ("sector", "layer", "both"):
        d = tmp_path / mode
        build_demo(str(d), group_by=mode, aoi=parse_aoi("state:MN"), log=lambda *a: None)
        for f in os.listdir(d):
            if f.endswith(".kmz"):
                assert f.startswith("SAMPLE_") or f == "DEMO_SAMPLE_ALL.kmz", f"{mode}: {f}"


def test_county_aoi_does_not_claim_a_boundary_it_never_had(tmp_path):
    """A county Aoi carries the STATE envelope until TIGER supplies the polygon.

    Placing the grid there is fine; claiming it is "inside Chisago County" is not.
    """
    aoi = parse_aoi("county:27025", county_name="Chisago")
    assert aoi.geometry is None and aoi.bbox == MN        # the premise of this test
    m = build_demo(str(tmp_path), aoi=aoi, log=lambda *a: None)

    placement = m["placement"]
    assert "WIDER than its actual boundary" in placement
    assert "inside the Chisago" not in placement

    # and the honest wording rides everywhere the claim is repeated
    assert "WIDER" in (tmp_path / "README.txt").read_text()
    kml = zipfile.ZipFile(tmp_path / "SAMPLE_MN-Chisago_Water.kmz").read("doc.kml").decode()
    assert "WIDER than its actual boundary" in kml


def test_aoi_without_an_envelope_is_not_named_in_the_pack(tmp_path):
    """country:XX has bbox=None, so nothing is placed - and nothing may claim it."""
    aoi = parse_aoi("country:CA")
    assert aoi.bbox is None                               # the premise of this test
    m = build_demo(str(tmp_path), aoi=aoi, log=lambda *a: None)

    files = [f for f in os.listdir(tmp_path) if f.endswith(".kmz")]
    assert not [f for f in files if "CA" in f.replace("SAMPLE_", "").split("_")[0]]
    assert "SAMPLE_Water.kmz" in files                    # bare prefix, no AOI token
    assert "Chisago" in m["placement"]                    # says where it really is
    kml = zipfile.ZipFile(tmp_path / "SAMPLE_Water.kmz").read("doc.kml").decode()
    assert all(-94 < x < -92 and 45 < y < 46 for x, y in _coords(kml))


def test_feature_total_is_not_double_counted(tmp_path):
    """rows carry a per-layer AND a per-sector entry over the same features."""
    m = build_demo(str(tmp_path), group_by="both", log=lambda *a: None)
    assert m["features_total"] == 68
    assert sum(r["features"] for r in m["layers"]) == 2 * m["features_total"]


def test_sector_pack_document_names_every_source(tmp_path):
    """PROJECT RULES: provenance in each KML Document, and a merged pack must
    never speak for a source it did not use."""
    build_demo(str(tmp_path), aoi=parse_aoi("state:MN"), log=lambda *a: None)
    kml = zipfile.ZipFile(tmp_path / "SAMPLE_MN_Energy-Electric.kmz").read("doc.kml").decode()
    head = kml[:kml.index("<Style")]
    assert "<description>" in head
    for layer in ("power_plants", "substations", "transmission_lines"):
        assert f"overlaybuilder demo ({layer})" in head, layer
    assert "3 sources in this pack" in head

