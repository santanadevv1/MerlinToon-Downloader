'use strict';

const DISABLED_FEATURES = [
  'Translate',
  'OptimizationHints',
  'MediaRouter',
  'DialMediaRouteProvider',
  'CalculateNativeWinOcclusion',
  'InterestFeedContentSuggestions',
  'CertificateTransparencyComponentUpdater',
  'AutofillServerCommunication',
  'PrivacySandboxSettings4',
  'AutomationControlled',
];

function isManagedFlag(flag) {
  return flag.includes('--disable-features') || flag.includes('component-update');
}

function getProxyFlags(proxy) {
  if (!proxy || !proxy.host || !proxy.port) return [];
  return [`--proxy-server=${proxy.host}:${proxy.port}`];
}

function getHeadlessFlags(headless) {
  return headless === false ? [] : [`--headless=${headless}`];
}

function buildChromeFlags({ Launcher, args, headless, proxy, ignoreAllFlags }) {
  if (ignoreAllFlags) {
    return [...getProxyFlags(proxy), ...args, ...getHeadlessFlags(headless)];
  }

  const defaults = Launcher.defaultFlags().filter((flag) => !isManagedFlag(flag));

  return [
    ...defaults,
    ...args,
    ...getHeadlessFlags(headless),
    ...getProxyFlags(proxy),
    `--disable-features=${DISABLED_FEATURES.join(',')}`,
    '--no-sandbox',
  ];
}

module.exports = { buildChromeFlags };
