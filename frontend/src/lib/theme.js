/**
 * Light or dark as Home Assistant shows it: its theme can differ from the system setting (a dark
 * theme on a light system, or switching with the sun), and the App in its panel must not clash.
 */

const CHECK_MS = 5000;

function apply() {
  let dark;
  try {
    // The panel page is same-origin under ingress; opened on its own there is no parent to ask
    dark = window.parent !== window ? window.parent.document.querySelector("home-assistant")?.hass?.themes?.darkMode : undefined;
  } catch {
    dark = undefined;
  }
  const root = document.documentElement;
  if (dark === undefined || dark === null) delete root.dataset.theme;
  else root.dataset.theme = dark ? "dark" : "light";
}

export function followHostTheme() {
  apply();
  setInterval(apply, CHECK_MS);
}
