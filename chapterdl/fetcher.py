from __future__ import annotations

import gzip
import http.client
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, TypeVar
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
CHALLENGE_STATUSES = frozenset({403, 429, 503})
CHALLENGE_MARKERS = (
    "just a moment",
    "cf_chl_opt",
    "cf-chl-",
    "enable javascript and cookies to continue",
    "checking your browser before accessing",
)
CHUNK_SIZE = 64 * 1024
CHALLENGE_SNIFF_BYTES = 128 * 1024
MAX_BACKOFF_SECONDS = 30.0
MAX_SOLVES_PER_REQUEST = 1

T = TypeVar("T")


class FetchError(Exception):
    pass


class IncompleteDownload(OSError):
    pass


class ChallengeDetected(Exception):
    pass


@dataclass(frozen=True)
class Page:
    url: str
    html: str


@dataclass(frozen=True)
class SolveResult:
    url: str
    html: str
    user_agent: str
    cookies: list[dict] = field(default_factory=list)


class Solver(Protocol):
    def solve(self, url: str) -> SolveResult: ...


def is_challenge_response(status: int, headers, body: str) -> bool:
    if (headers.get("cf-mitigated") or "").lower() == "challenge":
        return True
    lowered = body[:CHALLENGE_SNIFF_BYTES].lower()
    if status == 200:
        return "<title>just a moment" in lowered
    return status in CHALLENGE_STATUSES and any(marker in lowered for marker in CHALLENGE_MARKERS)


def _retry_after(error: HTTPError) -> float | None:
    value = error.headers.get("Retry-After") if error.headers else None
    return float(value) if value and value.strip().isdigit() else None


def _read_error_body(error: HTTPError) -> str:
    try:
        return error.read(CHALLENGE_SNIFF_BYTES).decode("utf-8", errors="replace")
    except (OSError, http.client.HTTPException):
        return ""


def _cookie_applies(cookie_domain: str, host: str) -> bool:
    if cookie_domain.startswith("."):
        bare = cookie_domain[1:]
        return host == bare or host.endswith("." + bare)
    return host == cookie_domain


def _merge_cookie_header(existing: str | None, extra: dict[str, str]) -> str:
    kept = []
    for part in (existing or "").split(";"):
        name = part.strip().partition("=")[0]
        if part.strip() and name not in extra:
            kept.append(part.strip())
    return "; ".join(kept + [f"{name}={value}" for name, value in extra.items()])


