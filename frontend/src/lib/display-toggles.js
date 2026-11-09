/**
 * Switches in the menu bar: explanations (elements with class "help") and recommendations
 * (class "suggestion") shown or hidden in every view at once. Each browser remembers its own.
 * A hidden explanation leaves a small "?" that shows it on a tap.
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

// Hidden explanations shrink to a small "?" at their place; a click shows that one ("help-open")
const HELP_HIDDEN = `
  .help:not(.help-open) { font-size: 0 !important; line-height: 0 !important; cursor: help; }
  .help:not(.help-open) > * { display: none !important; }
  .help:not(.help-open)::before {
    content: "?"; display: inline-flex; align-items: center; justify-content: center; box-sizing: border-box;
    width: 16px; height: 16px; border: 1px solid currentColor; border-radius: 50%;
    font-size: 11px; line-height: 1; font-weight: 600; color: var(--sf-muted); vertical-align: middle;
  }
  .help.help-open { cursor: help; }
  /* A paragraph below a heading: the "?" after the heading, the text below it when opened */
  :is(h1, h2, h3, h4) + .help:not(.help-open) { display: none !important; }
  :is(h1, h2, h3, h4):has(+ .help)::after {
    content: "?"; display: inline-flex; align-items: center; justify-content: center; box-sizing: border-box;
    width: 16px; height: 16px; margin-left: 8px; border: 1px solid currentColor; border-radius: 50%;
    font-size: 11px; line-height: 1; font-weight: 600; color: var(--sf-muted); vertical-align: middle; cursor: help;
  }
  :is(h1, h2, h3, h4):has(+ .help-open)::after { color: var(--sf-primary); }
  /* A titled block (a strategy): the "?" after its title, the text where it is */
  .help-host > .help:not(.help-open) { display: none !important; }
  .help-host .help-title::after {
    content: "?"; display: inline-flex; align-items: center; justify-content: center; box-sizing: border-box;
    width: 16px; height: 16px; margin-left: 8px; border: 1px solid currentColor; border-radius: 50%;
    font-size: 11px; line-height: 1; font-weight: 600; color: var(--sf-muted); vertical-align: middle; cursor: help;
  }
  .help-host:has(> .help-open) .help-title::after { color: var(--sf-primary); }
`;

function apply() {
  toggleSheet.replaceSync(`${state.help ? "" : HELP_HIDDEN}${state.suggestions ? "" : ".suggestion { display: none !important; }"}`);
}
apply();

/** Show or hide one explanation; a collapsible one ("What does … do?") opens right away. */
function toggleHelp(help) {
  const open = help.classList.toggle("help-open");
  if (help.tagName === "DETAILS") help.open = open;
}

const HEADINGS = new Set(["H1", "H2", "H3", "H4"]);
const INTERACTIVE = new Set(["A", "BUTTON", "SUMMARY", "INPUT", "SELECT", "TEXTAREA", "LABEL"]);

// Through shadow roots: the path of the click names the explanation that was tapped
document.addEventListener(
  "click",
  (e) => {
    if (state.help) return;
    const path = e.composedPath();
    // The "?" behind a heading or a title: right of its text
    const title = path[0];
    const target = HEADINGS.has(title?.tagName) && title.nextElementSibling?.classList.contains("help")
      ? title.nextElementSibling
      : title?.classList?.contains("help-title")
        ? [...(title.closest(".help-host")?.children ?? [])].find((el) => el.classList.contains("help"))
        : null;
    if (target) {
      const text = document.createRange();
      text.selectNodeContents(title);
      if (e.clientX > text.getBoundingClientRect().right) {
        // Inside a label: no choice, only the text
        e.preventDefault();
        e.stopPropagation();
        toggleHelp(target);
      }
      return;
    }
    const index = path.findIndex((el) => el.classList?.contains("help"));
    if (index < 0) return;
    const help = path[index];
    // An opened text keeps its own links, buttons and "What does … do?"
    if (help.classList.contains("help-open") && path.slice(0, index).some((el) => INTERACTIVE.has(el.tagName))) return;
    // Inside a label or button the tap only opens or closes the text
    e.preventDefault();
    e.stopPropagation();
    toggleHelp(help);
  },
  true,
);

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
