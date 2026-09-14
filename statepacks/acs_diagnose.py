#!/usr/bin/env python3
"""
acs_diagnose.py - find out why the Census ACS API is not answering.

The builder reported:
    ACS 2023 unavailable (Expecting value: line 1 column 1 (char 0))

That is json.loads() choking on a body that is not JSON at all - an HTML error
page, a redirect, or an empty response. The status code and the first bytes of
the body say which, and neither was visible before.

The prime suspect is URL ENCODING. urlencode() percent-escapes the colon and
asterisk in `for=county:*`, sending `for=county%3A%2A`. Most APIs decode that
fine; the Census API is known to be fussy about it. This tries several
encodings of the same query, plus several vintages, and prints what actually
comes back for each.

Standard library only.

USAGE
    python3 acs_diagnose.py
    python3 acs_diagnose.py --state MN --years 2023 2022 2021
"""
import argparse
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.census.gov/data/{year}/acs/acs5"
GET = "NAME,B01003_001E,B25001_001E"       # name, total population, housing units
CTX = ssl.create_default_context()
UA = {"User-Agent": "atak-statepacks-acs/1.0"}

STATE_FIPS = {"MN": "27", "WI": "55", "IA": "19", "TX": "48", "CA": "06",
              "ND": "38", "SD": "46", "MI": "26", "IL": "17"}


def attempt(label, url, timeout=45):
    """Fetch and report what really came back, without assuming it is JSON."""
    print(f"  {label}")
    print(f"    {url}")
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            status = r.status
            ctype = r.headers.get("Content-Type", "?")
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print(f"    HTTP {e.code}  {e.headers.get('Content-Type', '?')}")
        print(f"    body[:300]: {body[:300]!r}")
        return None
    except Exception as e:                          # noqa: BLE001
        print(f"    FAILED: {e}")
        return None

    print(f"    HTTP {status}  {ctype}  {len(body)} bytes")
    if not body.strip():
        print("    body is EMPTY")
        return None
    try:
        data = json.loads(body)
    except ValueError as e:
        low = body.lstrip()[:400].lower()
        if low.startswith("<"):
            if "missing key" in low:
                print("    HTML: **MISSING KEY** - the API needs a key and none was sent")
            elif "invalid key" in low:
                print("    HTML: **INVALID KEY** - the key was sent but rejected")
            else:
                print("    HTML page returned instead of data")
            print(f"    get one free: https://api.census.gov/data/key_signup.html")
        else:
            print(f"    NOT JSON: {e}")
        print(f"    body[:200]: {body[:200]!r}")
        return None
    if isinstance(data, list) and data:
        print(f"    OK - {len(data) - 1} data rows")
        print(f"    header: {data[0]}")
        if len(data) > 1:
            print(f"    first:  {data[1]}")
        return data
    print(f"    JSON but unexpected shape: {str(data)[:200]}")
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Diagnose the Census ACS API.")
    ap.add_argument("--state", default="MN")
    ap.add_argument("--years", nargs="*", type=int, default=[2023, 2022, 2021])
    a = ap.parse_args(argv)
    sfp = STATE_FIPS.get(a.state.upper(), "27")

    import os
    key = (os.environ.get("CENSUS_API_KEY") or "").strip()
    if not key:
        try:
            with open(os.path.join(os.path.expanduser("~"), ".config",
                                   "atak-statepacks", "census_key")) as fh:
                key = fh.read().strip()
        except OSError:
            key = ""
    print(f"KEY: {'found (...' + key[-4:] + ')' if key else 'NONE CONFIGURED'}")
    if not key:
        print("     The Census API requires one. Free and instant:")
        print("       https://api.census.gov/data/key_signup.html")
        print("     then:  export CENSUS_API_KEY=your_key_here")
    print()

    print("=" * 66)
    print("A. ENCODING - same query, four ways of building the URL (year 2023)")
    print("=" * 66)
    base = BASE.format(year=2023)
    params = {"get": GET, "for": "county:*", "in": f"state:{sfp}"}
    if key:
        params["key"] = key

    winners = []
    v = attempt("1. urlencode() - what the builder does now",
                base + "?" + urllib.parse.urlencode(params))
    if v:
        winners.append("urlencode")
    v = attempt("2. raw, nothing escaped",
                f"{base}?get={GET}&for=county:*&in=state:{sfp}" + (f"&key={key}" if key else ""))
    if v:
        winners.append("raw")
    v = attempt("3. urlencode with ':' and '*' left alone",
                base + "?" + urllib.parse.urlencode(params, safe=":*"))
    if v:
        winners.append("safe-colon-star")
    v = attempt("4. explicit wildcard spelling for=county:%2A",
                f"{base}?get={GET}&for=county:%2A&in=state:{sfp}" + (f"&key={key}" if key else ""))
    if v:
        winners.append("pct-star")

    print()
    print("=" * 66)
    print("B. VINTAGE - does another year answer? (raw encoding)")
    print("=" * 66)
    good_years = []
    for y in a.years:
        if attempt(f"ACS 5-year {y}",
                   f"{BASE.format(year=y)}?get={GET}&for=county:*&in=state:{sfp}" + (f"&key={key}" if key else "")):
            good_years.append(y)

    print()
    print("=" * 66)
    print("C. REACHABILITY - is api.census.gov answering at all?")
    print("=" * 66)
    attempt("dataset list (tiny request, no parameters)",
            "https://api.census.gov/data.json")

    print()
    print("=" * 66)
    print("VERDICT")
    print("=" * 66)
    if winners:
        print(f"  Encodings that worked: {', '.join(winners)}")
    else:
        print("  NO encoding worked - this is not a percent-escaping problem.")
    if good_years:
        print(f"  Years that answered:   {good_years}")
    else:
        print("  No ACS vintage answered.")
    if not winners and not good_years:
        print()
        print("  If section C also failed, api.census.gov is unreachable from this")
        print("  network - try mobile data instead of wifi. If C worked but A and B")
        print("  did not, paste this whole output back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
