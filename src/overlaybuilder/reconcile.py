"""Cross-source accuracy check.

When two sources describe the same kind of thing (catalog `entity:` e.g.
power_plant from EIA and from OSM), match features within a radius and compare
the canonical numbers. The report lists agreements, disagreements (with the
delta), and features only one source knows about - so a user can see where the
authoritative and the crowd-sourced views diverge instead of trusting either
blindly. Matches also stamp `xcheck` on the features ("agree", "capacity Δ 12%",
"unmatched") so the note shows in ATAK.
"""
import math
from typing import Dict, List, Tuple

from .aoi import representative_point
from .model import LayerResult
from .normalize import fmt_value

COMPARE_KEYS = ("capacity_mw", "voltage_kv", "max_voltage_kv", "height_ft", "beds")
DEFAULT_RADIUS_M = 800.0


def _dist_m(a, b) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    dx = math.radians(b[0] - a[0]) * math.cos(lat) * 6371000
    dy = math.radians(b[1] - a[1]) * 6371000
    return math.hypot(dx, dy)


def _points(res: LayerResult):
    out = []
    for f in res.features:
        if (f.properties or {}).get("_shape") == "marker":
            continue
        rp = representative_point(f.geometry) if f.geometry else None
        if rp:
            out.append((rp, f))
    return out


def _grid(pts, cell=0.02):
    g: Dict[Tuple[int, int], list] = {}
    for rp, f in pts:
        g.setdefault((int(rp[0] // cell), int(rp[1] // cell)), []).append((rp, f))
    return g


def _near(grid, rp, cell=0.02):
    cx, cy = int(rp[0] // cell), int(rp[1] // cell)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for item in grid.get((cx + dx, cy + dy), []):
                yield item


def compare_pair(a: LayerResult, b: LayerResult, radius_m: float = DEFAULT_RADIUS_M) -> dict:
    pa, pb = _points(a), _points(b)
    gb = _grid(pb)
    used_b = set()
    matched, only_a = [], []
    for rp, fa in pa:
        best, bd = None, radius_m + 1
        for rq, fb in _near(gb, rp):
            if id(fb) in used_b:
                continue
            d = _dist_m(rp, rq)
            if d < bd:
                best, bd = (rq, fb), d
        if best is None:
            only_a.append(fa)
            fa.properties["xcheck"] = f"unmatched in {b.provenance.source_name}"
            continue
        used_b.add(id(best[1]))
        fb = best[1]
        diffs = []
        for k in COMPARE_KEYS:
            va, vb = fa.properties.get(k), fb.properties.get(k)
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and va and vb:
                pct = abs(va - vb) / max(abs(va), abs(vb)) * 100
                if pct > 5:
                    diffs.append((k, va, vb, pct))
        note = "agree" if not diffs else "; ".join(
            f"{k} Δ {pct:.0f}% ({fmt_value(k, va)} vs {fmt_value(k, vb)})" for k, va, vb, pct in diffs)
        fa.properties["xcheck"] = f"{note} [{b.provenance.source_name}, {bd:.0f} m]"
        fb.properties["xcheck"] = f"{note} [{a.provenance.source_name}, {bd:.0f} m]"
        matched.append((fa, fb, bd, diffs))
    only_b = [fb for rq, fb in pb if id(fb) not in used_b]
    for fb in only_b:
        fb.properties["xcheck"] = f"unmatched in {a.provenance.source_name}"
    return {"a": a, "b": b, "matched": matched, "only_a": only_a, "only_b": only_b}


def reconcile_layers(results: List[LayerResult], specs: Dict[str, dict]) -> dict:
    by_entity: Dict[str, List[LayerResult]] = {}
    for r in results:
        sp = specs.get(getattr(r, "doc_key", r.logical)) or {}
        ent = sp.get("entity")
        if ent:
            by_entity.setdefault(ent, []).append(r)
    pairs = []
    for ent, rs in by_entity.items():
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                sp = specs.get(getattr(rs[i], "doc_key", rs[i].logical)) or {}
                pairs.append((ent, compare_pair(rs[i], rs[j], float(sp.get("match_radius_m", DEFAULT_RADIUS_M)))))
    return {"pairs": pairs}


def _nm(f):
    p = f.properties or {}
    return p.get("name") or p.get("source_id") or "(unnamed)"


def write_report(path: str, rec: dict, ctx) -> None:
    lines = [f"# Cross-source check - {ctx.aoi.describe()}", "",
             "Features of the same entity type from two sources, matched by proximity. "
             "Deltas >5% on capacity/voltage/height/beds are listed. Neither source is "
             "assumed correct; use this to spot stale or mis-tagged records.", ""]
    for ent, pr in rec["pairs"]:
        a, b = pr["a"], pr["b"]
        lines += [f"## {ent}: {a.provenance.source_name}  vs  {b.provenance.source_name}", "",
                  f"| | count |", "|---|---|",
                  f"| matched | {len(pr['matched'])} |",
                  f"| agree (within 5%) | {sum(1 for m in pr['matched'] if not m[3])} |",
                  f"| disagree | {sum(1 for m in pr['matched'] if m[3])} |",
                  f"| only in {a.provenance.source_name} | {len(pr['only_a'])} |",
                  f"| only in {b.provenance.source_name} | {len(pr['only_b'])} |", ""]
        dis = [m for m in pr["matched"] if m[3]]
        if dis:
            lines += ["### Disagreements", "", "| A | B | dist m | field | A | B | Δ% |", "|---|---|---|---|---|---|---|"]
            for fa, fb, d, diffs in dis:
                for k, va, vb, pct in diffs:
                    lines.append(f"| {_nm(fa)} | {_nm(fb)} | {d:.0f} | {k} | {fmt_value(k, va)} | {fmt_value(k, vb)} | {pct:.0f} |")
            lines.append("")
        for label, items in ((f"Only in {a.provenance.source_name}", pr["only_a"]),
                             (f"Only in {b.provenance.source_name}", pr["only_b"])):
            if items:
                lines += [f"### {label} ({len(items)})", ""]
                lines += [f"- {_nm(f)}" + (f" - {fmt_value('capacity_mw', f.properties['capacity_mw'])}"
                                          if f.properties.get('capacity_mw') else "") for f in items[:200]]
                if len(items) > 200:
                    lines.append(f"- ... {len(items) - 200} more")
                lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
