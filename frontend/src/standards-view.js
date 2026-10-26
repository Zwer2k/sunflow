import { LitElement, html, css, nothing } from "../vendor/lit-all.min.js";
import { api } from "./api.js";
import { fromDisplay, toDisplay } from "./format.js";

const STRATEGIES = {
  balanced: { label: "Ausgewogen", hint: "Geringste Stromkosten über den Planungszeitraum." },
  pv_absorption: {
    label: "Möglichst viel PV aufnehmen",
    hint: "Hält Platz für Solarstrom frei und lädt vor sonnigen Tagen nicht aus dem Netz – sinnvoll bei Einspeisebegrenzung.",
  },
  backup: { label: "Notstrom sicherstellen", hint: "Hält eine Reserve für Stromausfälle (EPS) im Speicher." },
  autarky: {
    label: "Möglichst autark",
    hint: "So wenig Netzbezug wie möglich: kein Netzladen, der Speicher deckt jeden Bedarf sofort. Kann mit Zeittarifen etwas mehr kosten.",
  },
};

/** Default planning settings ("Standards" step of the setup). */
class SunflowStandards extends LitElement {
  static properties = {
    _settings: { state: true },
    _error: { state: true },
    _wear: { state: true },
    _wearMode: { state: true },
    _wearCalc: { state: true },
    _batteryKwh: { state: true },
    _historyKwh: { state: true },
    _weather: { state: true },
    _reserve: { state: true },
    _maxSoc: { state: true },
    _fullEvery: { state: true },
    _gridAfter: { state: true },
    _daily: { state: true },
    _largeLoad: { state: true },
    _saving: { state: true },
  };

  constructor() {
    super();
    this._settings = null;
    this._error = null;
    this._saving = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
  }

  async _load() {
    try {
      this._settings = (await api.config()).optimizer;
      this._error = null;
      // Capacity as default for the wear calculation (derived from the battery modules)
      api.weatherEntities().then((list) => (this._weather = list)).catch(() => (this._weather = []));
      api.latestPlan().then((plan) => {
        this._batteryKwh = plan.battery ? plan.battery.capacity_wh / 1000 : null;
        this._historyKwh = plan.load_history_daily_kwh;
      }).catch(() => {});
    } catch (err) {
      this._error = `Einstellungen konnten nicht geladen werden: ${err.message}`;
    }
  }

  render() {
    return html`
      <div class="heading">
        <div>
          <h1>Standards</h1>
          <p class="muted">Voreinstellungen für die Planung. Vorübergehende Änderungen machst du im Tab „Steuerung“.</p>
        </div>
      </div>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${this._settings ? this._renderSettings(this._settings) : html`<p class="muted">Lade …</p>`}
    `;
  }

  async _saveSettings(changes) {
    this._saving = true;
    try {
      await api.setOptimizer({ ...this._settings, ...changes });
      // Only the saved fields drop their edit; numbers typed elsewhere stay
      if ("wear_ct_per_kwh" in changes) this._wear = undefined;
      if ("wear_calculation" in changes) {
        this._wearMode = undefined;
        this._wearCalc = undefined;
      }
      if ("backup_reserve_pct" in changes) this._reserve = undefined;
      if ("max_soc_pct" in changes) this._maxSoc = undefined;
      if ("full_charge_every_days" in changes) this._fullEvery = undefined;
      if ("full_charge_grid_after_days" in changes) this._gridAfter = undefined;
      if ("daily_consumption_kwh" in changes) this._daily = undefined;
      if ("large_load_threshold_w" in changes) this._largeLoad = undefined;
      await this._load();
      return true;
    } catch (err) {
      this._error = `Einstellungen konnten nicht gespeichert werden: ${err.message}`;
      // Radios and checkboxes show the stored value again
      this.requestUpdate();
      return false;
    } finally {
      this._saving = false;
    }
  }

  /** Save edited numbers before the wizard moves on; false when they are invalid. */
  async saveIfChanged() {
    const changed = [this._wear, this._wearMode, this._wearCalc, this._reserve, this._maxSoc, this._fullEvery, this._gridAfter, this._daily, this._largeLoad].some(
      (v) => v !== undefined,
    );
    return !changed || this._saveNumbers(this._settings);
  }


