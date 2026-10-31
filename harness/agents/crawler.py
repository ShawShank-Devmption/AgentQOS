"""Burst of small polite crawlers that headless Claude launches in AGENT_BULK sessions (§9).

One run starts N crawlers at the same moment, each from a random start page on a public site,
each fetching a few pages of that site. Politeness is enforced here, not left to the agent: only the
sites in SEED_URLS, robots.txt obeyed (including Crawl-delay), at most one request per second per
site across every crawler process of this user, an identifying User-Agent, and hard page/time caps.
Pages are not stored: the traffic is the product, so only per-request metadata is written.
"""

from __future__ import annotations

import argparse
import fcntl
import io
import json
import logging
import queue
import random
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Final, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

LOGGER = logging.getLogger(__name__)
# Start pages on sites that permit crawling at a polite rate; spread over many hosts so that
# crawlers started together mostly get a site to themselves.
SEED_URLS: Final = (
    "https://en.wikipedia.org/wiki/Computer_network",
    "https://en.wikipedia.org/wiki/Internet_protocol_suite",
    "https://en.wikipedia.org/wiki/Cryptography",
    "https://en.wikipedia.org/wiki/Operating_system",
    "https://de.wikipedia.org/wiki/Rechnernetz",
    "https://de.wikipedia.org/wiki/Programmiersprache",
    "https://fr.wikipedia.org/wiki/Internet",
    "https://fr.wikipedia.org/wiki/Cryptographie",
    "https://es.wikipedia.org/wiki/Red_de_computadoras",
    "https://es.wikipedia.org/wiki/Sistema_operativo",
    "https://it.wikipedia.org/wiki/Rete_di_computer",
    "https://it.wikipedia.org/wiki/Sistema_operativo",
    "https://en.wiktionary.org/wiki/network",
    "https://en.wiktionary.org/wiki/protocol",
    "https://en.wikibooks.org/wiki/Python_Programming",
    "https://en.wikibooks.org/wiki/C_Programming",
    "https://docs.python.org/3/library/socket.html",
    "https://docs.python.org/3/library/asyncio.html",
    "https://docs.python.org/3/tutorial/index.html",
    "https://www.rfc-editor.org/rfc/rfc9293.html",
    "https://www.rfc-editor.org/rfc/rfc9000.html",
    "https://www.rfc-editor.org/rfc/rfc8446.html",
)
ALLOWED_HOSTS: Final = frozenset(urlsplit(url).netloc for url in SEED_URLS)
USER_AGENT: Final = (
    "AgentAwareNetworking-crawler/0.1 (university research traffic capture; "
    "+https://github.com/ShawShank-Devmption/AgentQOS)"
)
MIN_INTERVAL_S: Final = 1.0
MAX_CRAWLERS: Final = 16
MAX_PAGES_EACH: Final = 50
MAX_SECONDS: Final = 180  # hard stop for one burst; Claude's Bash tool allows up to 10 minutes
MAX_PAGE_BYTES: Final = 2_000_000
TIMEOUT_S: Final = 15.0
RATE_DIR: Final = Path(tempfile.gettempdir()) / "aan-crawler-rate"


@dataclass(frozen=True)
class Response:
    """One HTTP reply, truncated to MAX_PAGE_BYTES."""

    status: int
    content_type: str
    body: bytes


Fetch = Callable[[str], Response]


def http_get(url: str) -> Response:
    """GET `url` with the identifying User-Agent; HTTP errors become a status, not an exception."""
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    try:
        with urlopen(request, timeout=TIMEOUT_S) as reply:
            return Response(
                reply.status, reply.headers.get_content_type(), reply.read(MAX_PAGE_BYTES)
            )
    except HTTPError as exc:
        return Response(exc.code, "", b"")


class HostRateLimiter:
    """Spaces requests to each host by at least the interval, across threads and processes."""

    def __init__(self, state_dir: Path = RATE_DIR, interval_s: float = MIN_INTERVAL_S) -> None:
        self.state_dir = state_dir
        self.interval_s = interval_s
        state_dir.mkdir(parents=True, exist_ok=True)

    def wait(self, host: str, min_interval_s: float = 0.0) -> None:
        """Reserve the host's next request slot under a file lock, then sleep until it."""
        interval = max(self.interval_s, min_interval_s)
        with (self.state_dir / host.replace(":", "_")).open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            text = handle.read().strip()
            now = time.time()
            slot = max(now, float(text) if text else 0.0)
            handle.seek(0)
            handle.truncate()
            handle.write(repr(slot + interval))
        time.sleep(max(0.0, slot - now))


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs.extend(value for name, value in attrs if name == "href" and value)


