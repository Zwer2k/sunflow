import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { fromDisplay, toDisplay } from "../lib/format.js";
import { t } from "../lib/i18n.js";
import { isOpen, setOpen } from "../lib/collapse.js";
import { toggleSheet } from "../lib/display-toggles.js";

const DETECTION = [
  ["card", t("Karte an der Wallbox"), t("Die gescannte Karte bestimmt das Fahrzeug.")],
  ["manual", t("Auswahl in der Steuerung"), t("Gilt bis zum Abstecken.")],
  ["sensor", t("Sensor des Fahrzeugs"), t("„Angesteckt“ oder „zuhause“ aus der Integration des Autos.")],
  ["single", t("Einziges zugeordnetes Fahrzeug"), t("Ist nur eines zugeordnet, gilt immer dieses.")],
];
const STRATEGIES = { cost: t("Kostenoptimal"), pv_only: t("Nur PV-Überschuss"), fast: t("Schnellstmöglich") };
// Cards a go-e knows
const CARD_NUMBERS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
const DEFAULT_FLEX = { strategy: "cost", target: null, ready_by: null, uses_battery: true };

/** A profile to edit: its settings and cards copied, so an edit does not change what was loaded. */
const copyProfile = (c) => ({ ...c, flex: { ...c.flex }, cards: (c.cards ?? []).map((card) => ({ ...card, flex: { ...card.flex } })) });

/**
 * Which vehicle charges at which wallbox and how (settings area "Laden"): a charging
 * profile per vehicle and wallbox with card, target, ready-by time and strategy, plus
 * how the wallbox recognizes the connected vehicle.
 */
class SunflowCharging extends LitElement {
  static properties = {
    _data: { state: true },
    _profiles: { state: true },
    _dirty: { state: true },
    _detections: { state: true },
    _error: { state: true },
    _busy: { state: true },
  };

  constructor() {
    super();
    this._data = null;
    this._profiles = [];
    // Wallbox ids with unsaved changes; detection edits by wallbox id
    this._dirty = new Set();
    this._detections = {};
    this._error = null;
    this._busy = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
  }

  async _load() {
    try {
      this._data = await api.vehicles();
      this._profiles = this._data.charging.map(copyProfile);
      this._dirty = new Set();
      this._detections = {};
    } catch (err) {
      this._error = t("Ladeprofile nicht verfügbar: {error}", { error: err.message });
    }
  }

  render() {
    const data = this._data;
    return html`
      <div class="heading">
        <h1>${t("Laden")}</h1>
        <p class="muted help">${t("Je Wallbox: welche Fahrzeuge dort laden, mit welcher Karte, bis zu welchem Ladestand und wann sie fertig sein sollen – und wie die Wallbox das angesteckte Fahrzeug erkennt.")}</p>
      </div>
      ${this._error ? html`<div class="alert error">${this._error}</div>` : nothing}
      ${data === null ? html`<p class="muted">${t("Lade …")}</p>` : nothing}
      ${data && !data.wallboxes.length ? html`<p class="muted">${t("Keine Wallbox eingerichtet (Geräte).")}</p>` : nothing}
      ${data && data.wallboxes.length && !data.vehicles.length
        ? html`<p class="muted">${t("Noch keine Fahrzeuge – zuerst unter „Fahrzeuge“ anlegen. Ohne Fahrzeuge plant Sunflow die Wallbox mit ihren Kenndaten und den Vorgaben unter „Verbraucher“.")}</p>`
        : nothing}
      ${data?.vehicles.length ? data.wallboxes.map((w) => this._renderWallbox(w)) : nothing}
    `;
  }

