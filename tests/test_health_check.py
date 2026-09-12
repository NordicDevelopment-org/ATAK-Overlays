"""The weekly catalog health check must not report false deaths (it gates CI)."""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("health_check", os.path.join(ROOT, "scripts", "health_check.py"))
health = importlib.util.module_from_spec(spec)
sys.modules["health_check"] = health
spec.loader.exec_module(health)


def test_no_source_is_pinged_with_an_unrendered_template(monkeypatch, capsys):
    pinged = []
    monkeypatch.setattr(health, "ping", lambda url, arcgis: pinged.append(url) or "ok 200")
    monkeypatch.setattr(health, "ping_monthly", lambda url, arcgis: pinged.append(url) or "ok 200")
    assert health.main() == 0
    assert pinged, "nothing was probed"
    for url in pinged:
        assert "{" not in url and "$" not in url, url


def test_disabled_sources_are_skipped(monkeypatch, capsys):
    monkeypatch.setattr(health, "ping", lambda url, arcgis: "ok 200")
    monkeypatch.setattr(health, "ping_monthly", lambda url, arcgis: "ok 200")
    health.main()
    out = capsys.readouterr().out
    assert "SKIP disabled" in out
    assert "developer.nrel.gov" not in out            # enabled: false, and needs an API key


def test_a_dead_source_still_fails_the_check(monkeypatch):
    monkeypatch.setattr(health, "ping", lambda url, arcgis: "DEAD 404")
    monkeypatch.setattr(health, "ping_monthly", lambda url, arcgis: "DEAD 404")
    assert health.main() == 1                         # the point of the script is a non-zero exit


def test_monthly_urls_try_several_months(monkeypatch):
    seen = []

    def fake_ping(url, arcgis):
        seen.append(url)
        return "ok 200" if "generator" in url and len(seen) > 1 else "DEAD 404"
    monkeypatch.setattr(health, "ping", fake_ping)
    status = health.ping_monthly("https://x/{month}_generator{year}.xlsx", False)
    assert status.startswith("ok") and len(seen) >= 2
