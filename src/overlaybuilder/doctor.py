"""`overlaybuilder doctor` - probe every source an AOI would use and report.

The catalog points at dozens of government endpoints that move, get renamed,
or go behind a login. This checks them all in about a minute without
downloading any data, tells you which alternate to switch to, and warns when a
server's schema no longer carries a field the catalog maps.

Exit code: 0 = every enabled source usable, 1 = at least one dead with no
working alternate.
"""
import time
from typing import Dict, List, Optional

from .probe import AUTH, DEAD, OK, SKIP, WARN, Probe, probe_source


def check_sources(sources: List[dict], ctx, use_alternates: bool = True, log=print) -> List[dict]:
    from .build import merge_alternate
    rows = []
    for spec in sources:
        sid = spec.get("id", spec["layer"])
        t0 = time.time()
        pr = probe_source(spec, ctx)
        row = {"id": sid, "layer": spec["layer"], "driver": spec["driver"],
               "tier": spec.get("_tier", ""), "confidence": spec.get("confidence", ""),
               "probe": pr, "fallback": None, "seconds": round(time.time() - t0, 1)}
        if not pr.ok and use_alternates:
            for i, alt in enumerate(spec.get("alternates") or [], 1):
                if not isinstance(alt, dict):
                    continue
                apr = probe_source(merge_alternate(spec, alt), ctx)
                if apr.ok:
                    row["fallback"] = (f"alt{i}", apr, alt.get("url", ""))
                    break
        rows.append(row)
        log(f"  {sid:38} {pr.line()[:110]}" + ("  -> alternate works" if row["fallback"] else ""))
    return rows


def summarize(rows: List[dict]) -> Dict[str, int]:
    c = {OK: 0, WARN: 0, AUTH: 0, DEAD: 0, SKIP: 0, "recovered": 0}
    for r in rows:
        c[r["probe"].status] = c.get(r["probe"].status, 0) + 1
        if r["fallback"]:
            c["recovered"] += 1
    return c


def write_report(path: str, rows: List[dict], ctx) -> None:
    c = summarize(rows)
    out = [f"# Source health - {ctx.aoi.describe()}", "",
           f"Probed {len(rows)} sources. ok {c[OK]}, warn {c[WARN]}, auth-required {c[AUTH]}, "
           f"dead {c[DEAD]} ({c['recovered']} of them recoverable via a catalog alternate), skipped {c[SKIP]}.",
           "",
           "`warn` means the endpoint answered but something is off - usually a mapped field is missing "
           "from the server schema, so that attribute will be absent from the popup headline (the raw "
           "attribute table still shows everything the server returns).", "",
           "| source | tier | driver | status | features | detail |", "|---|---|---|---|---|---|"]
    for r in rows:
        p = r["probe"]
        n = f"{p.count:,}" if isinstance(p.count, int) else ""
        out.append(f"| `{r['id']}` | {r['tier']} | {r['driver']} | {p.status} | {n} | {p.detail[:120]} |")
    bad = [r for r in rows if not r["probe"].ok]
    if bad:
        out += ["", "## Needs attention", ""]
        for r in bad:
            p = r["probe"]
            out.append(f"### `{r['id']}` ({p.status})")
            out.append("")
            out.append(f"- endpoint: {p.url or '(n/a)'}")
            out.append(f"- detail: {p.detail}")
            if r["fallback"]:
                tag, apr, aurl = r["fallback"]
                out.append(f"- **a catalog alternate works** ({tag}): {aurl or apr.url}")
                out.append("  Builds fall back to it automatically; promote it to the primary "
                           "`url:`/`layer_id:` in the YAML to skip the failed attempt.")
            else:
                out.append("- no working alternate in the catalog. Find a replacement endpoint, then edit "
                           f"`catalog/**` for this source, or set `enabled: false` to skip it.")
            out.append("")
    warn = [r for r in rows if r["probe"].status == WARN and r["probe"].detail]
    if warn:
        out += ["## Schema warnings", ""]
        for r in warn:
            out.append(f"- `{r['id']}`: {r['probe'].detail}")
        out += ["", "Fix by editing that source's `fields:` block to the server's real column names "
                    "(`overlaybuilder probe <url>/<layer> --sample` prints them).", ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
