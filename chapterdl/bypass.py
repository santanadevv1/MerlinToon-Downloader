from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .fetcher import FetchError, SolveResult

BRIDGE_DIR = Path(__file__).with_name("bypass_node")
RESULT_PREFIX = "SANTANA_RESULT:"
INSTALL_TIMEOUT_SECONDS = 600
SOLVE_GRACE_SECONDS = 45

NODE_HINT = "--bypass needs Node.js 18+ and Google Chrome (https://nodejs.org)"


class BypassError(FetchError):
    pass


def parse_payload(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_PREFIX):
            try:
                payload = json.loads(line[len(RESULT_PREFIX):])
            except ValueError:
                return None
            return payload if isinstance(payload, dict) else None
    return None


class CloudflareSolver:
    def __init__(
        self,
        timeout: float = 60.0,
        headless: bool = False,
        scroll: bool = True,
        bridge_dir: Path | None = None,
        node: str = "node",
        npm: str = "npm",
        log=print,
    ) -> None:
        self.timeout = timeout
        self.headless = headless
        self.scroll = scroll
        self.bridge_dir = Path(bridge_dir) if bridge_dir else BRIDGE_DIR
        self.node = node
        self.npm = npm
        self.log = log
        self._ready = False

    def solve(self, url: str) -> SolveResult:
        node = shutil.which(self.node)
        if node is None:
            raise BypassError(NODE_HINT)
        self._ensure_dependencies()

        command = [node, "solve.js", url, "--timeout", str(int(self.timeout))]
        if self.headless:
            command.append("--headless")
        if not self.scroll:
            command.append("--no-scroll")

        self.log(f"  cloudflare challenge: solving in a real browser ({url})")
        try:
            completed = subprocess.run(
                command,
                cwd=self.bridge_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout + SOLVE_GRACE_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            raise BypassError(f"the browser bridge timed out for {url}") from error
        except OSError as error:
            raise BypassError(f"could not start the browser bridge: {error}") from error

        payload = parse_payload(completed.stdout)
        if payload is None:
            detail = (completed.stderr or completed.stdout).strip().splitlines()[-3:]
            raise BypassError("the browser bridge failed: " + (" | ".join(detail) or f"exit code {completed.returncode}"))
        if payload.get("error"):
            raise BypassError(str(payload["error"]))

        cookies = [
            {"name": str(c["name"]), "value": str(c["value"]), "domain": str(c["domain"]).lower()}
            for c in payload.get("cookies", [])
            if isinstance(c, dict) and {"name", "value", "domain"} <= c.keys()
        ]
        return SolveResult(
            url=str(payload.get("url") or url),
            html=str(payload.get("html") or ""),
            user_agent=str(payload.get("userAgent") or ""),
            cookies=cookies,
        )

    def _ensure_dependencies(self) -> None:
        if self._ready or (self.bridge_dir / "node_modules").is_dir():
            self._ready = True
            return

        npm = shutil.which(self.npm)
        if npm is None:
            raise BypassError(NODE_HINT + " (npm not found)")

        self.log("  installing browser bridge dependencies (first run only)...")
        try:
            completed = subprocess.run(
                [npm, "install", "--omit=dev", "--no-audit", "--no-fund"],
                cwd=self.bridge_dir,
                capture_output=True,
                text=True,
                timeout=INSTALL_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise BypassError(f"npm install failed: {error}") from error
        if completed.returncode != 0:
            tail = " | ".join(completed.stderr.strip().splitlines()[-3:])
            raise BypassError(f"npm install failed: {tail}")
        self._ready = True