  _saveNumbers(settings) {
    const changes = {};
    const mode = this._wearMode ?? (settings.wear_calculation ? "calc" : "direct");
    if (mode === "calc" && (this._wearCalc !== undefined || this._wearMode !== undefined)) {
      const calc = this._parseWearCalc(settings);
      if (!calc) {
        this._error = "Bitte Preis, Zyklen und Kapazität für den Verschleiß angeben.";
        return false;
      }
      changes.wear_calculation = calc;
    } else if (mode === "direct" && this._wearMode !== undefined) {
      changes.wear_calculation = null;
    }
    if (mode === "direct" && this._wear !== undefined) {
      const wear = fromDisplay(this._wear, "ct");
      if (wear === undefined || wear === null || wear < 0) {
        this._error = "Bitte gültige Verschleißkosten eingeben.";
        return false;
      }
      changes.wear_ct_per_kwh = wear;
    }
    if (this._daily !== undefined) {
      const daily = fromDisplay(this._daily, "kWh");
      if (daily === undefined || (daily !== null && daily <= 0)) {
        this._error = "Bitte einen gültigen Tagesverbrauch in kWh eingeben oder das Feld leeren.";
        return false;
      }
      changes.daily_consumption_kwh = daily;
    }
    if (this._largeLoad !== undefined) {
      const kw = fromDisplay(this._largeLoad, "kW");
      if (kw === undefined || (kw !== null && kw <= 0)) {
        this._error = "Bitte eine gültige Leistung in kW eingeben oder das Feld leeren.";
        return false;
      }
      changes.large_load_threshold_w = kw === null ? null : kw * 1000;
    }
    if (this._maxSoc !== undefined) {
      const limit = fromDisplay(this._maxSoc, "%");
      if (limit === undefined || limit === null || limit < 50 || limit > 100) {
        this._error = "Bitte eine Ladegrenze zwischen 50 und 100 % eingeben.";
        return false;
      }
      changes.max_soc_pct = limit;
    }
    if (this._fullEvery !== undefined) {
      const days = fromDisplay(this._fullEvery, "");
      if (days === undefined || (days !== null && (!Number.isInteger(days) || days < 1 || days > 90))) {
        this._error = "Bitte ganze Tage zwischen 1 und 90 eingeben oder das Feld leeren.";
        return false;
      }
      changes.full_charge_every_days = days;
    }
    if (this._gridAfter !== undefined) {
      const days = fromDisplay(this._gridAfter, "");
      if (days === undefined || (days !== null && (!Number.isInteger(days) || days < 1 || days > 120))) {
        this._error = "Bitte ganze Tage zwischen 1 und 120 eingeben oder das Feld leeren.";
        return false;
      }
      changes.full_charge_grid_after_days = days;
    }
    if (this._reserve !== undefined) {
      const reserve = fromDisplay(this._reserve, "%");
      if (reserve === undefined || reserve === null || reserve < 0 || reserve > 100) {
        this._error = "Bitte eine Reserve zwischen 0 und 100 % eingeben.";
        return false;
      }
      changes.backup_reserve_pct = reserve;
    }
    return this._saveSettings({ ...settings, ...changes });
  }


  _wearInputs(settings) {
    const stored = settings.wear_calculation ?? {};
    return {
      price: toDisplay(stored.price_eur, "ct"),
      cycles: toDisplay(stored.cycles, "ct"),
      capacity: toDisplay(stored.capacity_kwh ?? this._batteryKwh, "ct"),
      dod: toDisplay(stored.depth_of_discharge_pct ?? 90, "ct"),
      eol: toDisplay(stored.end_of_life_pct ?? 70, "ct"),
      ...(this._wearCalc ?? {}),
    };
  }

  _parseWearCalc(settings) {
    const inputs = this._wearInputs(settings);
    const number = (text) => fromDisplay(text ?? "", "ct");
    const calc = {
      price_eur: number(inputs.price),
      cycles: number(inputs.cycles),
      capacity_kwh: number(inputs.capacity),
      depth_of_discharge_pct: number(inputs.dod),
      end_of_life_pct: number(inputs.eol),
    };
    const valid = Object.values(calc).every((v) => typeof v === "number" && v > 0)
      && calc.depth_of_discharge_pct <= 100 && calc.end_of_life_pct <= 100;
    return valid ? calc : null;
  }

