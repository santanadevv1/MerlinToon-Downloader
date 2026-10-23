from __future__ import annotations

import argparse
import re
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .browser import BrowserPageLoader
from .bypass import CloudflareSolver
from .chapters import format_number, select_chapters
from .downloader import ChapterDownloader, ChapterError, ChapterResult
from .fetcher import FetchError, HttpClient
from .parser import IMAGE_EXTENSIONS, Selector, extract_links
from .profiles import PROFILES

Job = tuple[str, str, Path]


class Progress:
    def __init__(self) -> None:
        self._interactive = sys.stdout.isatty()

    def update(self, done: int, total: int) -> None:
        if self._interactive:
            print(f"\r  downloading {done}/{total}", end="", flush=True)

    def finish(self) -> None:
        if self._interactive:
            print("\r" + " " * 40 + "\r", end="", flush=True)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "download"


def default_name(url: str) -> str:
    parsed = urlparse(url)
    segments = [segment for segment in parsed.path.split("/") if segment and "{" not in segment]
    return slugify(segments[-1] if segments else parsed.netloc)


def parse_headers(entries: list[str], cookie: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for entry in entries:
        name, separator, value = entry.partition(":")
        if not separator or not name.strip():
            raise ValueError(f"invalid header {entry!r}, expected NAME:VALUE")
        headers[name.strip()] = value.strip()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-o", "--output", type=Path, default=Path("downloads"), help="output directory")
    common.add_argument("--name", help="series folder name (default: derived from the URL)")
    common.add_argument("--container", metavar="SELECTOR", help="only collect images inside this element, e.g. '#reader' or 'div.page-break'")
    common.add_argument("--image-filter", metavar="REGEX", help="only keep image URLs matching this regex")
    common.add_argument("--profile", choices=sorted(PROFILES), help="preset for a specific site (container, image filter, chapter listing)")
    common.add_argument("--workers", type=int, default=6, help="parallel image downloads per chapter (default: 6)")
    common.add_argument("--retries", type=int, default=3, help="retries per request (default: 3)")
    common.add_argument("--timeout", type=float, default=30.0, help="request timeout in seconds (default: 30)")
    common.add_argument("--delay", type=float, default=1.0, help="pause between chapters in seconds (default: 1)")
    common.add_argument("--header", action="append", default=[], metavar="NAME:VALUE", help="extra request header, repeatable")
    common.add_argument("--cookie", help="Cookie header value")
    common.add_argument("--user-agent", help="custom User-Agent")
    common.add_argument("--any-extension", action="store_true", help="accept image URLs without a known file extension")
    common.add_argument("--no-resume", action="store_true", help="download everything again, ignoring existing files")
    common.add_argument("--browser", action="store_true", help="render pages in Chrome (for JavaScript-driven readers, needs Selenium)")
    common.add_argument("--bypass", action="store_true", help="solve Cloudflare challenges automatically in a real browser (needs Node.js 18+ and Chrome)")
    common.add_argument("--bypass-render", action="store_true", help="always load pages through the real browser (implies --bypass; for protected, JavaScript-driven readers)")
    common.add_argument("--bypass-timeout", type=float, default=60.0, metavar="SECONDS", help="time allowed to clear one challenge (default: 60)")
    common.add_argument("--bypass-headless", action="store_true", help="run the bypass browser headless (less reliable)")

    parser = argparse.ArgumentParser(
        prog="chapterdl",
        description="Download chapter images from pages you are authorized to access.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    chapter = commands.add_parser("chapter", parents=[common], help="download a single chapter page")
    chapter.add_argument("url")

    series = commands.add_parser("series", parents=[common], help="download chapters listed on a series page")
    series.add_argument("url")
    series.add_argument("--chapter-selector", metavar="SELECTOR", help="only read chapter links inside this element")
    series.add_argument("--chapter-pattern", default="chapter", help="regex a chapter URL must match (default: chapter)")
    series.add_argument("--from", dest="start", type=float, help="first chapter number")
    series.add_argument("--to", dest="end", type=float, help="last chapter number")

    sequence = commands.add_parser("range", parents=[common], help="download chapters from a URL template")
    sequence.add_argument("pattern", help="URL containing {n}, e.g. https://example.com/series/chapter-{n}/")
    sequence.add_argument("--from", dest="start", type=int, required=True, help="first chapter number")
    sequence.add_argument("--to", dest="end", type=int, required=True, help="last chapter number")

    return parser


def build_jobs(args: argparse.Namespace, client: HttpClient, fetch_page, chapter_container: Selector | None) -> list[Job]:
    root = args.output / (args.name or default_name(args.pattern if args.command == "range" else args.url))

    if args.command == "chapter":
        return [(args.url, args.url, root)]

    if args.command == "range":
        return [
            (f"chapter {n}", args.pattern.replace("{n}", str(n)), root / f"chapter-{format_number(n)}")
            for n in range(args.start, args.end + 1)
        ]

    profile = PROFILES.get(args.profile) if args.profile else None
    if profile and profile.chapters:
        chapters = profile.chapters(client, args.url, args.start, args.end)
        return [(f"chapter {chapter.number:g}", chapter.url, root / chapter.folder_name) for chapter in chapters]

    page = fetch_page(args.url)
    links = extract_links(page.html, page.url, chapter_container)
    chapters, unnumbered = select_chapters(links, args.chapter_pattern, args.start, args.end, exclude_url=page.url)
    for url in unnumbered:
        print(f"skipping link without a chapter number: {url}")
    return [(f"chapter {chapter.number:g}", chapter.url, root / chapter.folder_name) for chapter in chapters]


def report(result: ChapterResult) -> None:
    if result.already_complete:
        print(f"  already complete ({result.total} images)")
        return
    print(f"  {result.total} images: {result.downloaded} downloaded, {result.skipped} skipped, {len(result.failed)} failed")
    for failure in result.failed:
        print(f"    ! {failure}")


def run_jobs(downloader: ChapterDownloader, progress: Progress, jobs: list[Job], delay: float) -> int:
    failures = 0
    for position, (label, url, folder) in enumerate(jobs, start=1):
        print(f"[{position}/{len(jobs)}] {label} -> {folder}")
        try:
            result = downloader.download_chapter(url, folder)
        except (ChapterError, FetchError) as error:
            progress.finish()
            print(f"  error: {error}")
            failures += 1
            continue

        progress.finish()
        report(result)
        failures += 0 if result.ok else 1
        if not result.already_complete and position < len(jobs):
            time.sleep(delay)

    print(f"done: {len(jobs) - failures}/{len(jobs)} chapters complete")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        profile = PROFILES.get(args.profile) if args.profile else None
        container_text = args.container or (profile.container if profile else None)
        container = Selector.parse(container_text) if container_text else None
        image_filter = args.image_filter or (profile.image_filter if profile else None)
        chapter_container = Selector.parse(args.chapter_selector) if getattr(args, "chapter_selector", None) else None
        headers = parse_headers(args.header, args.cookie)
        if args.command == "range" and "{n}" not in args.pattern:
            raise ValueError("the URL pattern must contain {n}")
        if args.bypass_render and args.browser:
            raise ValueError("--bypass-render and --browser both render pages; use only one")
    except ValueError as error:
        parser.error(str(error))

    use_bypass = args.bypass or args.bypass_render
    solver = CloudflareSolver(timeout=args.bypass_timeout, headless=args.bypass_headless) if use_bypass else None
    client = HttpClient(args.user_agent, headers, args.timeout, args.retries, solver=solver)
    progress = Progress()

    try:
        with ExitStack() as stack:
            if args.bypass_render:
                load_page = client.render
            elif args.browser:
                load_page = stack.enter_context(BrowserPageLoader()).load
            else:
                load_page = None
            downloader = ChapterDownloader(
                client,
                load_page,
                container,
                None if args.any_extension else IMAGE_EXTENSIONS,
                image_filter,
                args.workers,
                not args.no_resume,
                progress.update,
            )
            jobs = build_jobs(args, client, load_page or client.fetch_page, chapter_container)
            if not jobs:
                print("no chapters matched; check --chapter-pattern, --chapter-selector and the --from/--to range")
                return 1
            return run_jobs(downloader, progress, jobs, args.delay)
    except FetchError as error:
        print(f"error: {error}")
        return 1
    except RuntimeError as error:
        print(f"error: {error}")
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted; run the same command again to resume")
        return 130
