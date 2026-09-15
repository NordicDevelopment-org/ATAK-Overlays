"""The weekly catalog health check must not report false deaths (it gates CI)."""
import importlib.util
import os
import sys
from urllib.error import URLError

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


# --------------------------------------------------------------------------
# alternates: fallback. build.py already falls through to a source's
# alternates when its primary fails - this script used to test only the
# primary, so a source whose primary was dead but whose alternate genuinely
# worked (exactly the case alternates: exists for) was still reported dead.
# --------------------------------------------------------------------------
def _fake_source(**kw):
    base = {"_file": "x.yaml", "layer": "test_layer", "driver": "arcgis",
            "url": "https://dead.example/svc", "layer_id": 0}
    base.update(kw)
    return base


def test_a_dead_primary_with_a_working_alternate_is_not_counted_dead(monkeypatch, capsys):
    source = _fake_source(alternates=[
        {"driver": "file", "url": "https://alt.example/data.zip"}])
    monkeypatch.setattr(health.catalog, "all_sources", lambda path: [source])
    monkeypatch.setattr(health, "ping",
                        lambda url, arcgis: "DEAD 404" if "dead.example" in url else "ok 200")
    assert health.main() == 0
    out = capsys.readouterr().out
    assert "ok (alternate)" in out


def test_a_dead_primary_whose_alternates_are_also_dead_is_still_counted(monkeypatch):
    source = _fake_source(alternates=[
        {"driver": "file", "url": "https://also-dead.example/data.zip"}])
    monkeypatch.setattr(health.catalog, "all_sources", lambda path: [source])
    monkeypatch.setattr(health, "ping", lambda url, arcgis: "DEAD 404")
    assert health.main() == 1


def test_a_source_with_no_alternates_is_unaffected_by_the_fallback(monkeypatch):
    monkeypatch.setattr(health.catalog, "all_sources", lambda path: [_fake_source()])
    monkeypatch.setattr(health, "ping", lambda url, arcgis: "DEAD 404")
    assert health.main() == 1


# --------------------------------------------------------------------------
# Timeout retry. A timeout means the server was slow, not that it refused or
# does not exist - unlike a 403/404/DNS failure, which are a definite answer
# retrying cannot change.
# --------------------------------------------------------------------------
class _FakeResponse:
    status = 200

    def read(self, n=-1):
        return b'{"fields": []}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_timeout_is_retried_and_can_still_succeed(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return _FakeResponse()

    monkeypatch.setattr(health, "urlopen", fake_urlopen)
    monkeypatch.setattr(health.time, "sleep", lambda s: None)
    status = health.ping("https://example.test/svc", True)
    assert status.startswith("ok"), status
    assert len(calls) == 2, "must retry once after a timeout, not give up immediately"


def test_a_permanent_timeout_eventually_reports_dead(monkeypatch):
    def always_times_out(req, timeout=None):
        raise TimeoutError("timed out")
    monkeypatch.setattr(health, "urlopen", always_times_out)
    monkeypatch.setattr(health.time, "sleep", lambda s: None)
    status = health.ping("https://example.test/svc", True)
    assert status.startswith("DEAD")


def test_a_definite_error_is_not_retried(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        raise URLError("Name or service not known")

    monkeypatch.setattr(health, "urlopen", fake_urlopen)
    status = health.ping("https://example.test/svc", True)
    assert status.startswith("DEAD")
    assert len(calls) == 1, "a non-timeout error must not be retried"
