from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .fetcher import FetchError, HttpClient, Page
from .parser import IMAGE_EXTENSIONS, Selector, extract_images

COMPLETE_MARKER = ".complete"
PART_SUFFIX = ".part"
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}


class ChapterError(Exception):
    pass


@dataclass
class ChapterResult:
    folder: Path
    total: int = 0
    downloaded: int = 0
    skipped: int = 0
    already_complete: bool = False
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


def _extension_from_url(url: str) -> str | None:
    extension = os.path.splitext(urlparse(url).path)[1].lower()
    return extension if extension in IMAGE_EXTENSIONS else None


def _existing_file(folder: Path, stem: str) -> Path | None:
    for candidate in folder.glob(f"{stem}.*"):
        if candidate.suffix != PART_SUFFIX and candidate.stat().st_size > 0:
            return candidate
    return None


class ChapterDownloader:
    def __init__(
        self,
        client: HttpClient,
        load_page: Callable[[str], Page] | None = None,
        container: Selector | None = None,
        extensions: frozenset[str] | None = IMAGE_EXTENSIONS,
        image_filter: str | None = None,
        workers: int = 6,
        resume: bool = True,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        self.client = client
        self.load_page = load_page or client.fetch_page
        self.container = container
        self.extensions = extensions
        self.image_filter = re.compile(image_filter) if image_filter else None
        self.workers = max(1, workers)
        self.resume = resume
        self.on_progress = on_progress

    def download_chapter(self, url: str, folder: Path) -> ChapterResult:
        marker = folder / COMPLETE_MARKER
        if self.resume and marker.is_file():
            total = int(marker.read_text().strip() or 0)
            return ChapterResult(folder, total=total, skipped=total, already_complete=True)

        page = self.load_page(url)
        images = extract_images(page.html, page.url, self.container, self.extensions)
        if self.image_filter:
            images = [image for image in images if self.image_filter.search(image)]
        if not images:
            raise ChapterError(f"no images found at {url} (JavaScript-rendered page? try --browser)")

        folder.mkdir(parents=True, exist_ok=True)
        width = max(3, len(str(len(images))))
        result = ChapterResult(folder, total=len(images))
        completed = 0

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {
                pool.submit(self._fetch_image, image_url, folder, f"{index:0{width}d}", page.url): image_url
                for index, image_url in enumerate(images, start=1)
            }
            for future in as_completed(futures):
                try:
                    if future.result() == "skipped":
                        result.skipped += 1
                    else:
                        result.downloaded += 1
                except (FetchError, OSError) as error:
                    result.failed.append(f"{futures[future]} ({error})")
                completed += 1
                if self.on_progress:
                    self.on_progress(completed, result.total)

        if result.ok:
            marker.write_text(str(result.total))
        return result

    def _fetch_image(self, image_url: str, folder: Path, stem: str, referer: str) -> str:
        if self.resume and _existing_file(folder, stem):
            return "skipped"

        part = folder / f"{stem}{PART_SUFFIX}"
        try:
            content_type = self.client.download(image_url, part, referer=referer)
            extension = _extension_from_url(image_url) or CONTENT_TYPE_EXTENSIONS.get(content_type, ".jpg")
            os.replace(part, folder / f"{stem}{extension}")
        finally:
            part.unlink(missing_ok=True)
        return "downloaded"
