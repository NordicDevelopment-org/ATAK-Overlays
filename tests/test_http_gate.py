"""Per-host concurrency cap and politeness spacing (no real network)."""
import threading
import time

import overlaybuilder.http as http


class _FakeResp:
    def __init__(self, body=b"x"):
        self._b = body

    def read(self):
        time.sleep(0.05)
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch(monkeypatch, tracker):
    def fake_urlopen(req, timeout=None, context=None):
        host = req.full_url.split("//", 1)[1].split("/", 1)[0]
        with tracker["lock"]:
            tracker["live"][host] = tracker["live"].get(host, 0) + 1
            tracker["peak"][host] = max(tracker["peak"].get(host, 0), tracker["live"][host])
            tracker["times"].setdefault(host, []).append(time.time())
        try:
            return _FakeResp()
        finally:
            pass

    class _Wrap(_FakeResp):
        pass

    def fake(req, timeout=None, context=None):
        host = req.full_url.split("//", 1)[1].split("/", 1)[0]
        with tracker["lock"]:
            tracker["live"][host] = tracker["live"].get(host, 0) + 1
            tracker["peak"][host] = max(tracker["peak"].get(host, 0), tracker["live"][host])
            tracker["times"].setdefault(host, []).append(time.time())

        class R(_FakeResp):
            def __exit__(s, *a):
                with tracker["lock"]:
                    tracker["live"][host] -= 1
                return False
        return R()
    monkeypatch.setattr(http, "urlopen", fake)


def _tracker():
    return {"lock": threading.Lock(), "live": {}, "peak": {}, "times": {}}


def _hammer(urls, **kw):
    threads = [threading.Thread(target=lambda u=u: http.http_get(u, cache=False, **kw)) for u in urls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_per_host_concurrency_is_capped(monkeypatch):
    tr = _tracker()
    _patch(monkeypatch, tr)
    http.set_max_per_host(2)
    _hammer([f"https://one.example/{i}" for i in range(8)])
    assert tr["peak"]["one.example"] <= 2


def test_hosts_are_independent(monkeypatch):
    tr = _tracker()
    _patch(monkeypatch, tr)
    http.set_max_per_host(1)
    _hammer([f"https://h{i}.example/x" for i in range(4)])
    assert set(tr["peak"].values()) == {1} and len(tr["peak"]) == 4


def test_min_interval_spaces_requests_across_threads(monkeypatch):
    tr = _tracker()
    _patch(monkeypatch, tr)
    http.set_max_per_host(4)
    _hammer([f"https://slow.example/{i}" for i in range(3)], min_interval=0.25)
    stamps = sorted(tr["times"]["slow.example"])
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(g >= 0.2 for g in gaps), gaps          # politeness held under concurrency
    http.set_max_per_host(2)
