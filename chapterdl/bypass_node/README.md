# Santana Bypass System

> Developed by **Santana**

A lightweight Node.js launcher that runs a **real Chrome instance** under Puppeteer, with human-like cursor movement and automatic **Cloudflare Turnstile** checkbox handling. Created for educational purposes.

## Features

- **Real browser, not headless emulation** – launches Chrome via `chrome-launcher` and attaches over the debugging port.
- **Turnstile auto-click** – detects the widget and clicks it in the background until the page or browser closes.
- **Human-like input** – `ghost-cursor` integration (`page.realClick`, `page.realCursor`) plus a mouse-event coordinate patch.
- **Multi-page aware** – every new tab is configured automatically.
- **Proxy support** – host/port flags and username/password authentication.
- **Plugin support** – optional `puppeteer-extra` plugins.
- **Linux friendly** – starts a virtual display (Xvfb) automatically.
- **Clean shutdown** – Chrome and Xvfb are killed when the browser disconnects.

## Requirements

- Node.js 18+
- Google Chrome / Chromium installed
- Linux only: `xvfb` (`sudo apt-get install xvfb`)

## Installation

```bash
npm install
```

## Usage

```js
const { connect } = require('./src');

(async () => {
  const { browser, page } = await connect({
    headless: false,
    turnstile: true,
  });

  await page.goto('https://example.com');
  await page.realClick('a');   // human-like click

  await browser.close();
})();
```

## Command-line solver

`solve.js` opens a URL in the real browser, waits for the Cloudflare challenge to clear and prints one result line. `chapterdl --bypass` uses it under the hood.

```bash
node solve.js <url> [--timeout SECONDS] [--headless] [--no-scroll]
```

```
SANTANA_RESULT:{"url":"...","userAgent":"...","contentType":"...","html":"...","cookies":[...]}
```

On failure the line contains an `error` field and the exit code is 1.

## Options

| Option           | Type      | Default | Description                                                        |
| ---------------- | --------- | ------- | ------------------------------------------------------------------ |
| `headless`       | `boolean` | `false` | Run Chrome headless. `'auto'` is treated as `false`.               |
| `turnstile`      | `boolean` | `false` | Automatically click Cloudflare Turnstile widgets.                  |
| `args`           | `string[]`| `[]`    | Extra Chrome flags.                                                |
| `proxy`          | `object`  | `{}`    | `{ host, port, username, password }`.                              |
| `plugins`        | `array`   | `[]`    | `puppeteer-extra` plugins.                                         |
| `customConfig`   | `object`  | `{}`    | Extra options passed to `chrome-launcher`.                         |
| `connectOption`  | `object`  | `{}`    | Extra options passed to `puppeteer.connect`.                       |
| `disableXvfb`    | `boolean` | `false` | Skip the Xvfb virtual display on Linux.                            |
| `ignoreAllFlags` | `boolean` | `false` | Use only your `args`, proxy and headless flags (no defaults).      |

## Project Structure

```
src/
├── index.js         # Launch flow: Xvfb → Chrome → connect → cleanup
├── chrome-flags.js  # Chrome launch flag builder
├── page-setup.js    # Per-page setup: solver, proxy auth, plugins, cursor
└── turnstile.js     # Turnstile detection and click loop
```

## Notes

- `--no-sandbox` is enabled by default. Avoid visiting untrusted sites outside an isolated environment (Docker/VM).
- Results depend on the target site's protection level and may change as Cloudflare updates its checks.

## Disclaimer

This project was created **for educational and research purposes only**.

- It is a Cloudflare Turnstile bypass tool. Using it against websites without the owner's explicit permission may violate their terms of service and, depending on your jurisdiction, applicable laws.
- The developer (Santana) provides this software "as is", without warranty of any kind, and **is not responsible** for how it is used or for any damage, loss, bans, or legal consequences resulting from its use.
- You are solely responsible for your actions. Only use it on systems you own or are authorized to test.

## License & Credits

Developed by **Santana**. Released under the **ISC License**. This project is a refactor inspired by [puppeteer-real-browser](https://github.com/zfcsoftware/puppeteer-real-browser) by zfcsoftware (ISC).
