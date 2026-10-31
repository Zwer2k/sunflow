import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import "../components/roof.js";

const TIGO_REPO = "https://github.com/Zwer2k/ha-tigo-rs485";

/** Optimizers (settings area "Optimierer"): sources, string assignment and their places on the roof. */
class SunflowOptimizers extends LitElement {
  static properties = {
    _data: { state: true },
    _error: { state: true },
    _saving: { state: true },
  };

  constructor() {
    super();
    this._data = null;
    this._error = null;
    this._saving = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
  }

  async _load() {
    try {
      this._data = await api.optimizers();
      this._error = null;
    } catch (err) {
      this._error = `Optimierer nicht abrufbar: ${err.message}`;
    }
  }

  /** The stored settings with one optimizer changed; proposals stay proposals (null). */
  async _save(changes = {}, tigoPlant = this._data.tigo_plant_chosen, layouts = this._data.layouts) {
    const placements = this._data.optimizers.map((o) => ({
      ha_device_id: o.id,
      string: o.string_auto ? null : (o.string ?? ""),
      row: o.row,
      col: o.col,
      name: o.name !== o.label ? o.name : null,
      rotated: o.rotated || null,
      ...(changes[o.id] ?? {}),
    }));
    this._saving = true;
    try {
      await api.setOptimizers({ tigo_plant: tigoPlant || null, placements, layouts });
      await this._load();
    } catch (err) {
      this._error = `Nicht gespeichert: ${err.message}`;
    } finally {
      this._saving = false;
    }
  }

  _move(optimizers, { id, row, col }) {
    const moved = optimizers.find((o) => o.id === id);
    const other = optimizers.find((o) => o.id !== id && o.row === row && o.col === col);
    const changes = { [id]: { row, col } };
    // A taken place: the two swap
    if (other) changes[other.id] = { row: moved.row, col: moved.col };
    return this._save(changes);
  }

  _toggleBreak(key, { row, col }) {
    const layout = { row_breaks: [], col_breaks: [], ...this._data.layouts[key] };
    const field = row !== undefined ? "row_breaks" : "col_breaks";
    const at = row ?? col;
    const breaks = layout[field];
    layout[field] = breaks.includes(at) ? breaks.filter((b) => b !== at) : [...breaks, at].sort((a, b) => a - b);
    return this._save({}, this._data.tigo_plant_chosen, { ...this._data.layouts, [key]: layout });
  }

  _reset(optimizers) {
    // Back to the proposal: string and place from the source
    return this._save(Object.fromEntries(optimizers.map((o) => [o.id, { string: null, row: null, col: null }])));
  }

  render() {
    const data = this._data;
    return html`
      <div class="heading">
        <h1>Optimierer</h1>
        <p class="muted">Optimierer an den PV-Modulen: welchem String sie angehören und wo sie auf dem Dach sitzen. In der
          Live-Ansicht öffnet ein Klick auf einen String seine Module mit den aktuellen Werten.</p>
      </div>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${data === null && !this._error ? html`<p class="muted">Lade …</p>` : nothing}
      ${data ? this._renderSources(data) : nothing}
      ${data ? this._renderStrings(data) : nothing}
    `;
  }

  _renderSources(data) {
    const tigo = data.sources.find((s) => s.key === "tigo");
    const plants = [...new Map(data.strings.map((s) => [s.device_id, s.plant])).entries()];
    return html`<section class="card">
      <h2>Quellen</h2>
      <ul class="sources">
        ${data.sources.map((s) => html`<li>
          <strong>${s.name}</strong>
          <span class="small ${s.count ? "" : "muted"}">${s.count
            ? `${s.count} Optimierer gefunden`
            : s.installed
              ? "Integration eingerichtet, aber keine Optimierer gefunden"
              : "Integration nicht eingerichtet"}</span>
          ${s.key === "huawei"
            ? html`<span class="small muted">Über die Integration „Huawei Solar“; Optimierer erscheinen als Unter-Geräte des
                Wechselrichters. Ist nur ein String in Betrieb, ordnet Sunflow sie ihm zu.</span>`
            : html`<span class="small muted">Über die Integration
                <a href=${TIGO_REPO} target="_blank" rel="noopener">Tigo RS485</a>, die den Bus zwischen CCA und TAP mitliest.
                Dort jedem Optimierer ein Label geben: „A1“ ist String A, Modul 1 – danach ordnet Sunflow ihn zu.</span>`}
        </li>`)}
      </ul>
      ${tigo?.count
        ? html`<label class="field">
            <span class="field-label">Tigo-Optimierer hängen am Wechselrichter</span>
            <select ?disabled=${this._saving} @change=${(e) => this._save({}, e.target.value)}>
              <option value="" ?selected=${!data.tigo_plant_chosen}>automatisch (${plants.find(([id]) => id === data.tigo_plant)?.[1] ?? "keiner"})</option>
              ${plants.map(([id, name]) => html`<option value=${id} ?selected=${data.tigo_plant_chosen === id}>${name}</option>`)}
            </select>
            <span class="small muted">String A ist dessen String 1, B String 2 usw.</span>
          </label>`
        : nothing}
    </section>`;
  }

