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
 * editable: empty cells to drop on; fires "move" with detail {id, row, col} and "rotate" with {id}.
 * breaks / colBreaks: rows and places before which a separator sets the following ones apart; editing
 * toggles them ("break" with {row} or {col}).
 * Rotated modules lie landscape and push the following modules of their row aside; each row
 * is laid out on its own, so other rows keep their spacing.
 * Moving works by drag and drop or by tapping a module and then a cell (phones).
 */
class SunflowRoof extends LitElement {
  static properties = {
    optimizers: { attribute: false },
    visible: { attribute: false },
    breaks: { attribute: false },
    colBreaks: { attribute: false },
    editable: { type: Boolean },
    _selected: { state: true },
  };

  constructor() {
    super();
    this.optimizers = [];
    this.visible = ["power"];
    this.breaks = [];
    this.colBreaks = [];
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
    const lines = [];
    for (let row = 0; row < rows; row += 1) {
      const cells = [];
      // Showing only: free places count up to the row's last module, as gaps
      const last = this.editable ? cols - 1 : Math.max(-1, ...placed.filter((o) => o.row === row).map((o) => o.col));
      for (let col = 0; col <= last; col += 1) {
        if (col > 0) cells.push(this._colSeparator(col));
        const optimizer = at.get(`${row}:${col}`);
        cells.push(optimizer ? this._module(optimizer, maxPower, row, col) : this._empty(row, col));
      }
      if (row > 0) lines.push(this._separator(row));
      lines.push(html`<div class="row">${cells}</div>`);
    }
    const selected = this.optimizers.find((o) => o.id === this._selected);
    return html`<div class="roof">${lines}</div>
    ${this.editable
      ? html`<p class="caption">${selected
          ? html`Ausgewählt: <strong>${selected.name}</strong>${selected.name !== selected.label ? ` (${selected.label})` : ""} –
              jetzt den Platz antippen oder
              <button class="rotate" @click=${() => this._rotate(selected.id)}>${selected.rotated ? "hochkant stellen" : "quer legen"}</button>`
          : "Modul antippen, um es auszuwählen"}</p>`
      : nothing}`;
  }

