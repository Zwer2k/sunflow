import { LitElement, html, css, nothing, unsafeCSS } from "../../vendor/lit-all.min.js";
import { t } from "../lib/i18n.js";
import { setShown, shown } from "../lib/display-toggles.js";

// Same width as the header's switch to the phone layout
const PHONE = unsafeCSS("(max-width: 700px)");
const WIDE = unsafeCSS("(min-width: 701px)");

/**
 * The switches for explanations and recommendations, only those a page has.
 *
 * placement="header": in the menu bar, on a PC. placement="title": next to a page's title, on a phone.
 * Each one hides itself on the other kind of screen, so a page can carry both.
 */
class SunflowDisplayToggles extends LitElement {
  static properties = {
    placement: { reflect: true },
    help: { type: Boolean },
    suggestions: { type: Boolean },
    _help: { state: true },
    _suggestions: { state: true },
  };

  constructor() {
    super();
    this.placement = "title";
    this.help = false;
    this.suggestions = false;
    this._help = shown("help");
    this._suggestions = shown("suggestions");
    // Another instance (header or title) switched: follow it
    this._onChange = (e) => {
      this._help = e.detail.help;
      this._suggestions = e.detail.suggestions;
    };
  }

  connectedCallback() {
    super.connectedCallback();
    window.addEventListener("sf-display-toggles", this._onChange);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    window.removeEventListener("sf-display-toggles", this._onChange);
  }

  _toggle(key, value, label, icon) {
    return html`<button class=${value ? "on" : ""} aria-pressed=${value} title=${label} aria-label=${label}
      @click=${() => setShown(key, !value)}><svg viewBox="0 0 24 24" aria-hidden="true"><path d=${icon}></path></svg></button>`;
  }

  render() {
    if (!this.help && !this.suggestions) return nothing;
    return html`
      ${this.help
        ? this._toggle("help", this._help, this._help ? t("Erklärtexte ausblenden") : t("Erklärtexte einblenden"),
          "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17.5h0")
        : nothing}
      ${this.suggestions
        ? this._toggle("suggestions", this._suggestions, this._suggestions ? t("Empfehlungen ausblenden") : t("Empfehlungen einblenden"),
          "M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z")
        : nothing}
    `;
  }

  static styles = css`
    :host { display: flex; gap: 4px; flex: none; }
    @media ${PHONE} { :host([placement="header"]) { display: none; } }
    @media ${WIDE} { :host([placement="title"]) { display: none; } }
    button {
      display: flex; align-items: center; justify-content: center; width: 34px; height: 34px; padding: 0;
      border: 1px solid var(--sf-border); border-radius: 8px; background: transparent; color: var(--sf-muted); cursor: pointer;
    }
    button.on { color: var(--sf-primary-text); background: var(--sf-primary-bg); border-color: var(--sf-primary); }
    svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    /* Fingers need about 40 px */
    @media (pointer: coarse) { button { width: 40px; height: 40px; } }
  `;
}

customElements.define("sf-display-toggles", SunflowDisplayToggles);