  _renderStrings(data) {
    if (!data.optimizers.length) {
      return html`<p class="muted">Keine Optimierer gefunden.</p>`;
    }
    const groups = data.strings
      .map((s) => ({ ...s, optimizers: data.optimizers.filter((o) => o.string === s.key) }))
      .filter((s) => s.optimizers.length);
    const unassigned = data.optimizers.filter((o) => !o.string || !data.strings.some((s) => s.key === o.string));
    return html`
      ${groups.map((s) => html`<section class="card">
        <div class="card-head">
          <h2>${s.name} <span class="small muted">· ${s.optimizers.length} Optimierer</span></h2>
          <button class="secondary" ?disabled=${this._saving} @click=${() => this._reset(s.optimizers)}>Vorschlag wiederherstellen</button>
        </div>
        <p class="small muted">So anordnen, wie die Module auf dem Dach liegen (oben = First): Modul auf einen freien Platz
          ziehen – oder antippen und dann den Platz antippen. Auf ein anderes Modul gezogen tauschen beide. Ein ausgewähltes
          Modul lässt sich quer legen. Zwischen zwei Reihen setzt „+ Trenner“ die Reihen darunter ab, das schmale „+“ zwischen
          zwei Plätzen die Plätze rechts davon in allen Reihen (z. B. ein anderes Dachstück).</p>
        <sf-roof editable .optimizers=${s.optimizers} .visible=${["power"]}
          .breaks=${data.layouts[s.key]?.row_breaks ?? []}
          .colBreaks=${data.layouts[s.key]?.col_breaks ?? []}
          @break=${(e) => this._toggleBreak(s.key, e.detail)}
          @move=${(e) => this._move(s.optimizers, e.detail)}
          @rotate=${(e) => this._save({ [e.detail.id]: { rotated: !s.optimizers.find((o) => o.id === e.detail.id).rotated || null } })}></sf-roof>
      </section>`)}
      <section class="card">
        <h2>Zuordnung</h2>
        <table>
          <thead><tr><th>Optimierer</th><th>Quelle</th><th>String</th><th>Name</th></tr></thead>
          <tbody>
            ${data.optimizers.map((o) => html`<tr class=${unassigned.includes(o) ? "open" : ""}>
              <td>${o.label}</td>
              <td class="small muted">${data.sources.find((s) => s.key === o.source)?.name ?? o.source}</td>
              <td>
                <select ?disabled=${this._saving} aria-label="String von ${o.label}"
                  @change=${(e) => this._save({ [o.id]: { string: e.target.value === "auto" ? null : e.target.value, row: null, col: null } })}>
                  <option value="auto" ?selected=${o.string_auto}>automatisch${o.string_auto
                    ? ` (${data.strings.find((s) => s.key === o.string)?.name ?? "unbekannt"})`
                    : ""}</option>
                  <option value="" ?selected=${!o.string_auto && !o.string}>keiner</option>
                  ${data.strings.map((s) => html`<option value=${s.key} ?selected=${!o.string_auto && o.string === s.key}>${s.name}</option>`)}
                </select>
              </td>
              <td><input .value=${o.name} aria-label="Name von ${o.label}" ?disabled=${this._saving}
                @change=${(e) => this._save({ [o.id]: { name: e.target.value.trim() && e.target.value.trim() !== o.label ? e.target.value.trim() : null } })}></td>
            </tr>`)}
          </tbody>
        </table>
        ${unassigned.length
          ? html`<p class="small warn-text">${unassigned.length} Optimierer ohne String – sie erscheinen in keiner Dachansicht.</p>`
          : nothing}
      </section>
    `;
  }

  static styles = css`
    :host { display: flex; flex-direction: column; gap: 16px; }
    * { box-sizing: border-box; }
    button, select, input { font: inherit; color: inherit; }
    h1 { margin: 0; font-size: 20px; }
    h2 { margin: 0; font-size: 16px; }
    p { margin: 0; }
    a { color: var(--sf-primary); }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .warn-text { color: var(--sf-warn-text); }
    .heading { display: flex; flex-direction: column; gap: 6px; }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    .card { border: 1px solid var(--sf-border); border-radius: 10px; padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
    .card-head { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: space-between; }
    .sources { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
    .sources li { display: flex; flex-direction: column; gap: 2px; }
    .field { display: flex; flex-direction: column; gap: 4px; }
    .field-label { font-size: 13px; font-weight: 600; }
    select, input {
      min-height: 40px; padding: 0 10px; font-size: 14px; border: 1px solid var(--sf-border-strong);
      border-radius: 8px; background: var(--sf-surface); color: var(--sf-text); max-width: 100%;
    }
    .secondary {
      min-height: 40px; padding: 0 14px; border-radius: 8px; border: 1px solid var(--sf-border-strong);
      background: var(--sf-surface); cursor: pointer;
    }
    table { border-collapse: collapse; width: 100%; font-size: 14px; display: block; overflow-x: auto; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--sf-border); vertical-align: middle; }
    th { font-size: 13px; color: var(--sf-muted); font-weight: 600; }
    tr.open td:first-child { color: var(--sf-warn-text); font-weight: 600; }
  `;
}

customElements.define("sf-optimizers", SunflowOptimizers);
