'use strict';

process.env.REBROWSER_PATCHES_RUNTIME_FIX_MODE = 'alwaysIsolated';

const puppeteer = require('puppeteer-core-patch');
const Xvfb = require('xvfb');
const treeKill = require('tree-kill');
const { buildChromeFlags } = require('./chrome-flags');
const { setupPage } = require('./page-setup');

const XVFB_MISSING_MESSAGE =
  'Linux detected but xvfb could not be started. ' +
  'Install it with "sudo apt-get install xvfb" to reduce the chance of detection.';

function startVirtualDisplay() {
  const display = new Xvfb({
    silent: true,
    xvfb_args: ['-screen', '0', '1920x1080x24', '-ac'],
  });

  try {
    display.startSync();
    return display;
  } catch (error) {
    console.warn(`${XVFB_MISSING_MESSAGE}\n${error.message}`);
    return null;
  }
}

async function createDriver(plugins) {
  if (plugins.length === 0) return puppeteer;

  const { addExtra } = await import('puppeteer-extra');
  const driver = addExtra(puppeteer);
  plugins.forEach((plugin) => driver.use(plugin));
  return driver;
}

function attachCleanup({ browser, chrome, display }) {
  browser.once('disconnected', () => {
    if (display) {
      try {
        display.stopSync();
      } catch (error) {
        console.warn(error.message);
      }
    }

    try {
      chrome.kill();
    } catch (error) {
      console.warn(error.message);
    }

    try {
      treeKill(chrome.pid, 'SIGKILL');
    } catch (error) {
      console.warn(error.message);
    }
  });
}

async function connect({
  args = [],
  headless = false,
  customConfig = {},
  proxy = {},
  turnstile = false,
  connectOption = {},
  disableXvfb = false,
  plugins = [],
  ignoreAllFlags = false,
} = {}) {
  const { launch, Launcher } = await import('chrome-launcher');
  const resolvedHeadless = headless === 'auto' ? false : headless;

  const display =
    process.platform === 'linux' && !disableXvfb ? startVirtualDisplay() : null;

  const chrome = await launch({
    ignoreDefaultFlags: true,
    chromeFlags: buildChromeFlags({
      Launcher,
      args,
      headless: resolvedHeadless,
      proxy,
      ignoreAllFlags,
    }),
    ...customConfig,
  });

  const driver = await createDriver(plugins);
  const browser = await driver.connect({
    browserURL: `http://127.0.0.1:${chrome.port}`,
    ...connectOption,
  });

  attachCleanup({ browser, chrome, display });

  const settings = { browser, proxy, turnstile, plugins };

  const [initialPage] = await browser.pages();
  const page = await setupPage({ ...settings, page: initialPage });

  browser.on('targetcreated', async (target) => {
    if (target.type() !== 'page') return;
    try {
      await setupPage({ ...settings, page: await target.page() });
    } catch (error) {
      console.warn(error.message);
    }
  });

  return { browser, page };
}

module.exports = { connect };