  _renderWear(settings) {
    const mode = this._wearMode ?? (settings.wear_calculation ? "calc" : "direct");
    const inputs = this._wearInputs(settings);
    const set = (field, value) => (this._wearCalc = { ...(this._wearCalc ?? {}), [field]: value });
    const calc = mode === "calc" ? this._parseWearCalc(settings) : null;
    // Same formula as the backend: price / (cycles x mean capacity x depth of discharge)
    const result = calc
      ? (calc.price_eur * 100) / (calc.cycles * calc.capacity_kwh * ((1 + calc.end_of_life_pct / 100) / 2) * (calc.depth_of_discharge_pct / 100))
      : null;
    const field = (label, key, unit, hint = null) => html`
      <label class="field">
        <span class="field-label">${label}</span>
        <span class="input-unit"><input inputmode="decimal" .value=${inputs[key] ?? ""} @input=${(e) => set(key, e.target.value)}>
          <span class="muted">${unit}</span></span>
        ${hint ? html`<span class="small muted">${hint}</span>` : nothing}
      </label>`;
    return html`
      <div class="field wide">
        <span class="field-label">Verschleißkosten Speicher</span>
        <div class="segmented" role="group" aria-label="Verschleißkosten angeben">
          ${[["direct", "Direkt in ct/kWh"], ["calc", "Aus Preis und Zyklen"]].map(
            ([value, label]) => html`<button class=${mode === value ? "on" : ""} aria-pressed=${mode === value}
              @click=${() => (this._wearMode = value)}>${label}</button>`,
          )}
        </div>
        ${mode === "direct"
          ? html`<span class="input-unit">
                <input inputmode="decimal" aria-label="Verschleißkosten in ct/kWh"
                  .value=${this._wear ?? toDisplay(settings.wear_ct_per_kwh, "ct")} @input=${(e) => (this._wear = e.target.value)}>
                <span class="muted">ct/kWh</span>
              </span>`
          : html`<div class="fields">
                ${field("Anschaffungspreis", "price", "€", "Speicher ohne Wechselrichter, inkl. Montage")}
                ${field("Zyklen laut Hersteller", "cycles", "Zyklen")}
                ${field("Kapazität", "capacity", "kWh")}
                ${field("bei Entladetiefe", "dod", "%")}
                ${field("Restkapazität danach", "eol", "%", "z. B. 70 % – die Kapazität sinkt bis dahin")}
              </div>
              <span class="small">${result !== null
                ? html`ergibt <strong>${toDisplay(Math.round(result * 100) / 100, "ct")} ct/kWh</strong> je entladener kWh`
                : html`<span class="muted">Preis, Zyklen und Kapazität eingeben</span>`}</span>`}
        <span class="small muted">Je entladener kWh – verhindert Zyklen für Kleinstbeträge.</span>
      </div>
    `;
  }

