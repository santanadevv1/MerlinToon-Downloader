'use strict';

const { createCursor } = require('ghost-cursor');
const { runTurnstileSolver } = require('./turnstile');

function patchScreenCoordinates() {
  Object.defineProperty(MouseEvent.prototype, 'screenX', {
    get() {
      return this.clientX + window.screenX;
    },
  });
  Object.defineProperty(MouseEvent.prototype, 'screenY', {
    get() {
      return this.clientY + window.screenY;
    },
  });
}

async function setupPage({ browser, page, proxy = {}, turnstile = false, plugins = [] }) {
  let solverActive = Boolean(turnstile);
  const stopSolver = () => {
    solverActive = false;
  };

  page.once('close', stopSolver);
  browser.once('disconnected', stopSolver);

  if (solverActive) {
    runTurnstileSolver(page, () => solverActive).catch(() => {});
  }

  if (proxy.username && proxy.password) {
    await page.authenticate({ username: proxy.username, password: proxy.password });
  }

  for (const plugin of plugins) {
    plugin.onPageCreated(page);
  }

  await page.evaluateOnNewDocument(patchScreenCoordinates);

  const cursor = createCursor(page);
  page.realCursor = cursor;
  page.realClick = cursor.click;

  return page;
}

module.exports = { setupPage };
