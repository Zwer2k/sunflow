/**
 * Call fn every ms while the page is visible, never twice at the same time.
 *
 * Hidden tabs (another HA page, minimized browser) do not poll: every open tab
 * would otherwise keep loading Home Assistant. Returns a function that stops it.
 */
import { renewIngressSession } from "./api.js";

// Away longer than this, the ingress session has likely expired meanwhile
const RENEW_AFTER_MS = 5 * 60 * 1000;
let hiddenSince = null;
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    hiddenSince = Date.now();
  } else if (hiddenSince !== null && Date.now() - hiddenSince > RENEW_AFTER_MS) {
    hiddenSince = null;
    renewIngressSession();
  }
});

export function poll(fn, ms) {
  let running = false;
  const tick = async () => {
    if (running || document.hidden) return;
    running = true;
    try {
      await fn();
    } finally {
      running = false;
    }
  };
  const timer = setInterval(tick, ms);
  // Catch up right away when the tab becomes visible again
  const onVisible = () => !document.hidden && tick();
  document.addEventListener("visibilitychange", onVisible);
  return () => {
    clearInterval(timer);
    document.removeEventListener("visibilitychange", onVisible);
  };
}