  _renderSettings(settings) {
    return html`
      <section class="settings">
        
        <fieldset class="strategy">
          <legend class="field-label">Strategie des Hausspeichers (Standard)</legend>
          ${Object.entries(STRATEGIES).map(
            ([value, { label, hint }]) => html`
              <label class="option ${settings.strategy === value ? "selected" : ""}">
                <input type="radio" name="strategy" .checked=${settings.strategy === value} ?disabled=${this._saving}
                  @change=${() => this._saveSettings({ strategy: value })}>
                <span><strong>${label}</strong><br><span class="small muted">${hint}</span></span>
              </label>
            `,
          )}
        </fieldset>
        <div class="fields">
          ${settings.strategy === "backup"
            ? html`<label class="field">
                <span class="field-label">Notstrom-Reserve</span>
                <span class="input-unit">
                  <input inputmode="decimal" .value=${this._reserve ?? toDisplay(settings.backup_reserve_pct, "%")}
                    @input=${(e) => (this._reserve = e.target.value)}>
                  <span class="muted">%</span>
                </span>
                <span class="small muted">Wird nie unterschritten – zusätzlich zum Mindest-Ladestand.</span>
              </label>`
            : nothing}
          <label class="field">
            <span class="field-label">Ladegrenze im Alltag</span>
            <span class="input-unit">
              <input inputmode="decimal" .value=${this._maxSoc ?? toDisplay(settings.max_soc_pct, "%")}
                @input=${(e) => (this._maxSoc = e.target.value)}>
              <span class="muted">%</span>
            </span>
            <span class="small muted">Schont die Zellen: höher lädt Sunflow nur für eine fällige Vollladung. 100 % = keine Grenze.</span>
          </label>
          <label class="field">
            <span class="field-label">Voll laden alle</span>
            <span class="input-unit">
              <input inputmode="numeric" placeholder="z. B. 14" .value=${this._fullEvery ?? toDisplay(settings.full_charge_every_days, "")}
                @input=${(e) => (this._fullEvery = e.target.value)}>
              <span class="muted">Tage</span>
            </span>
            <span class="small muted">Zum Ausgleichen und Kalibrieren der Zellen, bevorzugt mit PV. Leer = nie gezielt.</span>
          </label>
          <label class="field">
            <span class="field-label">Spätestens nach … aus dem Netz</span>
            <span class="input-unit">
              <input inputmode="numeric" placeholder="z. B. 21" .value=${this._gridAfter ?? toDisplay(settings.full_charge_grid_after_days, "")}
                @input=${(e) => (this._gridAfter = e.target.value)}>
              <span class="muted">Tage</span>
            </span>
            <span class="small muted">War der Speicher so lange nicht voll (z. B. im Winter), lädt Sunflow ihn zur günstigsten Zeit aus dem Netz voll – auch wenn Netzladen sonst aus ist. Leer = nie.</span>
          </label>
          <label class="field">
            <span class="field-label">Hausverbrauch pro Tag (Startwert)</span>
            <span class="input-unit">
              <input inputmode="decimal" placeholder="z. B. 14"
                .value=${this._daily ?? toDisplay(settings.daily_consumption_kwh, "kWh")}
                @input=${(e) => (this._daily = e.target.value)}>
              <span class="muted">kWh/Tag</span>
            </span>
            <span class="small muted">Gilt nur, solange weniger als 7 Tage Verbrauchsverlauf vorliegen; danach plant Sunflow mit
              den gemessenen Werten (ab 14 Tagen mit dem gelernten Modell). Ohne große Verbraucher wie Wallbox und Heizstab.</span>
            ${this._historyKwh
              ? html`<span class="small">Aus dem Verlauf: <strong>${toDisplay(Math.round(this._historyKwh * 10) / 10, "ct")} kWh/Tag</strong>
                  · <button class="link" @click=${() => (this._daily = toDisplay(Math.round(this._historyKwh * 10) / 10, "ct"))}>übernehmen</button></span>`
              : nothing}
          </label>
          <label class="field">
            <span class="field-label">Großverbraucher ausklammern ab</span>
            <span class="input-unit">
              <input inputmode="decimal"
                .value=${this._largeLoad ?? toDisplay(settings.large_load_threshold_w === null ? null : settings.large_load_threshold_w / 1000, "kW")}
                @input=${(e) => (this._largeLoad = e.target.value)}>
              <span class="muted">kW</span>
            </span>
            <span class="small muted">Stunden darüber (z. B. Wallbox) zählen nicht zur Lastprognose. Leer = nichts ausklammern.</span>
          </label>
          ${this._renderWear(settings)}
          <label class="field">
            <span class="field-label">Laden aus dem Netz</span>
            <span class="inline">
              <input type="checkbox" .checked=${settings.allow_grid_charge} ?disabled=${this._saving}
                @change=${(e) => this._saveSettings({ allow_grid_charge: e.target.checked })}>
              erlaubt, wenn es sich rechnet
            </span>
          </label>
          <label class="field">
            <span class="field-label">PV-Prognose</span>
            <span class="inline">
              <input type="checkbox" .checked=${settings.pv_learning} ?disabled=${this._saving}
                @change=${(e) => this._saveSettings({ pv_learning: e.target.checked })}>
              an die Anlage anpassen (aus Messwerten lernen)
            </span>
            <span class="small muted">Vergleicht Prognose und Messung je Stunde und Bewölkung, Details unter „Analyse“.</span>
          </label>
          <div class="field">
            <span class="field-label">Unsichere PV-Prognose</span>
            <select aria-label="Unsichere PV-Prognose" ?disabled=${this._saving}
              @change=${(e) => this._saveSettings({ pv_risk: e.target.value })}>
              ${[
                ["expected", "Erwartungswert (optimistischer)"],
                ["balanced", "Ausgewogen"],
                ["cautious", "Vorsichtig"],
              ].map(([value, label]) => html`<option value=${value} ?selected=${settings.pv_risk === value}>${label}</option>`)}
            </select>
            <span class="small muted">Vorsichtig plant mehr mit dem schlechten Fall der Prognose (Solcast p10): lieber nachts
              günstig nachladen, als bei weniger Sonne teuer nachzukaufen.</span>
          </div>
          <div class="field">
            <span class="field-label">Wetter für die Bewölkung</span>
            <select aria-label="Wetter-Entität" ?disabled=${this._saving}
              @change=${(e) => this._saveSettings({ weather_entity: e.target.value || null })}>
              <option value="" ?selected=${!settings.weather_entity}>Automatisch (erste Wetter-Entität)</option>
              ${(this._weather ?? []).map(
                (w) => html`<option value=${w.entity_id} ?selected=${settings.weather_entity === w.entity_id}>
                  ${w.name}${w.provider ? ` (${w.provider})` : ""}${w.cloud_coverage ? "" : " – ohne Bewölkungsangabe"}</option>`,
              )}
            </select>
            <span class="small muted">Liefert die Entität keine Bewölkung, lernt Sunflow nur je Stunde.</span>
          </div>
        </div>
        ${[this._wear, this._wearMode, this._wearCalc, this._reserve, this._maxSoc, this._fullEvery, this._gridAfter, this._daily, this._largeLoad].some((v) => v !== undefined)
          ? html`<div class="actions">
              <button class="ok" ?disabled=${this._saving} @click=${() => this._saveNumbers(settings)}>
                Speichern und neu planen
              </button>
            </div>`
          : html`<p class="small muted">Strategie und Netzladen gelten sofort. Zahlenwerte werden nach einer Änderung mit „Speichern und neu planen“ übernommen.</p>`}
      </section>
    `;
  }