def extract_links(base_url: str, html: str) -> list[str]:
    """Same-site links of a page, without fragments or query strings."""
    parser = _PageParser()
    parser.feed(html)
    base = urlsplit(base_url)
    links = []
    for href in parser.hrefs:
        url = urldefrag(urljoin(base_url, href)).url
        parts = urlsplit(url)
        if parts.scheme == base.scheme and parts.netloc == base.netloc and not parts.query:
            links.append(url)
    return links


class RobotsCache:
    """robots.txt per host, fetched once with our User-Agent and the same rate limit."""

    def __init__(self, limiter: HostRateLimiter, fetch: Fetch) -> None:
        self._limiter = limiter
        self._fetch = fetch
        self._rules: dict[str, RobotFileParser] = {}
        self._lock = threading.Lock()

    def check(self, url: str) -> tuple[bool, float]:
        """Return (allowed, crawl delay in seconds) for `url`."""
        parts = urlsplit(url)
        with self._lock:
            rules = self._rules.get(parts.netloc)
            if rules is None:
                rules = self._load(f"{parts.scheme}://{parts.netloc}/robots.txt", parts.netloc)
                self._rules[parts.netloc] = rules
        delay = rules.crawl_delay(USER_AGENT)
        return rules.can_fetch(USER_AGENT, url), float(delay or 0.0)

    def _load(self, robots_url: str, host: str) -> RobotFileParser:
        rules = RobotFileParser(robots_url)
        self._limiter.wait(host)
        try:
            reply = self._fetch(robots_url)
        except (URLError, OSError) as exc:
            LOGGER.warning("robots.txt unreachable for %s (%s); not crawling it", host, exc)
            rules.disallow_all = True
            return rules
        if reply.status == 200:
            rules.parse(reply.body.decode("utf-8", "replace").splitlines())
        elif 400 <= reply.status < 500:
            rules.allow_all = True  # no robots.txt: crawling permitted
        else:
            rules.disallow_all = True  # server trouble: stay away
        return rules


@dataclass
class CrawlReport:
    """Per-host totals of one crawl."""

    pages: Counter[str] = field(default_factory=Counter)
    bytes: Counter[str] = field(default_factory=Counter)
    errors: Counter[str] = field(default_factory=Counter)
    robots_skipped: int = 0


@dataclass
class _Shared:
    frontier: queue.Queue[str]
    seen: set[str]
    report: CrawlReport
    log: TextIO
    lock: threading.Lock = field(default_factory=threading.Lock)
    fetched: int = 0
    # URLs queued or being visited; a URL leaves only after its links are queued, so an empty
    # queue with work pending never ends a crawler early.
    pending: int = 0

    def enqueue(self, url: str) -> None:
        """Queue `url` once; call with the lock held."""
        if url not in self.seen:
            self.seen.add(url)
            self.pending += 1
            self.frontier.put(url)


