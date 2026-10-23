import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from chapterdl import profiles
from chapterdl.cli import main
from chapterdl.downloader import ChapterDownloader
from chapterdl.fetcher import FetchError, HttpClient
from chapterdl.parser import Selector
from chapterdl.profiles import PROFILES, _find_manga_id, _merlintoon_chapters

JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-data" * 50
CHAPTER_COUNT = 5
SERVER_PAGE_LIMIT = 2


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)

        if path == "/manga/demo/":
            return self._send(200, b'<body class="single postid-77"></body>', "text/html")

        if path == "/api":
            page = int(params.get("paged", 1))
            numbers = list(range(CHAPTER_COUNT, 0, -1))
            start = (page - 1) * SERVER_PAGE_LIMIT
            items = [{"number": str(n), "slug": f"chapter-{n}"} for n in numbers[start : start + SERVER_PAGE_LIMIT]]
            return self._send(200, json.dumps({"items": items}).encode(), "application/json")

        if path.startswith("/read/"):
            html = (
                '<div id="chapter-content">'
                '<img src="/wp-content/uploads/init-manga/a.jpg">'
                '<img src="/wp-content/uploads/init-manga/b.jpg">'
                '<img src="/wp-content/uploads/other/banner.jpg">'
                "</div>"
            )
            return self._send(200, html.encode(), "text/html")

        if path.startswith("/wp-content/"):
            return self._send(200, JPEG, "image/jpeg")

        self._send(404, b"nope", "text/plain")


class ProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        patches = [
            mock.patch.object(profiles, "MERLINTOON_API", f"{self.base}/api"),
            mock.patch.object(profiles, "MERLINTOON_PAGE_SIZE", SERVER_PAGE_LIMIT),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def fake_client(self):
        client = HttpClient(retries=0)
        original = client.fetch_page

        def fetch_page(url, referer=None):
            return original(url.replace("https://merlintoon.com", self.base), referer)

        client.fetch_page = fetch_page
        return client

    def test_manga_id_detection(self):
        self.assertEqual(_find_manga_id('<body class="a postid-12 b">'), 12)
        self.assertEqual(_find_manga_id('{"manga_id":"34"}'), 34)
        self.assertIsNone(_find_manga_id("<body></body>"))

    def test_chapter_listing_follows_pages_even_when_server_caps_page_size(self):
        chapters = _merlintoon_chapters(self.fake_client(), "https://merlintoon.com/manga/demo/", None, None)
        self.assertEqual([chapter.number for chapter in chapters], [1, 2, 3, 4, 5])
        self.assertEqual(chapters[0].url, "https://merlintoon.com/manga/demo/chapter-1/")

    def test_chapter_listing_respects_range(self):
        chapters = _merlintoon_chapters(self.fake_client(), "https://merlintoon.com/manga/demo/", 2, 4)
        self.assertEqual([chapter.number for chapter in chapters], [2, 3, 4])

    def test_rejects_foreign_series_url(self):
        with self.assertRaises(FetchError):
            _merlintoon_chapters(self.fake_client(), "https://example.com/manga/demo/", None, None)

    def test_image_filter_drops_unrelated_images(self):
        profile = PROFILES["merlintoon"]
        downloader = ChapterDownloader(
            HttpClient(retries=0),
            container=Selector.parse(profile.container),
            image_filter=profile.image_filter,
        )
        result = downloader.download_chapter(f"{self.base}/read/1/", self.out / "c1")
        self.assertTrue(result.ok)
        self.assertEqual(result.total, 2)
        self.assertEqual(sorted(p.name for p in (self.out / "c1").iterdir()), [".complete", "001.jpg", "002.jpg"])

    def test_cli_image_filter_option(self):
        code = main(["chapter", f"{self.base}/read/1/", "--name", "demo", "-o", str(self.out), "--container", "#chapter-content", "--image-filter", "banner"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.name for p in (self.out / "demo").iterdir()), [".complete", "001.jpg"])

    def test_cli_rejects_unknown_profile(self):
        with self.assertRaises(SystemExit):
            main(["chapter", "https://example.com/", "--profile", "nope"])


if __name__ == "__main__":
    unittest.main()
