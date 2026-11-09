import { LitElement, html, css, svg, nothing } from "../../vendor/lit-all.min.js";
import { t } from "../lib/i18n.js";
import { toggleSheet } from "../lib/display-toggles.js";

const R = 46;
const START = 135;
const SWEEP = 270;
const PANEL_WIDTH = 224;
const PANEL_HEIGHT = 300;

function point(fraction) {
  const angle = ((START + fraction * SWEEP) * Math.PI) / 180;
  return [60 + R * Math.cos(angle), 60 + R * Math.sin(angle)];
}

function arc(fraction) {
  const [x0, y0] = point(0);
  const [x1, y1] = point(fraction);
  return `M${x0} ${y0}A${R} ${R} 0 ${fraction * SWEEP > 180 ? 1 : 0} 1 ${x1} ${y1}`;
}

/**
 * Button that opens a rotary knob for any target (SoC, temperature).
 *
 * value: the chosen target or null (default); fires "change" with detail {value}, null for the default.
 */
class SunflowTargetDial extends LitElement {
  static properties = {
    value: { type: Number },
    defaultValue: { type: Number, attribute: "default-value" },
    min: { type: Number },
    max: { type: Number },
    step: { type: Number },
    unit: {},
    label: {},
    // Text on the closed button; empty: the value
    text: {},
    active: { type: Boolean },
    disabled: { type: Boolean },
    _open: { state: true },
    _draft: { state: true },
    _left: { state: true },
    _up: { state: true },
  };

  constructor() {
    super();
    this.value = null;
    this.defaultValue = null;
    this.min = 0;
    this.max = 100;
    this.step = 1;
    this.unit = "%";
    this.label = "";
    this.text = "";
    this.active = false;
    this.disabled = false;
    this._open = false;
    this._draft = null;
    this._left = false;
    this._up = false;
    this._outside = (e) => {
      if (!e.composedPath().includes(this)) this._close();
    };
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    document.removeEventListener("pointerdown", this._outside);
  }

  _toggle(e) {
    if (this._open) return this._close();
    this._draft = this._clamp(this.value ?? this.defaultValue ?? (this.min + this.max) / 2);
    const box = this.getBoundingClientRect();
    // Opens towards the side with room
    this._left = box.left + PANEL_WIDTH < window.innerWidth;
    this._up = box.bottom + PANEL_HEIGHT > window.innerHeight && box.top > PANEL_HEIGHT;
    this._open = true;
    document.addEventListener("pointerdown", this._outside);
    // Opened with the keyboard: straight on to the knob
    if (e.detail === 0) this.updateComplete.then(() => this.renderRoot.querySelector(".knob")?.focus());
  }

  _close() {
    this._open = false;
    document.removeEventListener("pointerdown", this._outside);
  }

  _clamp(v) {
    const stepped = Math.round((v - this.min) / this.step) * this.step + this.min;
    return Math.min(this.max, Math.max(this.min, Number(stepped.toFixed(3))));
  }

  _emit(value) {
    this._close();
    this.dispatchEvent(new CustomEvent("change", { detail: { value }, bubbles: true, composed: true }));
  }

  _fromPointer(e) {
    const box = e.currentTarget.getBoundingClientRect();
    const deg = (Math.atan2(e.clientY - box.top - box.height / 2, e.clientX - box.left - box.width / 2) * 180) / Math.PI;
    const a = (deg - START + 720) % 360;
    // In the gap at the bottom: the nearer end
    const fraction = a <= SWEEP ? a / SWEEP : a < (SWEEP + 360) / 2 ? 1 : 0;
    this._draft = this._clamp(this.min + fraction * (this.max - this.min));
  }

  _down(e) {
    e.currentTarget.setPointerCapture(e.pointerId);
    this._dragging = true;
    this._fromPointer(e);
  }

  _move(e) {
    if (this._dragging) this._fromPointer(e);
  }

  _key(e) {
    const steps = { ArrowUp: 1, ArrowRight: 1, ArrowDown: -1, ArrowLeft: -1, PageUp: 10, PageDown: -10 };
    if (e.key in steps) this._draft = this._clamp(this._draft + steps[e.key] * this.step);
    else if (e.key === "Home") this._draft = this.min;
    else if (e.key === "End") this._draft = this.max;
    else if (e.key === "Enter") this._emit(this._draft);
    else if (e.key === "Escape") this._close();
    else return;
    e.preventDefault();
  }

  _wheel(e) {
    e.preventDefault();
    this._draft = this._clamp(this._draft + (e.deltaY < 0 ? 1 : -1) * this.step);
  }

  render() {
    const shown = this.text || (this.value !== null && this.value !== undefined ? `${this.value} ${this.unit}` : "…");
    return html`<button part="button" class="open ${this.active ? "on" : ""}" aria-pressed=${this.active} aria-expanded=${this._open}
        ?disabled=${this.disabled} title=${this.label} aria-label=${this.label} @click=${this._toggle}>${shown}</button>
      ${this._open ? this._renderPanel() : nothing}`;
  }