  _renderWallbox(wallbox) {
    const vehicles = this._data.vehicles;
    const allowed = this._profiles.filter((p) => p.wallbox_id === wallbox.id);
    return html`
      <section class="device">
        <div class="device-title">${wallbox.name}</div>
        <div class="small muted">${allowed.length
          ? allowed.length > 1
            ? t("{count} Fahrzeuge dürfen hier laden", { count: allowed.length })
            : t("{count} Fahrzeug darf hier laden", { count: allowed.length })
          : t("Noch kein Fahrzeug – ein angestecktes Auto gilt als unbekannt, die Wallbox regelt dann selbst.")}</div>
        ${vehicles.map((v) => this._renderProfile(wallbox, v))}
        ${this._renderDetection(wallbox)}
        ${this._dirty.has(wallbox.id)
          ? html`<div class="actions">
              <button class="primary" ?disabled=${this._busy} @click=${() => this._save()}>${t("Speichern")}</button>
              <button class="secondary" ?disabled=${this._busy} @click=${() => this._load()}>${t("Verwerfen")}</button>
            </div>`
          : nothing}
      </section>
    `;
  }

  _renderProfile(wallbox, vehicle) {
    const profile = this._profiles.find((p) => p.wallbox_id === wallbox.id && p.vehicle_id === vehicle.id);
    const replace = (next) => {
      this._profiles = this._profiles.map((p) => (p === profile ? next : p));
      this._markDirty(wallbox.id);
    };
    const toggle = (on) => {
      this._profiles = on
        ? [...this._profiles, { vehicle_id: vehicle.id, wallbox_id: wallbox.id, cards: [], flex: { ...DEFAULT_FLEX } }]
        : this._profiles.filter((p) => p !== profile);
      this._markDirty(wallbox.id);
    };
    const setCard = (index, changes) =>
      replace({
        ...profile,
        cards: profile.cards.map((c, i) => (i === index ? { ...c, ...changes, flex: { ...c.flex, ...(changes.flex ?? {}) } } : c)),
      });
    return html`
      <div class="sub ${profile ? "" : "off"}">
        <label class="inline">
          <input type="checkbox" .checked=${Boolean(profile)} @change=${(e) => toggle(e.target.checked)}>
          <strong>${vehicle.name}</strong>
        </label>
        ${profile
          ? html`
              ${wallbox.cards ? html`<div class="small muted part">${t("Erkannt ohne Karte (Sensor, Auswahl, einziges Fahrzeug)")}</div>` : nothing}
              ${this._renderFlex(profile.flex, (flex) => replace({ ...profile, flex: { ...profile.flex, ...flex } }))}
              ${wallbox.cards
                ? html`
                    ${profile.cards.map((card, index) => this._renderCard(wallbox, profile, card, index, setCard))}
                    <div><button class="secondary small-button" ?disabled=${!this._freeNumber(wallbox)}
                      @click=${() => replace({ ...profile, cards: [...profile.cards, { number: this._freeNumber(wallbox), name: null, flex: { ...profile.flex } }] })}>
                      ${t("+ Karte hinzufügen")}</button></div>`
                : nothing}`
          : nothing}
      </div>
    `;
  }

  /** One card of the vehicle: number, name and how the car charges when it is scanned. */
  _renderCard(wallbox, profile, card, index, setCard) {
    const taken = new Set(
      this._profiles.filter((p) => p.wallbox_id === wallbox.id).flatMap((p) => p.cards.filter((c) => c !== card).map((c) => c.number)),
    );
    const remove = () => {
      this._profiles = this._profiles.map((p) => (p === profile ? { ...p, cards: p.cards.filter((c) => c !== card) } : p));
      this._markDirty(wallbox.id);
    };
    return html`<div class="card">
      <div class="card-head">
        <label class="field number">
          <span class="field-label">${t("Karte")}</span>
          <select @change=${(e) => setCard(index, { number: Number(e.target.value) })}>
            ${CARD_NUMBERS.map((n) => html`<option value=${n} ?selected=${card.number === n} ?disabled=${taken.has(n)}>${n}</option>`)}
          </select>
        </label>
        <label class="field name">
          <span class="field-label">${t("Name")}</span>
          <span class="input-unit"><input maxlength="40" placeholder=${t("Karte {number}", { number: card.number })} .value=${card.name ?? ""}
            @input=${(e) => setCard(index, { name: e.target.value || null })}></span>
        </label>
        <button class="secondary remove" title=${t("Karte entfernen")} aria-label=${t("Karte entfernen")} @click=${remove}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"></path></svg></button>
      </div>
      ${this._renderFlex(card.flex, (flex) => setCard(index, { flex }))}
    </div>`;
  }

