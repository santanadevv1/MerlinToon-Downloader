import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from chapterdl.bypass import BypassError, CloudflareSolver, parse_payload
from chapterdl.cli import main
from chapterdl.fetcher import FetchError, HttpClient, SolveResult

BROWSER_UA = "SolvedBrowser/1.0"
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-data" * 50
CHALLENGE_HTML = b"<html><head><title>Just a moment...</title></head><body>cf_chl_opt</body></html>"
CHAPTER_HTML = b'<div id="reader"><img src="/img/1.jpg"><img src="/img/2.jpg"><img src="/img/3.jpg"></div>'


class FakeCloudflare(BaseHTTPRequestHandler):
    accept_clearance = True
    challenge_status = 403

    def log_message(self, *args):
        pass

    def _cleared(self):
        cookie = self.headers.get("Cookie", "")
        return (
            FakeCloudflare.accept_clearance
            and "cf_clearance=good" in cookie
            and self.headers.get("User-Agent") == BROWSER_UA
        )

    def _send(self, status, body, content_type="text/html; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/blocked":
            return self._send(403, b"<title>Attention Required! | Cloudflare</title>", extra={"Server": "cloudflare"})
        if self.path == "/interstitial":
            return self._send(200, CHALLENGE_HTML)

        if not self._cleared():
            return self._send(
                FakeCloudflare.challenge_status, CHALLENGE_HTML, extra={"cf-mitigated": "challenge", "Server": "cloudflare"}
            )
        if self.path.startswith("/img/"):
            return self._send(200, JPEG, "image/jpeg")
        self._send(200, CHAPTER_HTML)


class FakeSolver:
    def __init__(self, port, delay=0.0, cookie_value="good"):
        self.port = port
        self.delay = delay
        self.cookie_value = cookie_value
        self.calls = []

    def solve(self, url):
        self.calls.append(url)
        time.sleep(self.delay)
        return SolveResult(
            url=url,
            html='<div id="reader"><img src="/img/browser.jpg"></div>',
            user_agent=BROWSER_UA,
            cookies=[{"name": "cf_clearance", "value": self.cookie_value, "domain": "127.0.0.1"}],
        )


class ClientChallengeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCloudflare)
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeCloudflare.accept_clearance = True
        FakeCloudflare.challenge_status = 403
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_without_solver_reports_cloudflare(self):
        with self.assertRaises(FetchError) as caught:
            HttpClient(retries=0).fetch_page(f"{self.base}/chapter/")
        self.assertIn("Cloudflare", str(caught.exception))

    def test_solves_once_and_reuses_clearance(self):
        solver = FakeSolver(self.port)
        client = HttpClient(retries=0, solver=solver)
        self.assertIn("img/1.jpg", client.fetch_page(f"{self.base}/chapter/").html)
        client.fetch_page(f"{self.base}/chapter/")
        client.download(f"{self.base}/img/1.jpg", self.out / "a.jpg")
        self.assertEqual(len(solver.calls), 1)
        self.assertEqual((self.out / "a.jpg").read_bytes(), JPEG)

    def test_503_challenge_is_solved_not_retried_blindly(self):
        FakeCloudflare.challenge_status = 503
        solver = FakeSolver(self.port)
        page = HttpClient(retries=3, solver=solver).fetch_page(f"{self.base}/chapter/")
        self.assertIn("img/1.jpg", page.html)
        self.assertEqual(len(solver.calls), 1)

    def test_200_interstitial_is_detected(self):
        solver = FakeSolver(self.port)
        client = HttpClient(retries=0, solver=solver)
        page = client.fetch_page(f"{self.base}/interstitial")
        self.assertIn("browser.jpg", page.html)
        self.assertEqual(len(solver.calls), 1)

    def test_parallel_workers_trigger_a_single_solve(self):
        solver = FakeSolver(self.port, delay=0.3)
        client = HttpClient(retries=0, solver=solver)
        errors = []

        def grab(n):
            try:
                client.download(f"{self.base}/img/{n}.jpg", self.out / f"{n}.jpg")
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=grab, args=(n,)) for n in range(1, 7)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(solver.calls), 1)
        self.assertEqual(len(list(self.out.glob("*.jpg"))), 6)

    def test_page_falls_back_to_browser_html_when_cookie_is_not_accepted(self):
        FakeCloudflare.accept_clearance = False
        solver = FakeSolver(self.port)
        page = HttpClient(retries=0, solver=solver).fetch_page(f"{self.base}/chapter/")
        self.assertIn("browser.jpg", page.html)

    def test_download_gives_up_when_cookie_is_not_accepted(self):
        FakeCloudflare.accept_clearance = False
        solver = FakeSolver(self.port)
        with self.assertRaises(FetchError) as caught:
            HttpClient(retries=0, solver=solver).download(f"{self.base}/img/1.jpg", self.out / "a.jpg")
        self.assertIn("Cloudflare", str(caught.exception))
        self.assertEqual(len(solver.calls), 1)

    def test_hard_block_is_not_sent_to_the_solver(self):
        solver = FakeSolver(self.port)
        with self.assertRaises(FetchError) as caught:
            HttpClient(retries=0, solver=solver).fetch_page(f"{self.base}/blocked")
        self.assertIn("HTTP 403", str(caught.exception))
        self.assertEqual(solver.calls, [])

    def test_render_always_uses_the_browser_and_keeps_cookies(self):
        solver = FakeSolver(self.port)
        client = HttpClient(retries=0, solver=solver)
        page = client.render(f"{self.base}/chapter/")
        self.assertIn("browser.jpg", page.html)
        client.download(f"{self.base}/img/1.jpg", self.out / "a.jpg")
        self.assertEqual(len(solver.calls), 1)

    def test_user_cookie_is_merged_with_clearance(self):
        solver = FakeSolver(self.port)
        client = HttpClient(headers={"Cookie": "session=abc; cf_clearance=stale"}, retries=0, solver=solver)
        self.assertIn("img/1.jpg", client.fetch_page(f"{self.base}/chapter/").html)

    def test_cli_bypass_end_to_end(self):
        solver = FakeSolver(self.port)
        with mock.patch("chapterdl.cli.CloudflareSolver", return_value=solver):
            code = main(["chapter", f"{self.base}/chapter/", "--bypass", "--name", "demo", "--container", "#reader",
                         "-o", str(self.out), "--delay", "0", "--retries", "0"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.name for p in (self.out / "demo").iterdir()), [".complete", "001.jpg", "002.jpg", "003.jpg"])
        self.assertEqual(len(solver.calls), 1)

    def test_cli_bypass_render_end_to_end(self):
        solver = FakeSolver(self.port)
        with mock.patch("chapterdl.cli.CloudflareSolver", return_value=solver):
            code = main(["chapter", f"{self.base}/chapter/", "--bypass-render", "--name", "demo", "--container", "#reader",
                         "-o", str(self.out), "--delay", "0", "--retries", "0"])
        self.assertEqual(code, 0)
        self.assertEqual([p.name for p in (self.out / "demo").iterdir() if p.suffix == ".jpg"], ["001.jpg"])


