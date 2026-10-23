# Santana Chapter Downloader

> Developed by **Santana**

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Version](https://img.shields.io/badge/version-1.2.0-informational)
![License](https://img.shields.io/badge/license-MIT-green)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)

A command-line tool that downloads chapter images from web pages. It finds lazy-loaded images, fetches them in parallel, retries what fails and picks up where it left off if a run is interrupted. When a site sits behind Cloudflare, an optional bundled bridge opens a real Chrome window, clears the challenge and hands the session back to the downloader.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Cloudflare bypass](#cloudflare-bypass)
- [Site profiles](#site-profiles)
- [Options](#options)
- [Output layout](#output-layout)
- [Project structure](#project-structure)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Responsible use](#responsible-use)
- [License and credits](#license-and-credits)

## Features

- **No runtime dependencies.** The core runs on the Python standard library alone.
- **Three ways to pick chapters.** A single chapter page, a series page that lists its chapters, or a URL template with a number range.
- **Lazy-load aware.** Reads `data-src`, `data-original`, `data-lazy-src`, `srcset` and similar attributes, and skips `data:` placeholders.
- **Scoped extraction.** Limit collection to a container such as `#reader` so ads and thumbnails stay out.
- **Parallel and resilient.** Configurable worker pool, retries with exponential backoff, and `Retry-After` support.
- **Resumable.** Finished chapters are skipped, partial ones continue, and every file is written atomically through a `.part` file.
- **Referer handling.** Each image request carries the chapter page as its referer.
- **Site profiles.** Presets that bundle the container, image filter and chapter listing for a specific site.
- **Browser mode.** Render JavaScript-driven readers with Selenium and Chrome.
- **Cloudflare bypass.** Detects challenges, solves them in a real browser and reuses the clearance for pages, APIs and images.

## Requirements

| Component | Needed for | Notes |
| --- | --- | --- |
| Python 3.9+ | Everything | |
| Selenium and Chrome | `--browser` | `pip install selenium` |
| Node.js 18+ and Chrome | `--bypass` | On Linux also install `xvfb` |

## Installation

```bash
git clone https://github.com/devvsantana/santana-chapter-downloader.git
cd santana-chapter-downloader
python -m chapterdl --help
```

To get a `chapterdl` command on your PATH:

```bash
pip install .
```

Browser mode support is available as an extra:

```bash
pip install ".[browser]"
```

## Quick start

### Single chapter

```bash
python -m chapterdl chapter "https://example.com/series/chapter-12/" \
  --container "#reader" --name my-series
```

### Series page

```bash
python -m chapterdl series "https://example.com/series/" \
  --chapter-selector "#chapters" --container "#reader" \
  --from 10 --to 25 --name my-series
```

### URL template

```bash
python -m chapterdl range "https://example.com/series/chapter-{n}/" \
  --from 1 --to 50 --container "#reader" --name my-series
```

Run the same command again at any time. Completed chapters are skipped and only missing images are fetched.

## Cloudflare bypass

Some sites answer with a Cloudflare challenge instead of the page. Passing `--bypass` lets the downloader deal with it without any manual steps.

```bash
# Plain requests first; a real browser opens only if a challenge appears
python -m chapterdl series "https://example.com/series/" --bypass --from 1 --to 30

# Protected site with a JavaScript-driven reader: load every page through the browser
python -m chapterdl chapter "https://example.com/series/chapter-1/" --bypass-render
```

### How it works

1. A request comes back as a Cloudflare challenge: HTTP 403, 429 or 503 with a challenge marker, a `cf-mitigated: challenge` header, or a "Just a moment..." page.
2. The bridge in `chapterdl/bypass_node/` launches a real Chrome, opens the URL, clicks the Turnstile widget with human-like cursor movement and waits until the challenge is gone.
3. The resulting cookies and the browser's User-Agent are stored and sent with every later request to that host: chapter pages, listing APIs and images.
4. Parallel workers share a single solve, so six threads hitting the same challenge open one browser, not six.
5. When the clearance expires, the next challenge triggers a fresh solve automatically.

If Cloudflare still rejects the plain HTTP request after a solve, page requests fall back to the HTML the browser already rendered. Image downloads in that situation report an error.

### Flags

| Flag | Effect |
| --- | --- |
| `--bypass` | Solve challenges on demand |
| `--bypass-render` | Always load pages through the real browser (implies `--bypass`) |
| `--bypass-timeout SECONDS` | Time allowed to clear one challenge (default: 60) |
| `--bypass-headless` | Run the bypass browser headless. Less reliable |

`--bypass-render` and `--browser` both render pages, so they cannot be combined.

### Things to know

- The first `--bypass` run installs the bridge's npm packages, which needs an internet connection once.
- Clearance cookies are tied to an IP address. Solve and download from the same connection.
- Hard blocks such as "Attention Required" pages, firewall rules and rate limits are not challenges. They are reported as normal HTTP errors.
- Results depend on how strictly a site is configured, and Cloudflare changes its checks over time. A solve that works today may need a bridge update later.
- The bridge starts Chrome with `--no-sandbox`. Only point it at sites you trust, or run it inside a container or VM.

### Using the bridge on its own

The bridge works without Python. It prints a single result line to stdout:

```bash
cd chapterdl/bypass_node
npm install
node solve.js "https://example.com/" --timeout 60
```

```
SANTANA_RESULT:{"url":"...","userAgent":"...","contentType":"...","html":"...","cookies":[...]}
```

On failure the line carries an `error` field and the process exits with code 1. More detail lives in [`chapterdl/bypass_node/README.md`](chapterdl/bypass_node/README.md).

## Site profiles

A profile fills in the container, the image filter and, where the site exposes an API, the chapter listing. A whole series becomes one command:

```bash
python -m chapterdl series "https://merlintoon.com/manga/my-series/" \
  --profile merlintoon --from 1 --to 30
```

Values passed with `--container` and `--image-filter` override the profile. New sites are added in `chapterdl/profiles.py`.

## Options

| Option | Description |
| --- | --- |
| `-o, --output DIR` | Output directory (default: `downloads`) |
| `--name NAME` | Series folder name (default: derived from the URL) |
| `--container SELECTOR` | Collect images only inside this element (`#id`, `.class`, `tag`, `tag.class`) |
| `--image-filter REGEX` | Keep only image URLs matching this expression |
| `--profile NAME` | Site preset (`merlintoon`) |
| `--chapter-selector SELECTOR` | `series` mode: read chapter links only inside this element |
| `--chapter-pattern REGEX` | `series` mode: pattern a chapter URL must match (default: `chapter`) |
| `--from N`, `--to N` | Chapter number range |
| `--workers N` | Parallel image downloads per chapter (default: 6) |
| `--retries N` | Retries per request (default: 3) |
| `--timeout SECONDS` | Request timeout (default: 30) |
| `--delay SECONDS` | Pause between chapters (default: 1) |
| `--header NAME:VALUE` | Extra request header, repeatable |
| `--cookie VALUE` | Cookie header value |
| `--user-agent VALUE` | Custom User-Agent |
| `--any-extension` | Accept image URLs without a known file extension |
| `--no-resume` | Download everything again |
| `--browser` | Render pages in Chrome before extracting images |
| `--bypass` | Solve Cloudflare challenges in a real browser |
| `--bypass-render` | Always load pages through the real browser |
| `--bypass-timeout SECONDS` | Time allowed to clear one challenge (default: 60) |
| `--bypass-headless` | Run the bypass browser headless |

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Every chapter completed |
| `1` | At least one chapter failed, or nothing matched |
| `2` | A required tool is missing (for example Selenium) |
| `130` | Interrupted with Ctrl+C. Run the command again to resume |

## Output layout

```
downloads/
└── my-series/
    ├── chapter-0010/
    │   ├── 001.jpg
    │   ├── 002.jpg
    │   └── .complete
    └── chapter-0011/
```

The `.complete` marker is written only after every image in a chapter was saved.

## Project structure

```
chapterdl/
├── cli.py          Argument parsing and orchestration
├── parser.py       HTML scanning, selectors, image and link extraction
├── chapters.py     Chapter numbering, filtering and sorting
├── profiles.py     Per-site presets and chapter list providers
├── fetcher.py      HTTP client: retries, atomic downloads, challenge detection
├── downloader.py   Parallel chapter download and resume logic
├── browser.py      Optional Selenium page loader
├── bypass.py       Python side of the Cloudflare bypass
└── bypass_node/    Real Chrome bridge: Turnstile click handling and solve.js
tests/              Unit and integration tests against a local HTTP server
```

## Testing

```bash
python -m unittest discover -s tests -t .
```

The suite spins up a local HTTP server and fakes the solver, so no network access or browser is required.

## Troubleshooting

**"no images found"**
The page probably builds its images with JavaScript. Try `--browser`, or `--bypass-render` if the site is also protected. A wrong `--container` can cause the same message.

**"blocked by a Cloudflare challenge"**
Re-run with `--bypass`.

**"still blocked by Cloudflare after solving the challenge"**
The clearance did not carry over. Make sure you are on the same IP the browser used, then try `--bypass-render`.

**"no chapters matched"**
Check `--chapter-pattern`, `--chapter-selector` and the `--from` / `--to` range.

**Chrome fails to start on Linux**
Install Xvfb with `sudo apt-get install xvfb`, or use `--bypass-headless`.

## Responsible use

This project is meant for personal archiving and learning. Download only content you own, have permission to use, or that is licensed for it, and follow the terms of service of the sites you visit. The Cloudflare bridge automates a browser the same way a person would click through, but using it against a site that does not want automated access may breach that site's terms or local law. You are responsible for how you use it. The software is provided as is, without warranty.

## License and credits

Developed by **Santana**. Released under the [MIT License](LICENSE). Copyright (c) Santana.

`chapterdl/bypass_node/` is the Santana Bypass System, released under the ISC license. It is a refactor inspired by [puppeteer-real-browser](https://github.com/zfcsoftware/puppeteer-real-browser) by zfcsoftware (ISC).