  /** Target, ready-by time, strategy and battery use: of a vehicle at a wallbox, or of one of its cards. */
  _renderFlex(flex, update) {
    return html`<div class="fields">
      <label class="field">
        <span class="field-label">${t("Ziel-Ladestand")}</span>
        <span class="input-unit"><input inputmode="decimal" placeholder="100"
          .value=${flex.target === null || flex.target === undefined ? "" : toDisplay(flex.target, "%")}
          @input=${(e) => update({ target: e.target.value === "" ? null : fromDisplay(e.target.value, "%") })}>
          <span class="muted">%</span></span>
      </label>
      <label class="field">
        <span class="field-label">${t("Fertig bis (täglich)")}</span>
        <span class="input-unit"><input type="time" .value=${flex.ready_by ?? ""}
          @change=${(e) => update({ ready_by: e.target.value || null })}></span>
      </label>
      <label class="field">
        <span class="field-label">${t("Laden")}</span>
        <select @change=${(e) => update({ strategy: e.target.value })}>
          ${Object.entries(STRATEGIES).map(([value, label]) => html`<option value=${value} ?selected=${flex.strategy === value}>${label}</option>`)}
        </select>
      </label>
      <label class="field">
        <span class="field-label">${t("Hausspeicher")}</span>
        <span class="inline"><input type="checkbox" .checked=${flex.uses_battery !== false}
          @change=${(e) => update({ uses_battery: e.target.checked })}>${t("darf ins Auto entladen")}</span>
      </label>
    </div>`;
  }

  /** The lowest card number nobody uses at this wallbox yet; null when all are taken. */
  _freeNumber(wallbox) {
    const taken = new Set(this._profiles.filter((p) => p.wallbox_id === wallbox.id).flatMap((p) => p.cards.map((c) => c.number)));
    return CARD_NUMBERS.find((n) => !taken.has(n)) ?? null;
  }

  _renderDetection(wallbox) {
    const d = this._detections[wallbox.id] ?? wallbox.detection;
    const set = (field, value) => {
      this._detections = { ...this._detections, [wallbox.id]: { ...d, [field]: value } };
      this._markDirty(wallbox.id);
    };
    const open = isOpen(`detection:${wallbox.id}`);
    const methods = DETECTION.filter(([key]) => d[key] && (key !== "card" || wallbox.cards)).map(([, label]) => label);
    const toggle = () => {
      setOpen(`detection:${wallbox.id}`, !open);
      this.requestUpdate();
    };
    return html`
      <div class="detection">
        <button class="device-toggle" aria-expanded=${open} @click=${toggle}>
          <span class="caret" aria-hidden="true">${open ? "▾" : "▸"}</span>
          <span><strong>${t("Erkennung des Fahrzeugs")}</strong>
            ${open ? nothing : html`<span class="small muted block">${methods.join(" · ") || t("aus")}${wallbox.cards && d.authorize ? ` · ${t("gibt ohne Karte frei")}` : ""}</span>`}</span>
        </button>
        ${open
          ? html`
              <div class="checks">
                ${DETECTION.filter(([key]) => key !== "card" || wallbox.cards).map(([key, label, hint]) => html`
                  <label class="inline" title=${hint}>
                    <input type="checkbox" .checked=${d[key]} @change=${(e) => set(key, e.target.checked)}>${label}</label>`)}
              </div>
              ${wallbox.cards
                ? html`<label class="inline">
                    <input type="checkbox" .checked=${d.authorize} @change=${(e) => set("authorize", e.target.checked)}>
                    ${t("Ladevorgang ohne Karte freigeben, wenn das Fahrzeug sicher erkannt ist (Sensor oder Auswahl)")}</label>`
                : nothing}
              <span class="small muted help">${t("Unbekanntes Fahrzeug (fremde Karte, nichts erkannt): Die Wallbox regelt selbst.")}</span>`
          : nothing}
      </div>
    `;
  }