class SolverBridgeTests(unittest.TestCase):
    def test_parse_payload_ignores_noise(self):
        out = 'warning\nSANTANA_RESULT:{"url": "u"}\n'
        self.assertEqual(parse_payload(out), {"url": "u"})
        self.assertIsNone(parse_payload("nothing here"))
        self.assertIsNone(parse_payload("SANTANA_RESULT:{broken"))

    def test_missing_node_is_explained(self):
        with self.assertRaises(BypassError) as caught:
            CloudflareSolver(node="definitely-not-node", log=lambda *_: None).solve("http://x/")
        self.assertIn("Node.js", str(caught.exception))

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_bridge_protocol_with_stub_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = Path(tmp)
            (bridge / "node_modules").mkdir()
            (bridge / "solve.js").write_text(
                "const a = process.argv.slice(2);\n"
                "console.error('noise');\n"
                "console.log('SANTANA_RESULT:' + JSON.stringify({url: a[0], userAgent: 'UA', html: '<p>ok</p>',"
                " cookies: [{name: 'cf_clearance', value: 'v', domain: '.Example.com'}, {bad: 1}], args: a}));\n"
            )
            result = CloudflareSolver(timeout=5, headless=True, bridge_dir=bridge, log=lambda *_: None).solve("http://example.com/")
            self.assertEqual(result.user_agent, "UA")
            self.assertEqual(result.html, "<p>ok</p>")
            self.assertEqual(result.cookies, [{"name": "cf_clearance", "value": "v", "domain": ".example.com"}])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_bridge_error_is_surfaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = Path(tmp)
            (bridge / "node_modules").mkdir()
            (bridge / "solve.js").write_text(
                "console.log('SANTANA_RESULT:' + JSON.stringify({error: 'challenge not cleared'})); process.exit(1);\n"
            )
            with self.assertRaises(BypassError) as caught:
                CloudflareSolver(timeout=5, bridge_dir=bridge, log=lambda *_: None).solve("http://example.com/")
            self.assertIn("challenge not cleared", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
