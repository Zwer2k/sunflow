import { LitElement, html, css } from "../../vendor/lit-all.min.js";
import { t } from "../lib/i18n.js";

/** Local calendar day as YYYY-MM-DD. */
export const isoDay = (date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

/**
 * ‹ date › for a day up to today; fires "change" with detail {day} (YYYY-MM-DD).
 */
class SunflowDayNav extends LitElement {
  static properties = {
    day: {},
  };

  constructor() {
    super();
    this.day = isoDay(new Date());
  }

  _shift(days) {
    const date = new Date(`${this.day}T12:00:00`);
    date.setDate(date.getDate() + days);
    this._set(isoDay(date));
  }

  _set(day) {
    if (!day || day > isoDay(new Date()) || day === this.day) return;
    this.dispatchEvent(new CustomEvent("change", { detail: { day } }));
  }

  render() {
    const today = isoDay(new Date());
    return html`
      <button aria-label=${t("Tag davor")} @click=${() => this._shift(-1)}>‹</button>
      <input type="date" .value=${this.day} max=${today} aria-label=${t("Tag")} @change=${(e) => this._set(e.target.value)}>
      <button aria-label=${t("Tag danach")} ?disabled=${this.day >= today} @click=${() => this._shift(1)}>›</button>
    `;
  }

  static styles = css`
    :host { display: flex; align-items: center; gap: 4px; }
    button {
      min-width: 36px; min-height: 36px; border-radius: 8px; border: 1px solid var(--sf-border-strong);
      background: transparent; color: var(--sf-text); font-size: 18px; cursor: pointer;
    }
    button:disabled { opacity: 0.4; cursor: default; }
    input {
      font: inherit; min-height: 36px; padding: 0 8px; border-radius: 8px; border: 1px solid var(--sf-border-strong);
      background: var(--sf-surface); color: var(--sf-text);
    }
  `;
}

customElements.define("sf-day-nav", SunflowDayNav);