  _module(optimizer, maxPower, row, col) {
    const power = optimizer.values?.power;
    // Lighter modules deliver less than the best one
    const share = power === null || power === undefined ? 0 : Math.round((Math.max(power, 0) / maxPower) * 70);
    const offline = power === null || power === undefined;
    return html`<div class="module ${offline ? "offline" : ""} ${optimizer.rotated ? "rotated" : ""} ${this._selected === optimizer.id ? "selected" : ""}"
      style="--share: ${share}%"
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

  _separator(row) {
    const on = this.breaks.includes(row);
    if (!this.editable) return on ? html`<div class="break" role="separator"></div>` : nothing;
    return html`<button class="break-toggle ${on ? "on" : ""}" aria-pressed=${on ? "true" : "false"}
      title=${on ? "Trenner entfernen" : "Trenner einfügen: die Reihen darunter liegen abseits"}
      @click=${() => this.dispatchEvent(new CustomEvent("break", { detail: { row } }))}>
      <span>${on ? "Trenner ✕" : "+ Trenner"}</span>
    </button>`;
  }

  _colSeparator(col) {
    const on = this.colBreaks.includes(col);
    if (!this.editable) return on ? html`<div class="vbreak" role="separator"></div>` : nothing;
    return html`<button class="vbreak-toggle ${on ? "on" : ""}" aria-pressed=${on ? "true" : "false"}
      aria-label=${on ? `Trenner vor Platz ${col + 1} entfernen` : `Trenner vor Platz ${col + 1} einfügen`}
      title=${on ? "Trenner entfernen (alle Reihen)" : "Trenner einfügen: die Plätze rechts liegen abseits (alle Reihen)"}
      @click=${() => this.dispatchEvent(new CustomEvent("break", { detail: { col } }))}>${on ? "✕" : "+"}</button>`;
  }

  _empty(row, col) {
    if (!this.editable) return html`<div class="gap"></div>`;
    return html`<div class="cell ${this._selected ? "target" : ""}"
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

  _rotate(id) {
    this._selected = null;
    this.dispatchEvent(new CustomEvent("rotate", { detail: { id } }));
  }

  _move(id, row, col) {
    this._selected = null;
    if (!id) return;
    this.dispatchEvent(new CustomEvent("move", { detail: { id, row, col } }));
  }

  static styles = css`
    :host { display: block; overflow-x: auto; --module-width: 64px; }
    .roof { display: flex; flex-direction: column; gap: 4px; padding: 4px; width: max-content; }
    .row { display: flex; gap: 4px; align-items: center; min-height: var(--module-width); }
    .module, .cell, .gap { flex: none; width: var(--module-width); height: calc(var(--module-width) * 1.6); border-radius: 4px; }
    .module.rotated { width: calc(var(--module-width) * 1.6); height: var(--module-width); }
    .module {
      display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 1px; padding: 2px;
      border: 1px solid var(--sf-border-strong); cursor: default; user-select: none; overflow: hidden;
      /* Solar cells: a fine grid over the module, filled by the share of the best module's power */
      --cells-short: to right;
      --cells-long: to bottom;
      background:
        linear-gradient(var(--cells-short), transparent 32%, rgb(255 255 255 / 0.25) 33%, transparent 34%, transparent 65%,
          rgb(255 255 255 / 0.25) 66%, transparent 67%),
        linear-gradient(var(--cells-long), transparent 24%, rgb(255 255 255 / 0.25) 25%, transparent 26%, transparent 49%,
          rgb(255 255 255 / 0.25) 50%, transparent 51%, transparent 74%, rgb(255 255 255 / 0.25) 75%, transparent 76%),
        color-mix(in srgb, var(--sf-series-2) var(--share), var(--sf-subtle));
    }
    /* Landscape: the cell grid turns with the module */
    .module.rotated { --cells-short: to bottom; --cells-long: to right; }
    .module.offline { opacity: 0.55; }
    .module.selected { outline: 3px solid var(--sf-primary); outline-offset: 1px; }
    :host([editable]) .module { cursor: grab; }
    .cell { border: 1px dashed var(--sf-border-strong); cursor: pointer; }
    .cell.target { border-color: var(--sf-primary); background: var(--sf-primary-bg); }
    /* Long serial numbers (Huawei) break onto a second line instead of being cut */
    .name {
      font-size: 9px; line-height: 1.15; color: var(--sf-muted); max-width: 100%; text-align: center;
      overflow-wrap: anywhere; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden;
    }
    .caption { margin: 4px 4px 0; font-size: 13px; color: var(--sf-muted); }
    /* Rows set apart (another roof part): space with a dashed line in the middle */
    .break { height: 1px; margin: 8px 0; border-top: 2px dashed var(--sf-border-strong); }
    .break-toggle {
      display: flex; align-items: center; gap: 6px; width: 100%; min-height: 20px; padding: 0; border: 0; cursor: pointer;
      background: linear-gradient(var(--sf-border) 0 0) center / 100% 1px no-repeat; color: var(--sf-muted); font: inherit;
      font-size: 11px; opacity: 0.6;
    }
    .break-toggle span { margin: 0 auto; padding: 0 6px; background: var(--sf-surface); border-radius: 4px; }
    .break-toggle:hover, .break-toggle:focus-visible { opacity: 1; }
    .vbreak { flex: none; align-self: stretch; width: 2px; margin: 0 8px; border-left: 2px dashed var(--sf-border-strong); }
    .vbreak-toggle {
      flex: none; align-self: stretch; width: 14px; margin: 0 -2px; padding: 0; border: 0; cursor: pointer; font: inherit;
      font-size: 11px; color: var(--sf-muted); opacity: 0.35;
      background: linear-gradient(var(--sf-border) 0 0) center / 1px 100% no-repeat;
    }
    .vbreak-toggle:hover, .vbreak-toggle:focus-visible { opacity: 1; }
    .vbreak-toggle.on {
      opacity: 1; width: 20px; margin: 0 2px; color: var(--sf-text);
      background: linear-gradient(to bottom, var(--sf-border-strong) 50%, transparent 0) center / 2px 10px repeat-y;
    }
    .break-toggle.on {
      opacity: 1; min-height: 28px; color: var(--sf-text);
      background: linear-gradient(to right, var(--sf-border-strong) 50%, transparent 0) center / 10px 2px repeat-x;
    }
    .rotate {
      font: inherit; font-size: 13px; min-height: 32px; padding: 0 10px; margin-left: 2px; border-radius: 8px; cursor: pointer;
      border: 1px solid var(--sf-primary); background: var(--sf-primary-bg); color: var(--sf-primary-text);
    }
    .value { font-size: 10px; color: var(--sf-text); font-variant-numeric: tabular-nums; white-space: nowrap; }
    .value.main { font-size: 12px; font-weight: 600; }
  `;
}

customElements.define("sf-roof", SunflowRoof);
