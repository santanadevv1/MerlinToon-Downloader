from __future__ import annotations

import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"})
IMAGE_ATTRIBUTES = (
    "data-src",
    "data-original",
    "data-original-src",
    "data-lazy-src",
    "data-url",
    "src",
)

_SELECTOR_PATTERN = re.compile(r"^([a-zA-Z][\w-]*)?(?:#([\w-]+))?((?:\.[\w-]+)*)$")


@dataclass(frozen=True)
class Selector:
    tag: str | None = None
    element_id: str | None = None
    classes: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> "Selector":
        text = text.strip()
        match = _SELECTOR_PATTERN.match(text)
        if not text or not match:
            raise ValueError(
                f"unsupported selector {text!r}: use a tag, #id, .class or a combination such as div.reader"
            )
        tag, element_id, classes = match.groups()
        return cls(
            tag.lower() if tag else None,
            element_id,
            tuple(name for name in classes.split(".") if name),
        )

    def matches(self, tag: str, attrs: dict[str, str]) -> bool:
        if self.tag and tag != self.tag:
            return False
        if self.element_id and attrs.get("id") != self.element_id:
            return False
        if self.classes and not set(self.classes) <= set(attrs.get("class", "").split()):
            return False
        return True


@dataclass(frozen=True)
class Link:
    url: str
    text: str


class _PageScanner(HTMLParser):
    def __init__(self, container: Selector | None) -> None:
        super().__init__(convert_charrefs=True)
        self._container = container
        self._container_tag: str | None = None
        self._depth = 0
        self._link_href: str | None = None
        self._link_text: list[str] = []
        self.base_href: str | None = None
        self.images: list[dict[str, str]] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        attributes = {name: value or "" for name, value in attrs}

        if tag == "base" and self.base_href is None and attributes.get("href"):
            self.base_href = attributes["href"]

        if self._container is not None:
            if self._depth == 0:
                if self._container.matches(tag, attributes):
                    self._container_tag = tag
                    self._depth = 1
                return
            if tag == self._container_tag:
                self._depth += 1

        if tag == "img":
            self.images.append(attributes)
        elif tag == "a" and attributes.get("href"):
            self._close_link()
            self._link_href = attributes["href"]
            self._link_text = []

    def handle_endtag(self, tag):
        if self._depth > 0 and tag == self._container_tag:
            self._depth -= 1
        if tag == "a":
            self._close_link()

    def handle_data(self, data):
        if self._link_href is not None:
            self._link_text.append(data)

    def close(self):
        super().close()
        self._close_link()

    def _close_link(self):
        if self._link_href is not None:
            self.links.append((self._link_href, " ".join("".join(self._link_text).split())))
        self._link_href = None
        self._link_text = []


def _scan(html: str, container: Selector | None) -> _PageScanner:
    scanner = _PageScanner(container)
    scanner.feed(html)
    scanner.close()
    return scanner


def _resolve(page_url: str, base_href: str | None, reference: str) -> str:
    base = urljoin(page_url, base_href) if base_href else page_url
    return urldefrag(urljoin(base, reference.strip()))[0]


def _image_source(attrs: dict[str, str]) -> str | None:
    for name in IMAGE_ATTRIBUTES:
        value = attrs.get(name, "").strip()
        if value and not value.startswith("data:"):
            return value

    srcset = attrs.get("data-srcset") or attrs.get("srcset") or ""
    candidates = [part.split()[0] for part in srcset.split(",") if part.strip()]
    return candidates[-1] if candidates else None


def _is_http(url: str) -> bool:
    return urlparse(url).scheme in ("http", "https")


def extract_images(
    html: str,
    page_url: str,
    container: Selector | None = None,
    extensions: frozenset[str] | None = IMAGE_EXTENSIONS,
) -> list[str]:
    scanner = _scan(html, container)
    urls: list[str] = []

    for attrs in scanner.images:
        source = _image_source(attrs)
        if not source:
            continue
        url = _resolve(page_url, scanner.base_href, source)
        if not _is_http(url):
            continue
        if extensions is not None:
            extension = os.path.splitext(urlparse(url).path)[1].lower()
            if extension not in extensions:
                continue
        urls.append(url)

    return list(dict.fromkeys(urls))


def extract_links(html: str, page_url: str, container: Selector | None = None) -> list[Link]:
    scanner = _scan(html, container)
    links: list[Link] = []

    for href, text in scanner.links:
        url = _resolve(page_url, scanner.base_href, href)
        if _is_http(url):
            links.append(Link(url, text))

    return links
