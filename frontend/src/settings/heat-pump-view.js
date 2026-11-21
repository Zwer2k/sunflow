import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { fromDisplay, toDisplay } from "../lib/format.js";
import "../components/picker.js";
import { decimal, t } from "../lib/i18n.js";
import { lineChart, SERIES_COLORS } from "../components/charts.js";

// Room temperature at which the cooling rate is shown (api/house.py)
const COOLING_INSIDE_C = 20;
// Where the outside temperature comes from (house.outside_source); a function so the texts follow the language
const OUTSIDE_SOURCES = () => ({
  auto: t("automatisch (eigener Sensor, sonst Wärmepumpe)"),
  heat_pump: t("Wärmepumpe: Außentemperatur"),
  heat_pump_unit: t("Wärmepumpe: Außengerät (TA2)"),
  learning_source: t("eigener Sensor (Lernquelle)"),
});

/** The house: its rooms and what Sunflow learned from them (settings area "Wärmepumpe"). */
class SunflowHeatPump extends LitElement {
  static properties = {
    _candidates: { state: true },
    _house: { state: true },
    _houseEdit: { state: true },
    _houseModel: { state: true },
    _error: { state: true },
    _busy: { state: true },
  };

  constructor() {
    super();
    this._candidates = null;
    this._house = null;
    this._houseEdit = null;
    this._houseModel = null;
    this._error = null;
    this._busy = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
  }

  async _load() {
    try {
      this._house = await api.house();
      this._error = null;
    } catch (err) {
      this._error = t("Einstellungen nicht verfügbar: {error}", { error: err.message });
    }
    this._loadHouseModel();
  }

  async _loadHouseModel() {
    try {
      this._houseModel = await api.houseModel();
    } catch (err) {
      this._houseModel = { state: "error", error: err.message };
    }
  }

  async _loadCandidates() {
    if (this._candidates) return;
    try {
      this._candidates = await api.candidates();
    } catch (err) {
      this._error = t("Entitäten konnten nicht geladen werden: {error}", { error: err.message });
    }
  }

  render() {
    if (this._house === null) {
      return html`<h1>${t("Wärmepumpe")}</h1>${this._error
        ? html`<div class="alert error" role="alert">${this._error}</div>`
        : html`<p class="muted">${t("Lade …")}</p>`}`;
    }
    return html`
      <div class="heading">
        <h1>${t("Wärmepumpe")}</h1>
        <p class="muted small">${t("Die Regelung der Wärmepumpe wird neu gebaut. Bis dahin lernt Sunflow aus den Raumtemperaturen, wie sich das Haus verhält.")}</p>
      </div>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${this._renderHouse()}
    `;
  }

  /** The house: rooms and heat into it; below what Sunflow learned from them. */
  _renderHouse() {
    if (this._candidates === null) this._loadCandidates();
    const house = this._houseEdit ?? this._house;
    const candidates = this._candidates ?? {};
    const options = (list) => (list ?? []).map((c) => ({ value: c.entity_id, label: c.name, detail: `${c.state ?? "–"} ${c.unit ?? ""}` }));
    const set = (changes) => (this._houseEdit = { ...house, ...changes });
    const setRoom = (i, changes) => set({ rooms: house.rooms.map((r, k) => (k === i ? { ...r, ...changes } : r)) });
    return html`
      <section class="device">
        <span class="device-title">${t("Haus")}</span>
        <p class="small muted">${t("Aus den Raumtemperaturen lernt Sunflow, wie schnell das Haus auskühlt und wie stark Heizung und Sonne es erwärmen. Sunflow regelt keine einzelnen Räume.")}</p>
        <fieldset class="group">
          <legend>${t("Räume")}</legend>
          ${house.rooms.map((room, i) => html`<div class="room">
            <div class="field grow">
              <span class="field-label">${t("Temperatur")}</span>
              <sf-picker label=${t("Temperatur")} placeholder=${t("Name oder Entität suchen …")}
                .value=${room.entity_id} .options=${options(candidates.temperature)}
                @change=${(e) => e.detail.value && setRoom(i, { entity_id: e.detail.value })}></sf-picker>
            </div>
            <label class="field narrow">
              <span class="field-label">${t("Gewicht")}</span>
              <span class="input-unit"><input inputmode="decimal" .value=${toDisplay(room.weight, "")}
                @change=${(e) => { const v = fromDisplay(e.target.value, ""); if (v > 0) setRoom(i, { weight: v }); }}></span>
            </label>
            <button class="secondary" @click=${() => set({ rooms: house.rooms.filter((_, k) => k !== i) })}>${t("Entfernen")}</button>
          </div>`)}
          <div><button class="secondary" @click=${() => set({ rooms: [...house.rooms, { entity_id: "", weight: 1 }] })}>${t("Raum hinzufügen")}</button></div>
          <p class="small muted">${t("Gewicht z. B. nach Fläche; ohne Angabe zählen alle Räume gleich.")}</p>
          <div class="fields">
            <div class="field">
              <span class="field-label">${t("Wärme ins Haus (optional)")}</span>
              <sf-picker label=${t("Wärme ins Haus (optional)")} empty-label=${t("aus der Wärmepumpe")} placeholder=${t("Name oder Entität suchen …")}
                .value=${house.heat_entity ?? ""} .options=${options(candidates.power)}
                @change=${(e) => set({ heat_entity: e.detail.value || null })}></sf-picker>
              <span class="small muted">${t("Wärmeleistung in W, z. B. vom Wärmemengenzähler der Heizung. Ohne Angabe nimmt Sunflow die Wärme der Wärmepumpe.")}</span>
            </div>
            <label class="field">
              <span class="field-label">${t("Außentemperatur")}</span>
              <select @change=${(e) => set({ outside_source: e.target.value })}>
                ${Object.entries(OUTSIDE_SOURCES()).map(([value, label]) => html`<option value=${value} ?selected=${(house.outside_source ?? "auto") === value}>${label}</option>`)}
              </select>
              <span class="small muted">${t("Für das Hausmodell und die Aufzeichnung der Wärmepumpe. Den eigenen Sensor wählst du unter Lernquellen.")}</span>
            </label>
          </div>
        </fieldset>
        ${this._houseEdit
          ? html`<div class="actions">
              <button class="primary" ?disabled=${this._busy || house.rooms.some((r) => !r.entity_id)} @click=${() => this._saveHouse()}>${t("Speichern")}</button>
              <button class="secondary" @click=${() => (this._houseEdit = null)}>${t("Abbrechen")}</button>
            </div>`
          : nothing}
        ${this._renderHouseModel()}
      </section>
    `;
  }

