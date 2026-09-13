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
    assert "power_plants.kmz" in lf and not [f for f in lf if f.startswith("SAMPLE_")]

    build_demo(str(tmp_path / "b"), group_by="both", log=lambda *a: None)
    bf = set(os.listdir(tmp_path / "b"))
    assert "power_plants.kmz" in bf and "SAMPLE_Energy-Electric.kmz" in bf

    with pytest.raises(ValueError, match="group_by"):
        build_demo(str(tmp_path / "x"), group_by="nope", log=lambda *a: None)
