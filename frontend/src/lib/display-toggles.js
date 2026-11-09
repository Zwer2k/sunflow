/**
 * Switches in the menu bar: explanations (elements with class "help") and recommendations
 * (class "suggestion") shown or hidden in every view at once. Each browser remembers its own.
 *
 * Every component adopts `toggleSheet`; changing it reaches all shadow roots without a render.
 */

const KEYS = { help: "sunflow.display.help", suggestions: "sunflow.display.suggestions" };

export const toggleSheet = new CSSStyleSheet();

function stored(key) {
  try {
    return localStorage.getItem(KEYS[key]) !== "off";
  } catch {
    return true;
  }
}

const state = { help: stored("help"), suggestions: stored("suggestions") };

function apply() {
  toggleSheet.replaceSync(
    `${state.help ? "" : ".help { display: none !important; }"}${state.suggestions ? "" : ".suggestion { display: none !important; }"}`,
  );
}
apply();

/** Whether explanations ("help") or recommendations ("suggestions") are shown. */
export function shown(key) {
  return state[key];
}

export function setShown(key, value) {
  state[key] = value;
  try {
    localStorage.setItem(KEYS[key], value ? "on" : "off");
  } catch {
    // Not remembered, the switch still works for this visit
  }
  apply();
  window.dispatchEvent(new CustomEvent("sf-display-toggles", { detail: { ...state } }));
}