  async _saveHouse() {
    this._busy = true;
    try {
      this._house = await api.setHouse(this._houseEdit);
      this._houseEdit = null;
      this._houseModel = null;
      this._loadHouseModel();
    } catch (err) {
      this._error = t("Haus konnte nicht gespeichert werden: {error}", { error: err.message });
    } finally {
      this._busy = false;
    }
  }

  /** What Sunflow learned about the house, in words, with the last week measured and expected. */
  _renderHouseModel() {
    const result = this._houseModel;
    if (!result || result.state === "no_rooms") return nothing;
    const one = (v) => decimal(v.toFixed(1));
    const messages = {
      no_outside: t("Es fehlt eine Außentemperatur: an der Wärmepumpe oder unter Lernquellen."),
      error: t("Nicht verfügbar: {error}", { error: result.error ?? "" }),
      learning: t("Lernt noch: {days} von {min} Tagen mit Raum- und Außentemperatur.", { days: result.days ?? 0, min: result.min_days ?? 7 }),
    };
    if (messages[result.state]) return html`<p class="small muted">${messages[result.state]}</p>`;
    const m = result.model;
    const heat = {
      meter: t("Wärme vom gewählten Sensor"),
      measured: t("Wärme der Wärmepumpe gemessen"),
      cop: t("Wärme aus Strom × COP der Wärmepumpe"),
      estimated: t("Wärme aus dem Strom der Wärmepumpe geschätzt (COP aus der Außentemperatur)"),
    }[result.heat_source] ?? t("ohne Wärmemessung – Heizung und innere Wärme lassen sich nicht trennen");
    const tile = (label, value, help = null) => html`<div class="tile"><div class="small muted">${label}</div><div class="value">${value}</div>${help ? html`<div class="small muted">${help}</div>` : nothing}</div>`;
    const name = (entityId) => (this._candidates?.temperature ?? []).find((c) => c.entity_id === entityId)?.name ?? entityId;
    const points = result.chart ?? [];
    return html`
      <div class="learned">
        <span class="field-label">${t("Gelernt")}</span>
        <p class="small ${result.state === "ready" ? "muted" : "warn-text"}">${result.state === "ready"
          ? t("Aus {days} Tagen · {heat}", { days: result.days, heat })
          : m.plausible
            ? t("Noch unsicher: Die Vorhersage ist nicht besser als „die Temperatur bleibt“. Sunflow nutzt das Modell noch nicht. ({heat})", { heat })
            : t("Noch nicht plausibel – vielleicht heizt etwas, das nicht gemessen wird, oder ein Sensor hängt in der Sonne. ({heat})", { heat })}</p>
        <div class="tiles">
          ${m.tau_h != null ? tile(t("Zeitkonstante"), `${Math.round(m.tau_h)} h`, t("bis zwei Drittel des Unterschieds nach außen weg sind")) : nothing}
          ${tile(t("Abkühlung bei 0 °C außen"), `${one(m.cooling_k_per_h_at_0)} K/h`, t("ohne Heizung und Sonne, bei {temp} °C innen", { temp: decimal(String(COOLING_INSIDE_C)) }))}
          ${m.loss_w_per_k != null ? tile(t("Wärmeverlust"), `${Math.round(m.loss_w_per_k)} W/K`, t("bei −10 °C außen etwa {kw} kW Heizleistung", { kw: one(m.loss_w_per_k * (COOLING_INSIDE_C + 10) / 1000) })) : nothing}
          ${m.capacity_kwh_per_k != null ? tile(t("Wärmespeicher"), `${Math.round(m.capacity_kwh_per_k)} kWh/K`, t("Wärme für 1 K wärmer im ganzen Haus")) : nothing}
          ${m.sun_k_per_h != null ? tile(t("Sonne"), `${m.sun_k_per_h >= 0 ? "+" : ""}${one(m.sun_k_per_h)} K/h`, t("bei voller Sonne")) : nothing}
          ${m.error_k != null ? tile(t("Vorhersage über 24 h"), `± ${one(m.error_k)} K`, t("„bleibt gleich“ läge bei ± {k} K", { k: one(m.baseline_k) })) : nothing}
        </div>
        ${result.excluded_hours
          ? html`<p class="small muted">${t("{hours} Stunden mit Warmwasser oder Abtauen sind ausgelassen: deren Wärme ging nicht ins Haus.", { hours: result.excluded_hours })}</p>`
          : nothing}
        ${result.restless?.length
          ? html`<p class="small warn-text">${t("Auffällig unruhig – vielleicht Sonne, Heizkörper oder Fenster am Sensor: {rooms}. Ein geringeres Gewicht oder ein anderer Sensor hilft.", { rooms: result.restless.map(name).join(", ") })}</p>`
          : nothing}
        ${points.length
          ? html`<span class="field-label">${t("Letzte 7 Tage")}</span>
            <div class="legend small">
              <span class="legend-item"><span class="swatch" style="background: ${SERIES_COLORS[0]}"></span>${t("gemessen")}</span>
              <span class="legend-item"><span class="swatch" style="background: ${SERIES_COLORS[1]}"></span>${t("Modell (startet jeden Tag neu)")}</span>
            </div>
            ${lineChart(
              [
                { name: t("gemessen"), color: SERIES_COLORS[0], points: points.map((p) => ({ start: p.start, end: p.end, value: p.measured })) },
                { name: t("Modell"), color: SERIES_COLORS[1], dashed: true, points: points.map((p) => ({ start: p.start, end: p.end, value: p.model })) },
              ],
              {
                label: t("Raumtemperatur der letzten 7 Tage, gemessen und vom Modell erwartet"),
                formatY: (v) => `${decimal(v.toFixed(1))} °C`,
                yMin: Math.floor(Math.min(...points.map((p) => p.measured)) - 1),
                yMax: Math.ceil(Math.max(...points.map((p) => p.measured)) + 1),
              },
            )}`
          : nothing}
      </div>
    `;
  }

