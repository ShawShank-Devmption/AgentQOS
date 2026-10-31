"""Politeness guarantees of the AGENT_BULK crawler, against a real local HTTP server."""

import io
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from harness.agents import crawler
from harness.agents.crawler import HostRateLimiter, crawl, extract_links

PAGES = {
    "/": '<a href="/a">a</a> <a href="/b#frag">b</a> <a href="/private/x">p</a>'
    ' <a href="https://elsewhere.example/">ext</a> <a href="/search?q=1">q</a>',
    "/a": '<a href="/c">c</a> <a href="/">home</a>',
    "/b": '<a href="/c">c</a>',
    "/c": "<p>leaf</p>",
    "/private/x": "<p>secret</p>",
}


class _Site(BaseHTTPRequestHandler):
    requests: list[tuple[float, str, str]] = []

    def do_GET(self) -> None:  # noqa: N802 -- stdlib handler interface
        self.requests.append((time.monotonic(), self.path, self.headers.get("User-Agent", "")))
        if self.path == "/robots.txt":
            body, kind = b"User-agent: *\nDisallow: /private/\n", "text/plain"
        elif self.path in PAGES:
            body, kind = PAGES[self.path].encode(), "text/html"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def site() -> Iterator[str]:
    _Site.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _crawl(
    tmp_path: Path, host: str, max_pages: int = 50, interval: float = 0.05
) -> tuple[crawler.CrawlReport, list[dict]]:
    log = io.StringIO()
    report = crawl(
        [f"http://{host}/"],
        crawlers=4,
        max_pages=max_pages,
        max_seconds=30,
        log=log,
        limiter=HostRateLimiter(tmp_path / "rate", interval),
        allowed_hosts=frozenset({host}),
    )
    return report, [json.loads(line) for line in log.getvalue().splitlines()]


def test_crawl_obeys_robots_stays_on_site_and_identifies_itself(tmp_path: Path, site: str) -> None:
    report, records = _crawl(tmp_path, site)
    paths = [path for _, path, _ in _Site.requests]
    assert sorted(paths) == ["/", "/a", "/b", "/c", "/robots.txt"]
    assert report.robots_skipped == 1
    assert report.pages[site] == 4
    assert {agent for _, _, agent in _Site.requests} == {crawler.USER_AGENT}
    assert all(record["status"] == 200 for record in records)


def test_requests_to_one_site_are_spaced_by_the_interval(tmp_path: Path, site: str) -> None:
    _crawl(tmp_path, site, interval=0.2)
    times = sorted(at for at, _, _ in _Site.requests)
    gaps = [later - earlier for earlier, later in zip(times, times[1:], strict=False)]
    assert min(gaps) >= 0.18  # 4 concurrent crawlers, yet one request per interval


def test_page_cap_is_never_exceeded(tmp_path: Path, site: str) -> None:
    report, records = _crawl(tmp_path, site, max_pages=2)
    assert sum(report.pages.values()) == len(records) == 2


def test_seed_outside_allowed_sites_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not on an allowed site"):
        crawl(
            ["https://example.com/"],
            crawlers=1,
            max_pages=1,
            max_seconds=1,
            log=io.StringIO(),
            limiter=HostRateLimiter(tmp_path / "rate", 0.01),
        )


def test_rate_slots_are_shared_between_independent_limiters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Separate crawler processes share the per-site schedule through the state directory."""
    sleeps: list[float] = []
    monkeypatch.setattr(crawler.time, "time", lambda: 1000.0)
    monkeypatch.setattr(crawler.time, "sleep", sleeps.append)
    first, second = (HostRateLimiter(tmp_path, 1.0) for _ in range(2))
    first.wait("en.wikipedia.org")
    second.wait("en.wikipedia.org")
    second.wait("docs.python.org")
    first.wait("en.wikipedia.org", min_interval_s=5.0)
    assert sleeps == [0.0, 1.0, 0.0, 2.0]


def test_links_are_same_site_without_fragments_or_queries() -> None:
    links = extract_links("https://docs.python.org/3/index.html", PAGES["/"])
    assert links == [
        "https://docs.python.org/a",
        "https://docs.python.org/b",
        "https://docs.python.org/private/x",
    ]


@pytest.mark.parametrize(
    "args",
    [
        ["--seed", "https://example.com/", "--out", "x.jsonl"],
        ["--seed", "https://en.wikipedia.org/wiki/X", "--crawlers", "9", "--out", "x.jsonl"],
        ["--seed", "https://en.wikipedia.org/wiki/X", "--max-minutes", "10", "--out", "x.jsonl"],
    ],
)
def test_cli_rejects_out_of_policy_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    try:
        code = crawler.main(args)
    except SystemExit as exc:
        code = exc.code
    assert code == 2
