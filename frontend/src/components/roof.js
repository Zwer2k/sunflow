import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";

/** Values an optimizer can report, in display order: short label and decimals. */
export const OPTIMIZER_VALUES = {
  power: { label: "Leistung", short: "", unit: "W", digits: 0 },
  voltage_in: { label: "Spannung Modul", short: "U", unit: "V", digits: 1 },
  current_in: { label: "Strom Modul", short: "I", unit: "A", digits: 2 },
  voltage_out: { label: "Spannung Ausgang", short: "U→", unit: "V", digits: 1 },
  current_out: { label: "Strom Ausgang", short: "I→", unit: "A", digits: 2 },
  temperature: { label: "Temperatur", short: "", unit: "°C", digits: 0 },
  pwm: { label: "PWM", short: "PWM", unit: "", digits: 0 },
};

const number = (value, digits) =>
  value.toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function optimizerValue(key, value) {
  const spec = OPTIMIZER_VALUES[key];
  if (value === null || value === undefined) return "–";
  return `${spec.short ? `${spec.short} ` : ""}${number(value, spec.digits)}${spec.unit ? ` ${spec.unit}` : ""}`;
}

/**
 * Optimizers as small PV modules where they sit on the roof.
 *
 * optimizers: [{id, name, row, col, values}]; visible: value keys to show.
 * editable: empty cells to drop on; fires "move" with detail {id, row, col}.
 * Moving works by drag and drop or by tapping a module and then a cell (phones).
 */
class SunflowRoof extends LitElement {
  static properties = {
    optimizers: { attribute: false },
    visible: { attribute: false },
    editable: { type: Boolean },
    _selected: { state: true },
  };

  constructor() {
    super();
    this.optimizers = [];
    this.visible = ["power"];
    this.editable = false;
    this._selected = null;
  }

  render() {
    const placed = this.optimizers.filter((o) => o.row !== null && o.col !== null);
    // Editing: one free row and column more to move modules to
    const extra = this.editable ? 1 : 0;
    const rows = Math.max(0, ...placed.map((o) => o.row + 1)) + extra;
    const cols = Math.max(0, ...placed.map((o) => o.col + 1)) + extra;
    const at = new Map(placed.map((o) => [`${o.row}:${o.col}`, o]));
    const maxPower = Math.max(1, ...this.optimizers.map((o) => o.values?.power ?? 0));
    const cells = [];
    for (let row = 0; row < rows; row += 1) {
      for (let col = 0; col < cols; col += 1) {
        const optimizer = at.get(`${row}:${col}`);
        cells.push(optimizer ? this._module(optimizer, maxPower, row, col) : this._empty(row, col));
      }
    }
    return html`<div class="roof" style="grid-template-columns: repeat(${Math.max(cols, 1)}, var(--module-width))">
      ${cells}
    </div>`;
  }

  _module(optimizer, maxPower, row, col) {
    const power = optimizer.values?.power;
    // Lighter modules deliver less than the best one
    const share = power === null || power === undefined ? 0 : Math.round((Math.max(power, 0) / maxPower) * 70);
    const offline = power === null || power === undefined;
    return html`<div class="module ${offline ? "offline" : ""} ${this._selected === optimizer.id ? "selected" : ""}"
      style="grid-row: ${row + 1}; grid-column: ${col + 1}; --share: ${share}%"
      title=${`${optimizer.name}\n${Object.keys(OPTIMIZER_VALUES)
        .filter((k) => k in (optimizer.values ?? {}))
        .map((k) => `${OPTIMIZER_VALUES[k].label}: ${optimizerValue(k, optimizer.values[k])}`)
        .join("\n")}`}
      draggable=${this.editable ? "true" : "false"}
      @dragstart=${(e) => e.dataTransfer.setData("text/plain", optimizer.id)}
      @dragover=${(e) => this.editable && e.preventDefault()}
      @drop=${(e) => this._drop(e, row, col)}
      @click=${() => this._tap(optimizer.id, row, col)}>
      <span class="name">${optimizer.name}</span>
      ${this.visible.filter((k) => k in (optimizer.values ?? {})).map(
        (k) => html`<span class="value ${k === "power" ? "main" : ""}">${optimizerValue(k, optimizer.values[k])}</span>`,
      )}
    </div>`;
  }

  _empty(row, col) {
    if (!this.editable) return nothing;
    return html`<div class="cell ${this._selected ? "target" : ""}" style="grid-row: ${row + 1}; grid-column: ${col + 1}"
      @dragover=${(e) => e.preventDefault()} @drop=${(e) => this._drop(e, row, col)}
      @click=${() => this._tap(null, row, col)}></div>`;
  }

  _drop(event, row, col) {
    if (!this.editable) return;
    event.preventDefault();
    this._move(event.dataTransfer.getData("text/plain"), row, col);
  }

  _tap(id, row, col) {
    if (!this.editable) return;
    if (this._selected && this._selected !== id) {
      this._move(this._selected, row, col);
    } else {
      this._selected = this._selected === id ? null : id;
    }
  }

  _move(id, row, col) {
    this._selected = null;
    if (!id) return;
    this.dispatchEvent(new CustomEvent("move", { detail: { id, row, col } }));
  }

  static styles = css`
    :host { display: block; overflow-x: auto; --module-width: 64px; }
    .roof { display: grid; gap: 4px; padding: 4px; width: max-content; }
    .module, .cell { width: var(--module-width); aspect-ratio: 1 / 1.6; border-radius: 4px; }
    .module {
      display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 1px; padding: 2px;
      border: 1px solid var(--sf-border-strong); cursor: default; user-select: none; overflow: hidden;
      /* Solar cells: a fine grid over the module, filled by the share of the best module's power */
      background:
        linear-gradient(to right, transparent 32%, rgb(255 255 255 / 0.25) 33%, transparent 34%, transparent 65%,
          rgb(255 255 255 / 0.25) 66%, transparent 67%),
        linear-gradient(to bottom, transparent 24%, rgb(255 255 255 / 0.25) 25%, transparent 26%, transparent 49%,
          rgb(255 255 255 / 0.25) 50%, transparent 51%, transparent 74%, rgb(255 255 255 / 0.25) 75%, transparent 76%),
        color-mix(in srgb, var(--sf-series-2) var(--share), var(--sf-subtle));
    }
    .module.offline { opacity: 0.55; }
    .module.selected { outline: 3px solid var(--sf-primary); outline-offset: 1px; }
    :host([editable]) .module { cursor: grab; }
    .cell { border: 1px dashed var(--sf-border-strong); cursor: pointer; }
    .cell.target { border-color: var(--sf-primary); background: var(--sf-primary-bg); }
    .name { font-size: 10px; color: var(--sf-muted); max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .value { font-size: 10px; color: var(--sf-text); font-variant-numeric: tabular-nums; white-space: nowrap; }
    .value.main { font-size: 12px; font-weight: 600; }
  `;
}

customElements.define("sf-roof", SunflowRoof);