  _renderPanel() {
    const fraction = (this._draft - this.min) / (this.max - this.min);
    const [kx, ky] = point(fraction);
    const text = `${this._draft} ${this.unit}`;
    const hasDefault = this.defaultValue !== null && this.defaultValue !== undefined;
    const tick = hasDefault ? (Math.min(this.max, Math.max(this.min, this.defaultValue)) - this.min) / (this.max - this.min) : null;
    return html`<div class="panel ${this._left ? "left" : ""} ${this._up ? "up" : ""}" role="dialog" aria-label=${this.label}
      @keydown=${(e) => e.key === "Escape" && this._close()}>
      <svg class="knob" viewBox="0 0 120 120" tabindex="0" role="slider" aria-label=${this.label}
        aria-valuemin=${this.min} aria-valuemax=${this.max} aria-valuenow=${this._draft} aria-valuetext=${text}
        @pointerdown=${this._down} @pointermove=${this._move} @pointerup=${() => (this._dragging = false)}
        @keydown=${this._key} @wheel=${this._wheel}>
        ${svg`<path class="track" d=${arc(1)}></path>
          ${fraction > 0 ? svg`<path class="fill" d=${arc(fraction)}></path>` : nothing}
          ${tick !== null ? svg`<line class="tick" x1=${point(tick)[0]} y1=${point(tick)[1]}
            x2=${60 + (point(tick)[0] - 60) * 1.25} y2=${60 + (point(tick)[1] - 60) * 1.25}></line>` : nothing}
          <circle class="handle" cx=${kx} cy=${ky} r="8"></circle>
          <text x="60" y=${hasDefault ? 62 : 66}>${text}</text>
          ${hasDefault ? svg`<text class="default" x="60" y="80">${t("Standard {value}", { value: `${this.defaultValue} ${this.unit}` })}</text>` : nothing}`}
      </svg>
      <div class="steps">
        <button aria-label=${t("Weniger")} @click=${() => (this._draft = this._clamp(this._draft - this.step))}>−</button>
        <button aria-label=${t("Mehr")} @click=${() => (this._draft = this._clamp(this._draft + this.step))}>+</button>
      </div>
      <div class="actions">
        <button @click=${() => this._emit(null)}>${t("Standard")}${this.defaultValue !== null && this.defaultValue !== undefined
          ? html`<span class="std">${this.defaultValue} ${this.unit}</span>`
          : nothing}</button>
        <button class="primary" @click=${() => this._emit(this._draft)}>${t("Übernehmen")}</button>
      </div>
    </div>`;
  }

  static styles = [toggleSheet, css`
    :host { position: relative; display: inline-block; }
    button { font: inherit; color: inherit; cursor: pointer; }
    button:disabled { cursor: default; opacity: 0.6; }
    .open {
      min-width: 38px; min-height: 32px; padding: 0 8px; border: 0; border-radius: 7px; background: transparent;
      font-size: 13px; color: var(--sf-muted); white-space: nowrap;
    }
    .open.on { background: var(--sf-surface); color: var(--sf-text); font-weight: 600; box-shadow: 0 1px 2px rgb(0 0 0 / 0.1); }
    .panel {
      position: absolute; top: calc(100% + 6px); right: 0; z-index: 20; width: 200px; padding: 12px;
      display: flex; flex-direction: column; gap: 8px; border: 1px solid var(--sf-border); border-radius: 12px;
      background: var(--sf-surface); box-shadow: 0 6px 24px rgb(0 0 0 / 0.18);
    }
    .panel.left { right: auto; left: 0; }
    .panel.up { top: auto; bottom: calc(100% + 6px); }
    .knob { overflow: visible; width: 160px; height: 160px; align-self: center; touch-action: none; cursor: grab; border-radius: 50%; outline: none; }
    .knob:focus-visible { outline: 2px solid var(--sf-primary); outline-offset: 2px; }
    .track, .fill { fill: none; stroke-width: 10; stroke-linecap: round; }
    .track { stroke: var(--sf-subtle); }
    .fill { stroke: var(--sf-primary); }
    .handle { fill: var(--sf-surface); stroke: var(--sf-primary); stroke-width: 3; }
    text { font-size: 20px; font-weight: 600; text-anchor: middle; fill: var(--sf-text); }
    text.default { font-size: 9px; font-weight: 400; fill: var(--sf-muted); }
    .tick { stroke: var(--sf-text); stroke-width: 2.5; stroke-linecap: round; }
    .steps, .actions { display: flex; gap: 6px; }
    .steps button { flex: 1; min-height: 36px; font-size: 18px; }
    .steps button, .actions button {
      border: 1px solid var(--sf-border); border-radius: 8px; background: transparent; padding: 0 8px;
    }
    .actions button { flex: 1; min-height: 34px; font-size: 13px; }
    .std { display: block; font-size: 11px; color: var(--sf-muted); }
    .actions .primary { background: var(--sf-primary); border-color: var(--sf-primary); color: var(--sf-on-primary, #fff); }
  `];
}

customElements.define("sf-target-dial", SunflowTargetDial);
