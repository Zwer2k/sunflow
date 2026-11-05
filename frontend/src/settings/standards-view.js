import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import "../components/picker.js";
import { api } from "../lib/api.js";
import { fromDisplay, toDisplay } from "../lib/format.js";
import { STRATEGIES, strategyDetails } from "../lib/strategies.js";
import { t } from "../lib/i18n.js";
import "./learning-sources.js";


/** Default planning settings (settings area "Planung"). */
class SunflowStandards extends LitElement {
  static properties = {
    _settings: { state: true },
    _error: { state: true },
    _historyKwh: { state: true },
    _weather: { state: true },
    _reserve: { state: true },
    _maxSoc: { state: true },
    _fullEvery: { state: true },
    _gridAfter: { state: true },
    _exportLimit: { state: true },
    _importLimit: { state: true },
    _daily: { state: true },
    _largeLoad: { state: true },
    _agingThreshold: { state: true },
    _agingCost: { state: true },
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
      api.weatherEntities().then((list) => (this._weather = list)).catch(() => (this._weather = []));
      api.latestPlan().then((plan) => (this._historyKwh = plan.load_history_daily_kwh)).catch(() => {});
    } catch (err) {
      this._error = t("Einstellungen konnten nicht geladen werden: {error}", { error: err.message });
    }
  }

  render() {
    return html`
      <div class="heading">
        <div>
          <h1>${t("Planung")}</h1>
          <p class="muted">${t("Wie Sunflow den Speicher plant: Strategie, Schonung, netzdienliches Verhalten. Vorübergehende Änderungen machst du im Tab „Steuerung“.")}</p>
        </div>
      </div>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${this._settings ? this._renderSettings(this._settings) : html`<p class="muted">${t("Lade …")}</p>`}
      ${this._settings ? html`<sunflow-learning-sources></sunflow-learning-sources>` : nothing}
    `;
  }

  async _saveSettings(changes) {
    this._saving = true;
    try {
      await api.setOptimizer({ ...this._settings, ...changes });
      // Only the saved fields drop their edit; numbers typed elsewhere stay
      if ("backup_reserve_pct" in changes) this._reserve = undefined;
      if ("max_soc_pct" in changes) this._maxSoc = undefined;
      if ("full_charge_every_days" in changes) this._fullEvery = undefined;
      if ("full_charge_grid_after_days" in changes) this._gridAfter = undefined;
      if ("export_limit_w" in changes) this._exportLimit = undefined;
      if ("import_limit_w" in changes) this._importLimit = undefined;
      if ("daily_consumption_kwh" in changes) this._daily = undefined;
      if ("large_load_threshold_w" in changes) this._largeLoad = undefined;
      if ("high_soc_threshold_pct" in changes) this._agingThreshold = undefined;
      if ("high_soc_ct_per_kwh_h" in changes) this._agingCost = undefined;
      await this._load();
      return true;
    } catch (err) {
      this._error = t("Einstellungen konnten nicht gespeichert werden: {error}", { error: err.message });
      // Radios and checkboxes show the stored value again
      this.requestUpdate();
      return false;
    } finally {
      this._saving = false;
    }
  }

  /** Save edited numbers before the wizard moves on; false when they are invalid. */
  async saveIfChanged() {
    const changed = [this._reserve, this._maxSoc, this._fullEvery, this._gridAfter, this._exportLimit, this._importLimit, this._daily, this._largeLoad, this._agingThreshold, this._agingCost].some(
      (v) => v !== undefined,
    );
    return !changed || this._saveNumbers(this._settings);
  }


  _saveNumbers(settings) {
    const changes = {};
    if (this._daily !== undefined) {
      const daily = fromDisplay(this._daily, "kWh");
      if (daily === undefined || (daily !== null && daily <= 0)) {
        this._error = t("Bitte einen gültigen Tagesverbrauch in kWh eingeben oder das Feld leeren.");
        return false;
      }
      changes.daily_consumption_kwh = daily;
    }
    if (this._largeLoad !== undefined) {
      const kw = fromDisplay(this._largeLoad, "kW");
      if (kw === undefined || (kw !== null && kw <= 0)) {
        this._error = t("Bitte eine gültige Leistung in kW eingeben oder das Feld leeren.");
        return false;
      }
      changes.large_load_threshold_w = kw === null ? null : kw * 1000;
    }
    if (this._maxSoc !== undefined) {
      const limit = fromDisplay(this._maxSoc, "%");
      if (limit === undefined || limit === null || limit < 50 || limit > 100) {
        this._error = t("Bitte eine Ladegrenze zwischen 50 und 100 % eingeben.");
        return false;
      }
      changes.max_soc_pct = limit;
    }
    if (this._fullEvery !== undefined) {
      const days = fromDisplay(this._fullEvery, "");
      if (days === undefined || (days !== null && (!Number.isInteger(days) || days < 1 || days > 90))) {
        this._error = t("Bitte ganze Tage zwischen 1 und 90 eingeben oder das Feld leeren.");
        return false;
      }
      changes.full_charge_every_days = days;
    }
    if (this._gridAfter !== undefined) {
      const days = fromDisplay(this._gridAfter, "");
      if (days === undefined || (days !== null && (!Number.isInteger(days) || days < 1 || days > 120))) {
        this._error = t("Bitte ganze Tage zwischen 1 und 120 eingeben oder das Feld leeren.");
        return false;
      }
      changes.full_charge_grid_after_days = days;
    }
    for (const [edit, key] of [[this._exportLimit, "export_limit_w"], [this._importLimit, "import_limit_w"]]) {
      if (edit === undefined) continue;
      const kw = fromDisplay(edit, "kW");
      if (kw === undefined || (kw !== null && kw <= 0)) {
        this._error = t("Bitte eine gültige Leistung in kW eingeben oder das Feld leeren.");
        return false;
      }
      changes[key] = kw === null ? null : kw * 1000;
    }
    if (this._agingThreshold !== undefined) {
      const threshold = fromDisplay(this._agingThreshold, "%");
      if (threshold === undefined || threshold === null || threshold < 30 || threshold > 100) {
        this._error = t("Bitte eine Schwelle zwischen 30 und 100 % eingeben.");
        return false;
      }
      changes.high_soc_threshold_pct = threshold;
    }
    if (this._agingCost !== undefined) {
      const cost = fromDisplay(this._agingCost, "ct");
      if (cost === undefined || cost === null || cost < 0 || cost > 5) {
        this._error = t("Bitte Kosten zwischen 0 und 5 ct je kWh und Stunde eingeben.");
        return false;
      }
      changes.high_soc_ct_per_kwh_h = cost;
    }
    if (this._reserve !== undefined) {
      const reserve = fromDisplay(this._reserve, "%");
      if (reserve === undefined || reserve === null || reserve < 0 || reserve > 100) {
        this._error = t("Bitte eine Reserve zwischen 0 und 100 % eingeben.");
        return false;
      }
      changes.backup_reserve_pct = reserve;
    }
    return this._saveSettings({ ...settings, ...changes });
  }


  _renderSettings(settings) {
    return html`
      <section class="settings">
        
        <fieldset class="strategy">
          <legend class="field-label">${t("Strategie des Hausspeichers (Standard)")}</legend>
          ${Object.entries(STRATEGIES).map(
            ([value, { label, hint }]) => html`
              <div class="option ${settings.strategy === value ? "selected" : ""}">
                <label class="option-choice">
                  <input type="radio" name="strategy" .checked=${settings.strategy === value} ?disabled=${this._saving}
                    @change=${() => this._saveSettings({ strategy: value })}>
                  <span><strong>${label}</strong><br><span class="small muted">${hint}</span></span>
                </label>
                ${strategyDetails(value)}
              </div>
            `,
          )}
        </fieldset>
        <div class="fields">
          <label class="field">
            <span class="field-label">${t("Ladegrenze im Alltag")}</span>
            <span class="input-unit">
              <input inputmode="decimal" .value=${this._maxSoc ?? toDisplay(settings.max_soc_pct, "%")}
                @input=${(e) => (this._maxSoc = e.target.value)}>
              <span class="muted">%</span>
            </span>
            <span class="small muted">${settings.max_soc_mode === "unless_poor_days"
              ? t("Schont die Zellen: höher lädt Sunflow nur für eine fällige Vollladung oder vor Tagen mit wenig PV. 100 % = keine Grenze.")
              : t("Schont die Zellen: höher lädt Sunflow nur für eine fällige Vollladung. 100 % = keine Grenze.")}</span>
          </label>
          ${settings.max_soc_pct < 100
            ? html`<label class="field">
                <span class="field-label">${t("Ladegrenze gilt")}</span>
                <select ?disabled=${this._saving} @change=${(e) => this._saveSettings({ max_soc_mode: e.target.value })}>
                  <option value="always" ?selected=${settings.max_soc_mode !== "unless_poor_days"}>${t("immer")}</option>
                  <option value="unless_poor_days" ?selected=${settings.max_soc_mode === "unless_poor_days"}>
                    ${t("außer vor Schlechtwetter-Tagen")}</option>
                </select>
                ${settings.max_soc_mode === "unless_poor_days"
                  ? html`<span class="input-unit">
                      <span class="muted">${t("Vorschau")}</span>
                      <select aria-label=${t("Tage im Voraus")} ?disabled=${this._saving}
                        @change=${(e) => this._saveSettings({ poor_days_ahead: Number(e.target.value) })}>
                        ${[1, 2, 3, 4, 5].map((d) => html`<option value=${d} ?selected=${(settings.poor_days_ahead ?? 2) === d}>
                          ${d === 1 ? t("morgen") : t("{days} Tage", { days: d })}</option>`)}
                      </select>
                    </span>`
                  : nothing}
                <span class="small muted">${t("Sagt die PV-Prognose für die nächsten Tage weniger Ertrag als den Tagesverbrauch voraus, setzt Sunflow die Grenze aus und speichert, was die Sonne vorher noch liefert.")}</span>
              </label>`
            : nothing}
          <label class="field">
            <span class="field-label">${t("Voll laden alle")}</span>
            <span class="input-unit">
              <input inputmode="numeric" placeholder=${t("z. B. {example}", { example: "14" })} .value=${this._fullEvery ?? toDisplay(settings.full_charge_every_days, "")}
                @input=${(e) => (this._fullEvery = e.target.value)}>
              <span class="muted">${t("Tage")}</span>
            </span>
            <span class="small muted">${t("Zum Ausgleichen und Kalibrieren der Zellen, bevorzugt mit PV. Leer = nie gezielt.")}</span>
          </label>
          <label class="field">
            <span class="field-label">${t("Spätestens nach … aus dem Netz")}</span>
            <span class="input-unit">
              <input inputmode="numeric" placeholder=${t("z. B. {example}", { example: "21" })} .value=${this._gridAfter ?? toDisplay(settings.full_charge_grid_after_days, "")}
                @input=${(e) => (this._gridAfter = e.target.value)}>
              <span class="muted">${t("Tage")}</span>
            </span>
            <span class="small muted">${t("War der Speicher so lange nicht voll (z. B. im Winter), lädt Sunflow ihn zur günstigsten Zeit aus dem Netz voll – auch wenn Netzladen sonst aus ist. Leer = nie.")}</span>
          </label>
          <div class="field">
            <span class="field-label">${t("Alterung bei hohem Ladestand")}</span>
            <span class="inline aging">
              <input type="checkbox" aria-label=${t("Alterung bei hohem Ladestand einrechnen")} .checked=${settings.high_soc_aging}
                ?disabled=${this._saving} @change=${(e) => this._saveSettings({ high_soc_aging: e.target.checked })}>
              ${settings.high_soc_aging
                ? html`<span class="input-unit">
                      <span class="muted">${t("über")}</span>
                      <input inputmode="decimal" aria-label=${t("Schwelle in Prozent")}
                        .value=${this._agingThreshold ?? toDisplay(settings.high_soc_threshold_pct, "%")}
                        @input=${(e) => (this._agingThreshold = e.target.value)}>
                      <span class="muted">%</span>
                    </span>
                    <span class="input-unit">
                      <input inputmode="decimal" aria-label=${t("Kosten in ct je kWh und Stunde")}
                        .value=${this._agingCost ?? toDisplay(settings.high_soc_ct_per_kwh_h, "ct")}
                        @input=${(e) => (this._agingCost = e.target.value)}>
                      <span class="muted">${t("ct je kWh und Stunde")}</span>
                    </span>`
                : html`${t("einrechnen")}`}
            </span>
            <span class="small muted">${t("Zellen altern schneller, je länger sie voll stehen (kalendarische Alterung). Jede kWh über der Schwelle kostet im Plan je Stunde diesen Betrag – Sunflow lädt dann möglichst kurz vor dem Bedarf, z. B. am Ende des günstigen Nachtfensters statt ab Mitternacht. Beispiel: 4 kWh über der Schwelle für 10 Stunden kosten bei 0,1 ct rund 4 ct. Die Alterung, die mit der Zeit ohnehin eintritt, ist darin nicht enthalten, weil sie nicht vom Betrieb abhängt.")}</span>
          </div>
          <div class="field">
            <span class="field-label">${t("Notstrom-Reserve")}</span>
            <span class="inline">
              <input type="checkbox" aria-label=${t("Notstrom-Reserve freihalten")} .checked=${settings.backup_reserve}
                ?disabled=${this._saving} @change=${(e) => this._saveSettings({ backup_reserve: e.target.checked })}>
              ${settings.backup_reserve
                ? html`<span class="input-unit">
                    <input inputmode="decimal" aria-label=${t("Notstrom-Reserve in Prozent")}
                      .value=${this._reserve ?? toDisplay(settings.backup_reserve_pct, "%")}
                      @input=${(e) => (this._reserve = e.target.value)}>
                    <span class="muted">%</span>
                  </span>`
                : html`${t("freihalten")}`}
            </span>
            <span class="small muted">${t("Gilt in jeder Strategie: Im Alltag plant Sunflow den Speicher nie unter diesen Stand, die Reserve bleibt dem Notstrom (EPS) bei Stromausfall vorbehalten. Liegt der Speicher darunter, wird er nicht weiter entladen.")}</span>
          </div>
          <label class="field">
            <span class="field-label">${t("Hausverbrauch pro Tag (Startwert)")}</span>
            <span class="input-unit">
              <input inputmode="decimal" placeholder=${t("z. B. {example}", { example: "14" })}
                .value=${this._daily ?? toDisplay(settings.daily_consumption_kwh, "kWh")}
                @input=${(e) => (this._daily = e.target.value)}>
              <span class="muted">${t("kWh/Tag")}</span>
            </span>
            <span class="small muted">${t("Gilt nur, solange weniger als 7 Tage Verbrauchsverlauf vorliegen; danach plant Sunflow mit den gemessenen Werten (ab 14 Tagen mit dem gelernten Modell). Ohne große Verbraucher wie Wallbox und Heizstab.")}</span>
            ${this._historyKwh
              ? html`<span class="small">${t("Aus dem Verlauf:")} <strong>${toDisplay(Math.round(this._historyKwh * 10) / 10, "ct")} ${t("kWh/Tag")}</strong>
                  · <button class="link" @click=${() => (this._daily = toDisplay(Math.round(this._historyKwh * 10) / 10, "ct"))}>${t("übernehmen")}</button></span>`
              : nothing}
          </label>
          <label class="field">
            <span class="field-label">${t("Großverbraucher ausklammern ab")}</span>
            <span class="input-unit">
              <input inputmode="decimal"
                .value=${this._largeLoad ?? toDisplay(settings.large_load_threshold_w === null ? null : settings.large_load_threshold_w / 1000, "kW")}
                @input=${(e) => (this._largeLoad = e.target.value)}>
              <span class="muted">kW</span>
            </span>
            <span class="small muted">${t("Stunden mit mehr Hausverbrauch (z. B. Backofen, Sauna) zählen nicht zur Lastprognose, damit seltene Spitzen den Alltag nicht verzerren. Wallbox, Heizstab und andere Verbraucher in Sunflow rechnet es ohnehin heraus. Leer = nichts ausklammern.")}</span>
          </label>
          <div class="field wide">
            <span class="field-label">${t("Verschleißkosten Speicher")}</span>
            <span class="small muted">${t("Stellst du je Speicher ein: Einstellungen → Geräte → Speicher.")}</span>
          </div>
          <label class="field">
            <span class="field-label">${t("Laden aus dem Netz")}</span>
            <span class="inline">
              <input type="checkbox" .checked=${settings.allow_grid_charge} ?disabled=${this._saving}
                @change=${(e) => this._saveSettings({ allow_grid_charge: e.target.checked })}>
              ${t("erlaubt, wenn es sich rechnet")}
            </span>
          </label>
          <label class="field">
            <span class="field-label">${t("PV-Prognose")}</span>
            <span class="inline">
              <input type="checkbox" .checked=${settings.pv_learning} ?disabled=${this._saving}
                @change=${(e) => this._saveSettings({ pv_learning: e.target.checked })}>
              ${t("an die Anlage anpassen (aus Messwerten lernen)")}
            </span>
            <span class="small muted">${t("Vergleicht Prognose und Messung je Stunde und Bewölkung, Details unter „Analyse“.")}</span>
            <span class="inline">
              <input type="checkbox" .checked=${settings.pv_sun} ?disabled=${this._saving}
                @change=${(e) => this._saveSettings({ pv_sun: e.target.checked })}>
              ${t("nach Sonnenstand korrigieren (Test)")}
            </span>
            <span class="small muted">${t("Für Flächen mit Solcast-Historie: lernt aus bis zu zwei Jahren, wie viel je Sonnenhöhe und -richtung ankommt, und erkennt so Verschattung, die mit der Jahreszeit wandert. Ersetzt für diese Flächen die Korrektur nach Uhrzeit; Backtest und Verschattungskarte unter „Analyse“.")}</span>
          </label>
          <div class="field wide">
            <span class="field-label">${t("Netzdienlich")}</span>
            <span class="inline">
              <input type="checkbox" .checked=${settings.grid_friendly} ?disabled=${this._saving}
                @change=${(e) => this._saveSettings({ grid_friendly: e.target.checked })}>
              ${t("Einspeise- und Bezugsspitzen vermeiden, wo es wenig kostet")}
            </span>
            <span class="small muted">${t("Weiches Ziel, keine harte Grenze: Sunflow verschiebt Speicherladen und Verbraucher, damit möglichst wenig über den Werten liegt. Jede kWh darüber zählt in der Planung wie 20 ct Mehrkosten (nicht in den angezeigten Kosten). Lässt sich eine Spitze nicht vermeiden – Speicher voll, kein Verbraucher –, wird sie trotzdem zugelassen; PV wird nie abgeregelt.")}</span>
            ${settings.grid_friendly
              ? html`<div class="fields">
                  <label class="field">
                    <span class="field-label">${t("Einspeisung möglichst unter")}</span>
                    <span class="input-unit"><input inputmode="decimal" placeholder=${t("z. B. {example}", { example: "6" })}
                      .value=${this._exportLimit ?? toDisplay(settings.export_limit_w === null ? null : settings.export_limit_w / 1000, "kW")}
                      @input=${(e) => (this._exportLimit = e.target.value)}><span class="muted">kW</span></span>
                    <span class="small muted">${t("Der Speicher wird morgens nicht schon voll, sondern nimmt die Mittagsspitze auf; Wallbox und Heizstab laufen bevorzugt dann. Ersetzt keine vorgeschriebene Begrenzung im Wechselrichter (Solarspitzengesetz: oft 60 % der PV-Leistung).")}</span>
                  </label>
                  <label class="field">
                    <span class="field-label">${t("Netzbezug möglichst unter")}</span>
                    <span class="input-unit"><input inputmode="decimal" placeholder=${t("z. B. {example}", { example: "11" })}
                      .value=${this._importLimit ?? toDisplay(settings.import_limit_w === null ? null : settings.import_limit_w / 1000, "kW")}
                      @input=${(e) => (this._importLimit = e.target.value)}><span class="muted">kW</span></span>
                    <span class="small muted">${t("Wallbox, Heizstab und Laden aus dem Netz laufen dann eher nacheinander als gleichzeitig, z. B. damit der Hausanschluss nicht überlastet wird.")}</span>
                  </label>
                </div>`
              : nothing}
          </div>
          <div class="field">
            <span class="field-label">${t("Unsichere PV-Prognose")}</span>
            <select aria-label=${t("Unsichere PV-Prognose")} ?disabled=${this._saving}
              @change=${(e) => this._saveSettings({ pv_risk: e.target.value })}>
              ${[
                ["expected", t("Erwartungswert (optimistischer)")],
                ["balanced", t("Ausgewogen")],
                ["cautious", t("Vorsichtig")],
              ].map(([value, label]) => html`<option value=${value} ?selected=${settings.pv_risk === value}>${label}</option>`)}
            </select>
            <span class="small muted">${t("Vorsichtig plant mehr mit dem schlechten Fall der Prognose (Solcast p10): lieber nachts günstig nachladen, als bei weniger Sonne teuer nachzukaufen.")}</span>
          </div>
          <div class="field">
            <span class="field-label">${t("Wetter für die Bewölkung")}</span>
            <sf-picker label=${t("Wetter-Entität")} ?disabled=${this._saving}
              empty-label=${t("Automatisch (erste Wetter-Entität)")} .value=${settings.weather_entity ?? ""}
              .options=${(this._weather ?? []).map((w) => ({
                value: w.entity_id,
                label: w.name,
                detail: [w.provider, w.cloud_coverage ? null : t("ohne Bewölkungsangabe")].filter(Boolean).join(" · "),
              }))}
              @change=${(e) => this._saveSettings({ weather_entity: e.detail.value || null })}></sf-picker>
            <span class="small muted">${t("Liefert die Entität keine Bewölkung, lernt Sunflow nur je Stunde.")}</span>
          </div>
        </div>
        ${[this._reserve, this._maxSoc, this._fullEvery, this._gridAfter, this._exportLimit, this._importLimit, this._daily, this._largeLoad, this._agingThreshold, this._agingCost].some((v) => v !== undefined)
          ? html`<div class="actions">
              <button class="ok" ?disabled=${this._saving} @click=${() => this._saveNumbers(settings)}>
                ${t("Speichern und neu planen")}
              </button>
            </div>`
          : html`<p class="small muted">${t("Strategie und Netzladen gelten sofort. Zahlenwerte werden nach einer Änderung mit „Speichern und neu planen“ übernommen.")}</p>`}
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
    .option { flex-direction: column; gap: 4px; cursor: default; }
    .option-choice { display: flex; gap: 10px; align-items: flex-start; cursor: pointer; }
    .strategy-details { padding-left: 28px; }
    .strategy-details summary { cursor: pointer; color: var(--sf-primary-text); }
    .strategy-details ul { margin: 6px 0 0; padding-left: 18px; display: grid; gap: 4px; }
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
    .inline input[type="checkbox"] { width: 18px; height: 18px; }
    .aging { display: flex; flex-wrap: wrap; }
    .aging .input-unit { flex: 1 1 140px; }
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