def crawl(
    seeds: Iterable[str],
    crawlers: int,
    max_pages: int,
    max_seconds: float,
    log: TextIO,
    limiter: HostRateLimiter,
    fetch: Fetch = http_get,
    allowed_hosts: frozenset[str] = ALLOWED_HOSTS,
) -> CrawlReport:
    """Run `crawlers` concurrent crawlers breadth-first from `seeds` until a cap is reached.

    Raises:
        ValueError: If a seed is not on an allowed site.
    """
    shared = _Shared(queue.Queue(), set(), CrawlReport(), log)
    for seed in seeds:
        if urlsplit(seed).netloc not in allowed_hosts:
            raise ValueError(f"seed {seed} is not on an allowed site: {sorted(allowed_hosts)}")
        shared.enqueue(seed)
    robots = RobotsCache(limiter, fetch)
    stop_at = time.monotonic() + max_seconds
    threads = [
        threading.Thread(
            target=_crawler, args=(shared, robots, limiter, fetch, max_pages, stop_at), daemon=True
        )
        for _ in range(crawlers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return shared.report


def _crawler(
    shared: _Shared,
    robots: RobotsCache,
    limiter: HostRateLimiter,
    fetch: Fetch,
    max_pages: int,
    stop_at: float,
) -> None:
    while time.monotonic() < stop_at:
        try:
            url = shared.frontier.get(timeout=0.2)
        except queue.Empty:
            with shared.lock:
                if shared.pending == 0 or shared.fetched >= max_pages:
                    return
            continue
        try:
            _visit(shared, robots, limiter, fetch, max_pages, url)
        finally:
            with shared.lock:
                shared.pending -= 1


def _visit(
    shared: _Shared,
    robots: RobotsCache,
    limiter: HostRateLimiter,
    fetch: Fetch,
    max_pages: int,
    url: str,
) -> None:
    host = urlsplit(url).netloc
    allowed, delay = robots.check(url)
    with shared.lock:
        if not allowed:
            shared.report.robots_skipped += 1
            return
        if shared.fetched >= max_pages:
            return
        shared.fetched += 1
    limiter.wait(host, delay)
    started = time.time()
    try:
        reply = fetch(url)
    except (URLError, OSError) as exc:
        LOGGER.warning("fetch failed for %s: %s", url, exc)
        with shared.lock:
            shared.report.errors[host] += 1
        return
    record = {"url": url, "status": reply.status, "bytes": len(reply.body), "time_s": started}
    with shared.lock:
        shared.report.pages[host] += 1
        shared.report.bytes[host] += len(reply.body)
        shared.log.write(json.dumps(record) + "\n")
    if reply.status != 200 or reply.content_type != "text/html":
        return
    for link in extract_links(url, reply.body.decode("utf-8", "replace")):
        with shared.lock:
            shared.enqueue(link)


def pick_seeds(count: int, rng: random.Random, seeds: Sequence[str] = SEED_URLS) -> list[str]:
    """Pick `count` random start pages, spread over as many different sites as possible."""
    by_host: dict[str, list[str]] = {}
    for url in seeds:
        by_host.setdefault(urlsplit(url).netloc, []).append(url)
    pools = [rng.sample(urls, len(urls)) for urls in by_host.values()]
    rng.shuffle(pools)
    picks: list[str] = []
    while len(picks) < count and any(pools):
        for pool in pools:
            if pool and len(picks) < count:
                picks.append(pool.pop())
    return picks


class _LockedLog(io.TextIOBase):
    """Serialises writes from concurrent crawls into one log."""

    def __init__(self, target: TextIO) -> None:
        super().__init__()
        self._target = target
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        with self._lock:
            return self._target.write(text)


def burst(
    count: int,
    pages_each: int,
    rng: random.Random,
    log: TextIO,
    limiter: HostRateLimiter,
    fetch: Fetch = http_get,
    seeds: Sequence[str] = SEED_URLS,
    allowed_hosts: frozenset[str] = ALLOWED_HOSTS,
) -> tuple[list[str], CrawlReport]:
    """Start `count` independent small crawlers at once, each from its own random start page.

    Returns:
        The chosen start pages and the merged report.
    """
    starts = pick_seeds(count, rng, seeds)
    shared_log = _LockedLog(log)
    reports: list[CrawlReport] = []

    def run(start: str) -> None:
        report = crawl(
            [start], 1, pages_each, MAX_SECONDS, shared_log, limiter, fetch, allowed_hosts
        )
        reports.append(report)

    threads = [threading.Thread(target=run, args=(start,), daemon=True) for start in starts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    merged = CrawlReport()
    for report in reports:
        merged.pages.update(report.pages)
        merged.bytes.update(report.bytes)
        merged.errors.update(report.errors)
        merged.robots_skipped += report.robots_skipped
    return starts, merged


def _bounded(name: str, low: int, high: int) -> Callable[[str], int]:
    def parse(text: str) -> int:
        value = int(text)
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"{name} must be {low}-{high}")
        return value

    return parse


def main(argv: Sequence[str] | None = None) -> int:
    """CLI used by headless Claude; logs a JSON summary when the burst ends."""
    parser = argparse.ArgumentParser(
        description="Start N small polite crawlers at once, each from a random public start page "
        f"(robots.txt obeyed, <= {MIN_INTERVAL_S:g} request/s per site)."
    )
    parser.add_argument("--crawlers", type=_bounded("crawlers", 1, MAX_CRAWLERS), default=8)
    parser.add_argument("--pages-each", type=_bounded("pages-each", 1, MAX_PAGES_EACH), default=20)
    parser.add_argument("--random-seed", type=int, required=True, help="picks the start pages")
    parser.add_argument("--out", type=Path, required=True, help="new JSONL file of requests")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s crawler: %(message)s")
    try:
        with args.out.open("x", encoding="utf-8") as log:
            starts, report = burst(
                args.crawlers,
                args.pages_each,
                random.Random(args.random_seed),
                log,
                HostRateLimiter(),
            )
    except FileExistsError as exc:
        LOGGER.error("%s", exc)
        return 2
    summary = {
        "random_seed": args.random_seed,
        "start_pages": starts,
        "pages": dict(report.pages),
        "bytes": dict(report.bytes),
        "errors": dict(report.errors),
        "robots_skipped": report.robots_skipped,
    }
    LOGGER.info("summary %s", json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