class HttpClient:
    def __init__(
        self,
        user_agent: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        retries: int = 3,
        backoff: float = 1.5,
        solver: Solver | None = None,
    ) -> None:
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = backoff
        self.solver = solver
        self.headers = {
            "User-Agent": user_agent or DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        self.headers.update(headers or {})

        self._solve_lock = threading.Lock()
        self._solved_user_agent: str | None = None
        self._cookie_jar: dict[tuple[str, str], str] = {}
        self._generations: dict[str, int] = {}
        self._solutions: dict[str, SolveResult] = {}

    def fetch_page(self, url: str, referer: str | None = None) -> Page:
        def read(response) -> Page:
            body = response.read()
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                html = body.decode(charset, errors="replace")
            except LookupError:
                html = body.decode("utf-8", errors="replace")
            if is_challenge_response(response.status, response.headers, html):
                raise ChallengeDetected
            return Page(response.geturl(), html)

        return self._request(url, read, referer, compressed=True, fallback=lambda r: Page(r.url, r.html))

    def render(self, url: str) -> Page:
        if self.solver is None:
            raise FetchError("render() needs a solver (use --bypass)")
        result = self._solve(url, force=True)
        return Page(result.url, result.html)

    def download(self, url: str, destination: Path, referer: str | None = None) -> str:
        def save(response) -> str:
            content_type = response.headers.get_content_type()
            if content_type in ("text/html", "application/json", "text/plain"):
                sample = response.read(CHALLENGE_SNIFF_BYTES).decode("utf-8", errors="replace")
                if is_challenge_response(response.status, response.headers, sample):
                    raise ChallengeDetected
                raise FetchError(f"server returned {content_type} instead of an image: {url}")

            expected = response.headers.get("Content-Length", "")
            written = 0
            with open(destination, "wb") as handle:
                while chunk := response.read(CHUNK_SIZE):
                    handle.write(chunk)
                    written += len(chunk)

            if written == 0:
                raise IncompleteDownload("empty response body")
            if expected.isdigit() and written != int(expected):
                raise IncompleteDownload(f"received {written} of {expected} bytes")
            return content_type

        return self._request(url, save, referer, compressed=False)

    def _generation(self, url: str) -> int:
        return self._generations.get(urlparse(url).netloc.lower(), 0)

    def _solve(self, url: str, generation: int | None = None, force: bool = False) -> SolveResult:
        host = urlparse(url).netloc.lower()
        with self._solve_lock:
            cached = self._solutions.get(host)
            if not force and cached is not None and self._generations.get(host, 0) != generation:
                return cached

            result = self.solver.solve(url)  # type: ignore[union-attr]
            self._solved_user_agent = result.user_agent or self._solved_user_agent
            for cookie in result.cookies:
                self._cookie_jar[(cookie["domain"], cookie["name"])] = cookie["value"]
            self._generations[host] = self._generations.get(host, 0) + 1
            self._solutions[host] = result
            return result

    def _request_headers(self, url: str, referer: str | None, compressed: bool) -> dict[str, str]:
        headers = dict(self.headers)
        headers["Accept-Encoding"] = "gzip" if compressed else "identity"
        if referer:
            headers["Referer"] = referer

        if self._solved_user_agent:
            headers["User-Agent"] = self._solved_user_agent
        host = (urlparse(url).hostname or "").lower()
        clearance = {name: value for (domain, name), value in self._cookie_jar.items() if _cookie_applies(domain, host)}
        if clearance:
            headers["Cookie"] = _merge_cookie_header(headers.get("Cookie"), clearance)
        return headers

    def _request(
        self,
        url: str,
        handler: Callable[[http.client.HTTPResponse], T],
        referer: str | None,
        compressed: bool,
        fallback: Callable[[SolveResult], T] | None = None,
    ) -> T:
        last_error: Exception | None = None
        last_solution: SolveResult | None = None
        solves = 0
        attempt = 0

        while True:
            generation = self._generation(url)
            headers = self._request_headers(url, referer, compressed)
            wait: float | None = None
            challenged = False

            try:
                with urlopen(Request(url, headers=headers), timeout=self.timeout) as response:
                    return handler(response)
            except ChallengeDetected:
                challenged = True
            except HTTPError as error:
                body = _read_error_body(error)
                error.close()
                if is_challenge_response(error.code, error.headers, body):
                    challenged = True
                elif error.code not in RETRYABLE_STATUSES:
                    raise FetchError(f"HTTP {error.code} for {url}") from error
                else:
                    last_error = error
                    wait = _retry_after(error)
            except (OSError, http.client.HTTPException) as error:
                last_error = error

            if challenged:
                if self.solver is None:
                    raise FetchError(f"blocked by a Cloudflare challenge: {url} (try --bypass)")
                if solves >= MAX_SOLVES_PER_REQUEST:
                    if fallback is not None and last_solution is not None:
                        return fallback(last_solution)
                    raise FetchError(f"still blocked by Cloudflare after solving the challenge: {url}")
                solves += 1
                last_solution = self._solve(url, generation)
                continue

            if attempt >= self.retries:
                break
            delay = wait if wait is not None else self.backoff ** (attempt + 1)
            time.sleep(min(delay, MAX_BACKOFF_SECONDS))
            attempt += 1

        raise FetchError(f"giving up on {url}: {last_error}")
