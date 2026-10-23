'use strict';

const { connect } = require('./src');

const RESULT_PREFIX = 'SANTANA_RESULT:';
const POLL_MS = 1000;
const SCROLL_STEPS = 80;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function parseArgs(argv) {
  const options = { url: null, timeout: 60, headless: false, scroll: true };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--timeout') options.timeout = Number(argv[(i += 1)]) || options.timeout;
    else if (arg === '--headless') options.headless = true;
    else if (arg === '--no-scroll') options.scroll = false;
    else if (!options.url) options.url = arg;
  }
  return options;
}

function emit(payload) {
  process.stdout.write(`${RESULT_PREFIX}${JSON.stringify(payload)}\n`);
}

async function challengeActive(page) {
  try {
    return await page.evaluate(() => {
      const title = (document.title || '').toLowerCase();
      if (/just a moment|un instant|einen moment|un momento|bir dakika|um momento/.test(title)) return true;
      if (typeof window._cf_chl_opt !== 'undefined') return true;
      return Boolean(
        document.querySelector(
          '#challenge-running, #challenge-stage, #challenge-form, #cf-challenge-running, .cf-browser-verification'
        )
      );
    });
  } catch (error) {
    return true;
  }
}

async function waitForClearance(page, timeoutSeconds) {
  const deadline = Date.now() + timeoutSeconds * 1000;
  while (Date.now() < deadline) {
    if (!(await challengeActive(page))) {
      await sleep(POLL_MS);
      if (!(await challengeActive(page))) return true;
    }
    await sleep(POLL_MS);
  }
  return false;
}

async function autoScroll(page) {
  await page
    .evaluate(async (steps) => {
      await new Promise((resolve) => {
        let count = 0;
        const timer = setInterval(() => {
          window.scrollBy(0, Math.max(400, window.innerHeight * 0.8));
          count += 1;
          const atBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 2;
          if (count >= steps || atBottom) {
            clearInterval(timer);
            resolve();
          }
        }, 250);
      });
    }, SCROLL_STEPS)
    .catch(() => {});
}

async function solve(options) {
  const { browser, page } = await connect({
    headless: options.headless ? 'new' : false,
    turnstile: true,
  });

  try {
    await page
      .goto(options.url, { waitUntil: 'domcontentloaded', timeout: options.timeout * 1000 })
      .catch(() => {});

    if (!(await waitForClearance(page, options.timeout))) {
      throw new Error(`Cloudflare challenge was not cleared within ${options.timeout}s`);
    }

    await page.waitForNetworkIdle({ idleTime: 800, timeout: 8000 }).catch(() => {});
    if (options.scroll) await autoScroll(page);

    const info = await page.evaluate(() => {
      const isHtml = (document.contentType || '').includes('html');
      return {
        url: location.href,
        userAgent: navigator.userAgent,
        contentType: document.contentType || '',
        html: isHtml
          ? document.documentElement.outerHTML
          : document.body
            ? document.body.innerText
            : '',
      };
    });

    const session = await page.createCDPSession();
    const { cookies } = await session.send('Network.getCookies', {
      urls: [...new Set([options.url, info.url])],
    });

    return {
      ...info,
      cookies: cookies.map(({ name, value, domain }) => ({ name, value, domain })),
    };
  } finally {
    await browser.close().catch(() => {});
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (!options.url) {
    emit({ error: 'usage: node solve.js <url> [--timeout SECONDS] [--headless] [--no-scroll]' });
    return 1;
  }

  try {
    emit(await solve(options));
    return 0;
  } catch (error) {
    emit({ error: error.message || String(error) });
    return 1;
  }
}

main().then((code) => process.exit(code));
