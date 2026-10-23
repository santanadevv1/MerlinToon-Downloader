from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from .chapters import Chapter
from .fetcher import FetchError, HttpClient

ChapterProvider = Callable[[HttpClient, str, "float | None", "float | None"], "list[Chapter]"]

MERLINTOON_SERIES = re.compile(r"^https?://(?:www\.)?merlintoon\.com/manga/([^/?#]+)", re.IGNORECASE)
MERLINTOON_API = "https://merlintoon.com/wp-json/initmanga/v1/chapters"
MERLINTOON_PAGE_SIZE = 50
MERLINTOON_MAX_PAGES = 200


@dataclass(frozen=True)
class Profile:
    name: str
    container: str | None = None
    image_filter: str | None = None
    chapters: ChapterProvider | None = None


def _find_manga_id(html: str) -> int | None:
    for pattern in (r'<body[^>]*\bclass="[^"]*\bpostid-(\d+)', r'"manga_id"\s*:\s*"?(\d+)'):
        match = re.search(pattern, html)
        if match:
            return int(match.group(1))
    return None


def _merlintoon_page(client: HttpClient, manga_id: int, page_number: int) -> list[dict]:
    url = f"{MERLINTOON_API}?manga_id={manga_id}&per_page={MERLINTOON_PAGE_SIZE}&paged={page_number}"
    try:
        items = json.loads(client.fetch_page(url).html).get("items") or []
    except (ValueError, AttributeError) as error:
        raise FetchError(f"unexpected chapter list response: {error}") from error
    return [item for item in items if isinstance(item, dict)]


def _merlintoon_chapters(
    client: HttpClient, series_url: str, start: float | None, end: float | None
) -> list[Chapter]:
    match = MERLINTOON_SERIES.match(series_url)
    if not match:
        raise FetchError("expected a series URL like https://merlintoon.com/manga/<series>/")
    series = match.group(1)

    manga_id = _find_manga_id(client.fetch_page(f"https://merlintoon.com/manga/{series}/").html)
    if manga_id is None:
        raise FetchError("could not determine the series id from the page")

    chapters: dict[str, Chapter] = {}
    seen: set[str] = set()
    for page_number in range(1, MERLINTOON_MAX_PAGES + 1):
        items = _merlintoon_page(client, manga_id, page_number)

        fresh = 0
        for item in items:
            try:
                number = float(item.get("number"))
            except (TypeError, ValueError):
                continue
            slug = str(item.get("slug") or f"chapter-{number:g}").strip("/")
            if slug not in seen:
                seen.add(slug)
                fresh += 1
            if (start is not None and number < start) or (end is not None and number > end):
                continue
            chapters.setdefault(slug, Chapter(number, f"https://merlintoon.com/manga/{series}/{slug}/"))

        if not fresh:
            break

    return sorted(chapters.values(), key=lambda chapter: chapter.number)


PROFILES: dict[str, Profile] = {
    "merlintoon": Profile(
        name="merlintoon",
        container="#chapter-content",
        image_filter=r"/wp-content/uploads/init-manga",
        chapters=_merlintoon_chapters,
    ),
}
