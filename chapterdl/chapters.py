from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .parser import Link

_NUMBER_PATTERN = re.compile(r"\d+(?:[.-]\d+)?")


@dataclass(frozen=True)
class Chapter:
    number: float
    url: str

    @property
    def folder_name(self) -> str:
        return f"chapter-{format_number(self.number)}"


def chapter_number(url: str) -> float | None:
    matches = _NUMBER_PATTERN.findall(urlparse(url).path)
    if not matches:
        return None
    return float(matches[-1].replace("-", "."))


def format_number(number: float) -> str:
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    whole, _, fraction = text.partition(".")
    return whole.zfill(4) + (f".{fraction}" if fraction else "")


def select_chapters(
    links: list[Link],
    pattern: str = "chapter",
    start: float | None = None,
    end: float | None = None,
    exclude_url: str | None = None,
) -> tuple[list[Chapter], list[str]]:
    matcher = re.compile(pattern, re.IGNORECASE)
    seen_urls: set[str] = set()
    seen_numbers: set[float] = set()
    chapters: list[Chapter] = []
    unnumbered: list[str] = []

    for link in links:
        if link.url == exclude_url or link.url in seen_urls or not matcher.search(link.url):
            continue
        seen_urls.add(link.url)

        number = chapter_number(link.url)
        if number is None:
            unnumbered.append(link.url)
            continue
        if (start is not None and number < start) or (end is not None and number > end):
            continue
        if number in seen_numbers:
            continue

        seen_numbers.add(number)
        chapters.append(Chapter(number, link.url))

    chapters.sort(key=lambda chapter: chapter.number)
    return chapters, unnumbered