  static styles = css`
    :host { display: flex; flex-direction: column; gap: 12px; }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; }
    h1 { margin: 0; font-size: 20px; }
    .heading { display: flex; flex-direction: column; gap: 4px; }
    .heading p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    p { margin: 0; }
    .device { border: 1px solid var(--sf-border); border-radius: 10px; padding: 18px; display: flex; flex-direction: column; gap: 12px; }
    .device-title { font-weight: 600; font-size: 16px; display: block; }
    .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .field-label { font-weight: 500; font-size: 13px; }
    .input-unit { display: flex; align-items: center; gap: 8px; }
    .input-unit input, select {
      flex: 1; min-width: 0; min-height: 44px; padding: 0 12px; font: inherit; font-size: 15px;
      border: 1px solid var(--sf-border-strong); border-radius: 8px; background: var(--sf-surface); color: var(--sf-text);
    }
    .group { border: 1px solid var(--sf-border); border-radius: 8px; padding: 12px 14px; margin: 0; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
    .group legend { font-weight: 600; font-size: 14px; padding: 0 4px; }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .primary, .secondary { min-height: 44px; padding: 0 20px; border-radius: 8px; cursor: pointer; font-weight: 600; }
    .primary { border: 0; background: var(--sf-primary); color: var(--sf-on-primary); }
    .secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); font-weight: 500; }
    .warn-text { color: var(--sf-warn-text); }
    .room { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }
    .room .grow { flex: 1 1 260px; }
    .room .narrow { flex: 0 0 90px; }
    .learned { display: flex; flex-direction: column; gap: 10px; border-top: 1px solid var(--sf-border); padding-top: 12px; }
    .legend { display: flex; flex-wrap: wrap; gap: 16px; }
    .legend-item { display: inline-flex; align-items: center; gap: 6px; }
    .swatch { width: 12px; height: 12px; border-radius: 3px; flex: none; }
    .chart { width: 100%; height: auto; display: block; }
    .chart .grid { stroke: var(--sf-border); stroke-width: 1; }
    .chart .axis { fill: var(--sf-muted); font-size: 11px; }
    .chart .hit { fill: transparent; }
    .chart .hit:hover { fill: var(--sf-text); fill-opacity: 0.06; }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
    .tile { background: var(--sf-subtle); border-radius: 8px; padding: 10px 12px; }
    .tile .value { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    button:disabled { cursor: not-allowed; opacity: .6; }
  `;
}

customElements.define("sf-heat-pump", SunflowHeatPump);
