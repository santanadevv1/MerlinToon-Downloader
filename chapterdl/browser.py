from __future__ import annotations

import time

from .fetcher import Page

SELENIUM_HINT = "Browser mode needs Selenium and Chrome: pip install selenium"


class BrowserPageLoader:
    def __init__(self, headless: bool = True, scroll_pause: float = 0.6, max_steps: int = 400) -> None:
        self.headless = headless
        self.scroll_pause = scroll_pause
        self.max_steps = max_steps
        self._driver = None

    def __enter__(self) -> "BrowserPageLoader":
        try:
            from selenium import webdriver
        except ImportError as error:
            raise RuntimeError(SELENIUM_HINT) from error

        options = webdriver.ChromeOptions()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--window-size=1280,1600")
        self._driver = webdriver.Chrome(options=options)
        return self

    def __exit__(self, *exc_info) -> None:
        if self._driver is not None:
            self._driver.quit()
            self._driver = None

    def load(self, url: str) -> Page:
        driver = self._driver
        if driver is None:
            raise RuntimeError("BrowserPageLoader must be used as a context manager")

        driver.get(url)
        time.sleep(self.scroll_pause)

        viewport = driver.execute_script("return window.innerHeight") or 800
        position = 0
        for _ in range(self.max_steps):
            height = driver.execute_script("return document.body.scrollHeight")
            if position >= height:
                break
            position += viewport
            driver.execute_script(f"window.scrollTo(0, {position});")
            time.sleep(self.scroll_pause)

        return Page(driver.current_url, driver.page_source)
