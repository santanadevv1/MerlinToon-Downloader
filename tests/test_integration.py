import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from chapterdl.cli import main
from chapterdl.downloader import ChapterDownloader
from chapterdl.fetcher import HttpClient
from chapterdl.parser import Selector

JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-data" * 50
PNG = b"\x89PNG\r\n\x1a\n" + b"png-data" * 50


class Site(BaseHTTPRequestHandler):
    image_hits: dict = {}
    flaky_seen = False
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    def _send(self, status, body=b"", content_type="text/html; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path
        if path == "/series/":
            links = "".join(f'<a href="/series/chapter-{n}/">Chapter {n}</a>' for n in (3, 1, 2))
            return self._send(200, f'<div id="chapters">{links}</div><a href="/series/">home</a>'.encode())

        if path.startswith("/series/chapter-"):
            number = path.strip("/").split("-")[-1]
            images = (
                f'<img src="data:image/gif;base64,AAAA" data-src="/img/{number}/a.jpg">'
                f'<img src="/img/{number}/b.png">'
                f'<img src="/img/{number}/noext">'
            )
            return self._send(200, f'<div id="reader">{images}</div><img src="/ad.jpg">'.encode())

        if path.startswith("/img/"):
            if "localhost" not in self.headers.get("Referer", "") and "127.0.0.1" not in self.headers.get("Referer", ""):
                return self._send(403, b"hotlink", "text/plain")
            with Site.lock:
                Site.image_hits[path] = Site.image_hits.get(path, 0) + 1
            if path == "/img/1/a.jpg" and not Site.flaky_seen:
                Site.flaky_seen = True
                return self._send(503, b"busy", "text/plain", {"Retry-After": "0"})
            if path == "/img/2/b.png":
                return self._send(404, b"missing", "text/plain")
            if path.endswith("noext"):
                return self._send(200, PNG, "image/png")
            if path.endswith(".png"):
                return self._send(200, PNG, "image/png")
            return self._send(200, JPEG, "image/jpeg")

        self._send(404, b"nope", "text/plain")


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Site.image_hits = {}
        Site.flaky_seen = False
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        return main([*args, "-o", str(self.out), "--delay", "0", "--retries", "2"])

    def test_single_chapter_with_retry_and_referer(self):
        code = self.run_cli("chapter", f"{self.base}/series/chapter-1/", "--name", "demo", "--container", "#reader", "--any-extension")
        folder = self.out / "demo"
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), [".complete", "001.jpg", "002.png", "003.png"])
        self.assertEqual((folder / "001.jpg").read_bytes(), JPEG)
        self.assertEqual(Site.image_hits["/img/1/a.jpg"], 2)
        self.assertFalse(list(folder.glob("*.part")))

    def test_default_extension_filter_skips_extensionless(self):
        code = self.run_cli("chapter", f"{self.base}/series/chapter-1/", "--name", "demo", "--container", "#reader")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.name for p in (self.out / "demo").iterdir()), [".complete", "001.jpg", "002.png"])

    def test_series_range_and_failure_report(self):
        code = self.run_cli("series", f"{self.base}/series/", "--name", "demo", "--container", "#reader", "--chapter-selector", "#chapters", "--from", "2")
        self.assertEqual(code, 1)
        root = self.out / "demo"
        self.assertEqual(sorted(p.name for p in root.iterdir()), ["chapter-0002", "chapter-0003"])
        self.assertFalse((root / "chapter-0002" / ".complete").exists())
        self.assertTrue((root / "chapter-0003" / ".complete").exists())
        self.assertEqual(sorted(p.name for p in (root / "chapter-0002").iterdir()), ["001.jpg"])

    def test_resume_skips_complete_chapters_and_retries_failed(self):
        args = ("series", f"{self.base}/series/", "--name", "demo", "--container", "#reader", "--chapter-selector", "#chapters")
        self.run_cli(*args)
        first_hits = dict(Site.image_hits)
        self.run_cli(*args)
        self.assertEqual(Site.image_hits["/img/3/a.jpg"], first_hits["/img/3/a.jpg"])
        self.assertEqual(Site.image_hits["/img/1/a.jpg"], first_hits["/img/1/a.jpg"])
        self.assertEqual(Site.image_hits["/img/2/b.png"], first_hits["/img/2/b.png"] + 1)
        self.assertEqual(Site.image_hits["/img/2/a.jpg"], first_hits["/img/2/a.jpg"])

    def test_range_mode(self):
        code = self.run_cli("range", f"{self.base}/series/chapter-{{n}}/", "--from", "3", "--to", "3", "--name", "demo", "--container", "#reader")
        self.assertEqual(code, 0)
        self.assertTrue((self.out / "demo" / "chapter-0003" / "001.jpg").exists())

    def test_missing_images_is_reported(self):
        code = self.run_cli("chapter", f"{self.base}/series/", "--name", "demo", "--container", "#nothing")
        self.assertEqual(code, 1)

    def test_progress_callback(self):
        seen = []
        downloader = ChapterDownloader(HttpClient(retries=2), container=Selector.parse("#reader"), on_progress=lambda d, t: seen.append((d, t)))
        result = downloader.download_chapter(f"{self.base}/series/chapter-3/", self.out / "c3")
        self.assertTrue(result.ok)
        self.assertEqual(seen[-1], (2, 2))


if __name__ == "__main__":
    unittest.main()
