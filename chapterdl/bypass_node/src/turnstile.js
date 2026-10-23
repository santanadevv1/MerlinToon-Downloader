'use strict';

const CHECK_TIMEOUT_MS = 5000;
const POLL_INTERVAL_MS = 1000;
const CLICK_OFFSET_X = 30;
const WIDGET_WIDTH = { min: 290, max: 310 };
const RESPONSE_FIELD = '[name="cf-turnstile-response"]';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function withTimeout(promise, ms) {
  return Promise.race([promise, sleep(ms).then(() => false)]);
}

function collectWidgetBoxes(width) {
  const candidates = Array.from(document.querySelectorAll('div')).filter((element) => {
    try {
      const rect = element.getBoundingClientRect();
      return rect.width > width.min && rect.width <= width.max && !element.querySelector('*');
    } catch (error) {
      return false;
    }
  });

  const unstyled = candidates.filter((element) => {
    const style = window.getComputedStyle(element);
    return style.margin === '0px' && style.padding === '0px';
  });

  return (unstyled.length > 0 ? unstyled : candidates).map((element) => {
    const { x, y, width: boxWidth, height } = element.getBoundingClientRect();
    return { x, y, width: boxWidth, height };
  });
}

async function clickWidget(page, box) {
  if (!box) return;
  try {
    await page.mouse.click(box.x + CLICK_OFFSET_X, box.y + box.height / 2);
  } catch (error) {
    return;
  }
}

async function clickResponseHosts(page, fields) {
  for (const field of fields) {
    try {
      const host = await field.evaluateHandle((element) => element.parentElement);
      await clickWidget(page, await host.boundingBox());
    } catch (error) {
      continue;
    }
  }
}

async function solveOnce(page) {
  const fields = await page.$$(RESPONSE_FIELD);

  if (fields.length > 0) {
    await clickResponseHosts(page, fields);
    return true;
  }

  const boxes = await page.evaluate(collectWidgetBoxes, WIDGET_WIDTH);
  for (const box of boxes) {
    await clickWidget(page, box);
  }
  return true;
}

async function runTurnstileSolver(page, isActive) {
  while (isActive()) {
    try {
      await withTimeout(solveOnce(page), CHECK_TIMEOUT_MS);
    } catch (error) {
      if (page.isClosed()) return;
    }
    await sleep(POLL_INTERVAL_MS);
  }
}

module.exports = { runTurnstileSolver };