  static styles = css`
    :host { display: flex; flex-direction: column; gap: 16px; }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; }
    h1 { margin: 0; font-size: 20px; }
    h2 { margin: 0 0 8px; font-size: 16px; }
    p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .heading { display: flex; flex-wrap: wrap; gap: 12px; justify-content: space-between; align-items: flex-start; }
    .alert { padding: 10px 14px; border-radius: 8px; font-size: 14px; }
    .alert.warn { background: var(--sf-warn-bg); color: var(--sf-warn-text); }
    .alert.error { background: var(--sf-error-bg); color: var(--sf-error-text); }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
    .tile { background: var(--sf-subtle); border-radius: 8px; padding: 12px 14px; }
    .tile.now { background: var(--sf-primary-bg); }
    .tile .value { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .timeline { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
    .seg {
      display: grid; grid-template-columns: minmax(150px, auto) 1fr auto; gap: 4px 12px; align-items: baseline;
      padding: 8px 12px; border-radius: 8px; background: var(--sf-subtle); border-left: 4px solid var(--sf-border);
    }
    .seg.hold { border-left-color: var(--sf-muted); }
    .seg.auto { border-left-color: var(--sf-ok); }
    .seg.force_charge { border-left-color: var(--sf-primary); }
    .seg.force_discharge { border-left-color: var(--sf-warn-text); }
    .seg .when { font-variant-numeric: tabular-nums; font-size: 14px; }
    .seg .what { font-weight: 600; }
    @media (max-width: 560px) { .seg { grid-template-columns: 1fr; } }
    .chart { width: 100%; height: auto; display: block; }
    .chart .grid { stroke: var(--sf-border); stroke-width: 1; }
    .chart .axis { fill: var(--sf-muted); font-size: 11px; }
    .chart .hit { fill: transparent; }
    .chart .hit:hover { fill: var(--sf-text); fill-opacity: 0.06; }
    .legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; margin-bottom: 6px; }
    .legend-item { display: inline-flex; align-items: center; gap: 6px; }
    .swatch { width: 12px; height: 12px; border-radius: 3px; }
    .warn-text { color: var(--sf-error-text); }
    .flex-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }
    .flex-card { background: var(--sf-subtle); border-radius: 8px; padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
    .flex-head { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
    .windows { margin: 4px 0 0; padding-left: 18px; }
    h3 { margin: 0 0 8px; font-size: 15px; }
    .tag {
      font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; margin-left: 6px;
      background: var(--sf-neutral-bg); color: var(--sf-neutral-text); vertical-align: middle;
    }
    .section-head { display: flex; flex-wrap: wrap; gap: 8px; justify-content: space-between; align-items: center; }
    .range { display: flex; gap: 4px; background: var(--sf-subtle); padding: 4px; border-radius: 10px; }
    .range button {
      min-height: 36px; padding: 0 12px; border: 0; border-radius: 8px; background: transparent; cursor: pointer;
      font-size: 13px; color: var(--sf-muted);
    }
    .range button.active { background: var(--sf-surface); color: var(--sf-text); font-weight: 600; }
    .line { display: inline-block; width: 22px; height: 0; border-top: 2px solid var(--sf-text); }
    .line.dashed { border-top-style: dashed; }
    .chart .now { stroke: var(--sf-text); stroke-width: 1; stroke-dasharray: 2 3; }
    .table-wrap { overflow-x: auto; border: 1px solid var(--sf-border); border-radius: 8px; margin-top: 10px; }
    table { border-collapse: collapse; width: 100%; min-width: 520px; font-size: 13px; }
    th { text-align: left; font-weight: 600; background: var(--sf-subtle); padding: 8px 10px; }
    td { padding: 6px 10px; border-top: 1px solid var(--sf-border); }
    .settings { border-top: 1px solid var(--sf-border); padding-top: 16px; display: flex; flex-direction: column; gap: 16px; }
    .strategy { border: 0; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; font-size: 14px; }
    .strategy legend { margin-bottom: 8px; padding: 0; }
    .option {
      display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; border: 1px solid var(--sf-border);
      border-radius: 8px; cursor: pointer;
    }
    .option.selected { border-color: var(--sf-primary); background: var(--sf-primary-bg); }
    .option input { margin-top: 3px; width: 18px; height: 18px; }
    .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
    .link { border: 0; background: none; padding: 0; color: var(--sf-primary-text); text-decoration: underline; cursor: pointer; font: inherit; }
    select {
      min-height: 40px; padding: 0 10px; font: inherit; font-size: 14px; border: 1px solid var(--sf-border-strong);
      border-radius: 8px; background: var(--sf-surface); color: var(--sf-text);
    }
    .field.wide { grid-column: 1 / -1; display: flex; flex-direction: column; gap: 8px; }
    .segmented { display: flex; flex-wrap: wrap; gap: 4px; background: var(--sf-subtle); padding: 4px; border-radius: 10px; align-self: flex-start; }
    .segmented button {
      min-height: 36px; padding: 0 12px; border: 0; border-radius: 8px; background: transparent;
      cursor: pointer; font: inherit; font-size: 14px; color: var(--sf-muted);
    }
    .segmented button.on { background: var(--sf-surface); color: var(--sf-text); font-weight: 600; box-shadow: 0 1px 2px rgb(0 0 0 / 0.1); }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .field-label { font-weight: 500; }
    .input-unit { display: flex; align-items: center; gap: 8px; }
    .input-unit input {
      flex: 1; min-width: 0; min-height: 44px; padding: 0 12px; font: inherit; font-size: 15px;
      border: 1px solid var(--sf-border-strong); border-radius: 8px; background: var(--sf-surface); color: var(--sf-text);
    }
    .inline { display: inline-flex; align-items: center; gap: 8px; min-height: 44px; font-size: 14px; }
    .inline input { width: 18px; height: 18px; }
    .actions { display: flex; gap: 10px; margin-top: 12px; }
    .secondary, .ok { min-height: 44px; padding: 0 18px; border-radius: 8px; cursor: pointer; font-weight: 600; }
    .secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); font-weight: 500; }
    .button-link {
      display: inline-flex; align-items: center; min-height: 44px; padding: 0 18px; border-radius: 8px;
      text-decoration: none; color: var(--sf-text); cursor: pointer; font-size: 14px;
    }
    .ok { border: 0; background: var(--sf-ok); color: var(--sf-on-ok); }
    button:disabled { cursor: not-allowed; opacity: .6; }
    button:focus-visible { outline: 2px solid var(--sf-primary); outline-offset: 2px; }
  `;
}

customElements.define("sf-standards", SunflowStandards);