  _markDirty(wallboxId) {
    this._dirty = new Set([...this._dirty, wallboxId]);
  }

  /** Charging profiles and recognition together, so one button saves what was changed. */
  async _save() {
    if (this._profiles.some((p) => p.flex.target === undefined || p.cards.some((c) => c.flex.target === undefined))) {
      this._error = t("Bitte einen gültigen Ziel-Ladestand eingeben.");
      return;
    }
    this._busy = true;
    try {
      for (const [wallboxId, detection] of Object.entries(this._detections)) {
        await api.setVehicleDetection(wallboxId, detection);
      }
      this._data = await api.setCharging(this._profiles);
      this._profiles = this._data.charging.map(copyProfile);
      this._dirty = new Set();
      this._detections = {};
      this._error = null;
    } catch (err) {
      this._error = t("Nicht gespeichert: {error}", { error: err.message });
    } finally {
      this._busy = false;
    }
  }

  /** Called by the settings when leaving the area: unsaved changes are kept. */
  async saveIfChanged() {
    if (!this._dirty.size) return true;
    await this._save();
    return !this._dirty.size;
  }

  static styles = [toggleSheet, css`
    :host { display: flex; flex-direction: column; gap: 12px; }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; }
    h1 { margin: 0; font-size: 20px; }
    .heading { display: flex; flex-direction: column; gap: 4px; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .block { display: block; }
    .device { border: 1px solid var(--sf-border); border-radius: 10px; padding: 18px; display: flex; flex-direction: column; gap: 12px; }
    .device-title { font-weight: 600; font-size: 16px; }
    .sub { display: flex; flex-direction: column; gap: 10px; border: 1px solid var(--sf-border); border-radius: 8px; padding: 12px 14px; }
    .sub.off { padding: 8px 14px; }
    .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .field-label { font-weight: 500; font-size: 13px; }
    .input-unit { display: flex; align-items: center; gap: 8px; }
    .input-unit input, select {
      flex: 1; min-width: 0; min-height: 44px; padding: 0 12px; font: inherit; font-size: 15px;
      border: 1px solid var(--sf-border-strong); border-radius: 8px; background: var(--sf-surface); color: var(--sf-text);
    }
    .part { font-weight: 500; }
    /* A card of the vehicle: its own box inside the vehicle's */
    .card { display: flex; flex-direction: column; gap: 10px; border-top: 1px dashed var(--sf-border); padding-top: 12px; }
    /* Number, name and remove in one line, also on a phone */
    .card-head { display: flex; align-items: flex-end; gap: 10px; }
    .card-head .number { flex: 0 0 76px; }
    .card-head .name { flex: 1 1 auto; min-width: 0; }
    .secondary.remove { flex: none; width: 44px; padding: 0; display: flex; align-items: center; justify-content: center; color: var(--sf-muted); }
    .remove svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
    .small-button { min-height: 40px; padding: 0 14px; font-size: 14px; }
    .checks { display: flex; flex-wrap: wrap; gap: 6px 18px; }
    .inline { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 14px; }
    .inline input { width: 18px; height: 18px; margin: 0; }
    .detection { display: flex; flex-direction: column; gap: 8px; border-top: 1px solid var(--sf-border); padding-top: 12px; }
    .device-toggle { display: flex; gap: 8px; align-items: flex-start; border: 0; background: none; padding: 0; cursor: pointer; text-align: left; font-size: 14px; }
    .caret { width: 14px; color: var(--sf-muted); line-height: 20px; }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; }
    .primary, .secondary { min-height: 44px; padding: 0 20px; border-radius: 8px; cursor: pointer; font-weight: 600; }
    .primary { border: 0; background: var(--sf-primary); color: var(--sf-on-primary); }
    .secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); font-weight: 500; }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    button:disabled { cursor: not-allowed; opacity: .6; }
  `];
}

customElements.define("sf-charging", SunflowCharging);
