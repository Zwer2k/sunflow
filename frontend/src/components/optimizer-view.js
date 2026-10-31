import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { poll } from "../lib/poll.js";
import { formatPower } from "../lib/format.js";
import { OPTIMIZER_VALUES } from "./roof.js";
import "./roof.js";

const REFRESH_MS = 10 * 1000;
// Remembered per browser; storage may be unavailable (private mode, previews)
const VISIBLE_KEY = "sunflow.optimizers.visible";

function readVisible() {
  try {
    const stored = JSON.parse(localStorage.getItem(VISIBLE_KEY));
    return Array.isArray(stored) ? stored : ["power"];
  } catch {
    return ["power"];
  }
}

/**
 * The optimizers of one PV string as they sit on the roof, with live values.
 *
 * Shown as modal dialog; fires "close". string: key "<device id>:<index>", heading: its name.
 */
class SunflowOptimizerView extends LitElement {
  static properties = {
    string: {},
    heading: {},
    _data: { state: true },
    _error: { state: true },
    _visible: { state: true },
  };

  constructor() {
    super();
    this._data = null;
    this._error = null;
    this._visible = readVisible();
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
    this._stopPolling = poll(() => this._load(), REFRESH_MS);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stopPolling?.();
  }

  firstUpdated() {
    this.renderRoot.querySelector("dialog").showModal();
  }

  async _load() {
    try {
      this._data = await api.optimizers();
      this._error = null;
    } catch (err) {
      this._error = err.message;
    }
  }

  _toggle(key, on) {
    this._visible = Object.keys(OPTIMIZER_VALUES).filter((k) => (k === key ? on : this._visible.includes(k)));
    try {
      localStorage.setItem(VISIBLE_KEY, JSON.stringify(this._visible));
    } catch {
      // Kept for this visit only
    }
  }

  _close() {
    this.renderRoot.querySelector("dialog")?.close();
    this.dispatchEvent(new CustomEvent("close"));
  }

  _edit() {
    this._close();
    this.dispatchEvent(new CustomEvent("sf-navigate", { detail: { view: "setup", step: "optimizers" }, bubbles: true, composed: true }));
  }

  render() {
    const mine = (this._data?.optimizers ?? []).filter((o) => o.string === this.string);
    const available = Object.keys(OPTIMIZER_VALUES).filter((k) => mine.some((o) => k in o.values));
    const powers = mine.map((o) => o.values.power).filter((p) => p !== null && p !== undefined);
    return html`
      <dialog aria-labelledby="title" @cancel=${(e) => { e.preventDefault(); this._close(); }}
        @click=${(e) => e.target.tagName === "DIALOG" && this._close()}>
        <div class="box">
          <div class="head">
            <h2 id="title">${this.heading}</h2>
            <button class="close" aria-label="Schließen" @click=${() => this._close()}>✕</button>
          </div>
          ${this._error ? html`<div class="alert">Optimierer nicht abrufbar: ${this._error}</div>` : nothing}
          ${this._data === null && !this._error ? html`<p class="muted">Lade …</p>` : nothing}
          ${this._data && !mine.length
            ? html`<p class="muted">Diesem String sind keine Optimierer zugeordnet.</p>`
            : nothing}
          ${mine.length
            ? html`
                <p class="small muted">${mine.length} Optimierer${powers.length
                  ? ` · zusammen ${formatPower(powers.reduce((a, b) => a + b, 0))} · ${formatPower(Math.min(...powers))} bis ${formatPower(Math.max(...powers))}`
                  : ""}</p>
                <div class="chips" role="group" aria-label="Angezeigte Werte">
                  ${available.map((k) => html`<label class="chip ${this._visible.includes(k) ? "on" : ""}">
                    <input type="checkbox" .checked=${this._visible.includes(k)} @change=${(e) => this._toggle(k, e.target.checked)}>
                    ${OPTIMIZER_VALUES[k].label}</label>`)}
                </div>
                <sf-roof .optimizers=${mine} .visible=${this._visible}
                  .breaks=${this._data.layouts?.[this.string]?.row_breaks ?? []}
                  .colBreaks=${this._data.layouts?.[this.string]?.col_breaks ?? []}></sf-roof>
                ${mine.some((o) => o.row === null || o.col === null)
                  ? html`<p class="small muted">Nicht alle Optimierer haben einen Platz auf dem Dach.</p>`
                  : nothing}`
            : nothing}
          <div class="actions">
            <button class="link" @click=${() => this._edit()}>Zuordnung und Anordnung bearbeiten …</button>
          </div>
        </div>
      </dialog>
    `;
  }

  static styles = css`
    dialog {
      padding: 0; border: 0; border-radius: 12px; max-width: min(960px, calc(100vw - 16px)); width: max-content;
      background: var(--sf-surface); color: var(--sf-text); box-shadow: 0 12px 40px rgb(0 0 0 / 0.25);
    }
    dialog::backdrop { background: rgb(0 0 0 / 0.4); }
    .box { padding: 16px 18px; display: flex; flex-direction: column; gap: 12px; min-width: min(320px, calc(100vw - 48px)); }
    .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    h2 { margin: 0; font-size: 17px; }
    p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .alert { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    .close { border: 0; background: transparent; color: var(--sf-muted); font-size: 18px; min-width: 40px; min-height: 40px; cursor: pointer; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; }
    .chip {
      display: inline-flex; align-items: center; gap: 6px; min-height: 32px; padding: 0 10px; border-radius: 16px;
      border: 1px solid var(--sf-border-strong); font-size: 13px; cursor: pointer;
    }
    .chip.on { background: var(--sf-primary-bg); color: var(--sf-primary-text); border-color: var(--sf-primary); }
    .chip input { margin: 0; }
    .actions { display: flex; justify-content: flex-end; }
    .link { border: 0; background: transparent; color: var(--sf-primary); cursor: pointer; font: inherit; font-size: 14px; padding: 6px 0; }
  `;
}

customElements.define("sf-optimizer-view", SunflowOptimizerView);
