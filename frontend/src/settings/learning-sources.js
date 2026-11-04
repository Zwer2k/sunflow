import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import "../components/picker.js";
import { api } from "../lib/api.js";
import { locale, t } from "../lib/i18n.js";

// Below this share of plausible values the source is shown as doubtful
const QUALITY_WARN_PCT = 80;

const KINDS = [
  {
    key: "outdoor_temperature", candidates: "temperature", label: t("Außentemperatur"),
    hint: t("Im Schatten gemessen. Für den Heizanteil am Hausverbrauch und die Modultemperatur. Ohne: Temperatur der Wetter-Entität."),
  },
  {
    key: "outdoor_humidity", candidates: "humidity", label: t("Luftfeuchte draußen"),
    hint: t("Mit der Temperatur: Tau und Reif auf den Modulen am Morgen."),
  },
  {
    key: "irradiance", candidates: "irradiance", label: t("Helligkeit oder Einstrahlung"),
    hint: t("Wie hell es tatsächlich war: trennt Wolken von Verschattung, auch für vergangene Tage."),
  },
  {
    key: "precipitation", candidates: "precipitation", label: t("Niederschlag"),
    hint: t("Für die Erkennung von Schnee auf den Modulen."),
  },
];

/** Optional sensors the learning uses (settings area "Planung"). */
class SunflowLearningSources extends LitElement {
  static properties = {
    _data: { state: true },
    _coverage: { state: true },
    _quality: { state: true },
    _error: { state: true },
    _saving: { state: true },
  };

  constructor() {
    super();
    this._coverage = {};
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
  }

  async _load() {
    try {
      this._data = await api.learningSources();
      this._error = null;
      this._loadCoverage();
      this._loadQuality();
    } catch (err) {
      this._error = t("Lernquellen konnten nicht geladen werden: {error}", { error: err.message });
    }
  }

  _chosen() {
    const sources = this._data.sources;
    return [...KINDS.map((k) => sources[k.key]), ...Object.values(sources.module_temperature)].filter(Boolean);
  }

  async _loadCoverage() {
    const chosen = this._chosen().filter((e) => !(e in this._coverage));
    if (!chosen.length) return;
    try {
      const inventory = await api.dataInventory(false, chosen);
      const found = Object.fromEntries(inventory.entities.map((e) => [e.entity_id, e]));
      this._coverage = { ...this._coverage, ...found };
    } catch {
      // Only a hint; the choice works without it
    }
  }

  async _loadQuality() {
    try {
      this._quality = await api.learningSourceQuality();
    } catch {
      // Only a hint; the choice works without it
      this._quality = null;
    }
  }

  async _save(changes) {
    this._saving = true;
    try {
      const sources = await api.setLearningSources({ ...this._data.sources, ...changes });
      this._data = { ...this._data, sources };
      this._error = null;
      this._loadCoverage();
      this._loadQuality();
    } catch (err) {
      this._error = t("Lernquellen konnten nicht gespeichert werden: {error}", { error: err.message });
    } finally {
      this._saving = false;
    }
  }

  _qualityText(key) {
    const q = this._quality?.[key]?.quality;
    if (!q || q.valid_share === null) return nothing;
    const pct = Math.round(q.valid_share * 100);
    const details = [
      q.gaps ? t("Ausfälle: {count} ({hours} h)", { count: q.gaps, hours: Math.round(q.gap_hours) }) : null,
      q.stuck_hours >= 1 ? t("{hours} h unverändert", { hours: Math.round(q.stuck_hours) }) : null,
      q.spikes ? t("Ausreißer: {count}", { count: q.spikes }) : null,
      q.out_of_range ? t("unmögliche Werte: {count}", { count: q.out_of_range }) : null,
    ].filter(Boolean);
    return html`<span class="small ${pct < QUALITY_WARN_PCT ? "warn" : "muted"}"
      title=${t("Geprüft über die aufgezeichneten Tage. Unplausible Werte und Ausfälle lässt das Lernen weg; Zeiten, in denen Home Assistant nicht aufgezeichnet hat, zählen nicht mit.")}>
      ${t("{pct} % plausibel", { pct })}${details.length ? ` · ${details.join(" · ")}` : ""}</span>`;
  }

  _coverageText(entityId) {
    const c = this._coverage[entityId];
    if (!c) return nothing;
    const history = c.states_days ? t("Verlauf {days} Tage", { days: c.states_days }) : t("kein Verlauf aufgezeichnet");
    const statistics = c.statistics_since
      ? t("Langzeitstatistik seit {date}", {
          date: new Date(c.statistics_since).toLocaleDateString(locale(), { month: "short", year: "numeric" }),
        })
      : t("keine Langzeitstatistik (ohne state_class)");
    return html`<span class="small muted">${history} · ${statistics}</span>`;
  }

  _select(label, candidates, value, onChange) {
    return html`<sf-picker label=${label} ?disabled=${this._saving} empty-label=${t("Keiner")} .value=${value ?? ""}
      .options=${candidates.map((c) => ({
        value: c.entity_id,
        label: c.name,
        detail: c.state && !Number.isNaN(Number(c.state)) ? `${c.state} ${c.unit ?? ""}`.trim() : null,
      }))}
      @change=${(e) => onChange(e.detail.value || null)}></sf-picker>`;
  }

  render() {
    if (!this._data) return this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing;
    const { sources, candidates, planes } = this._data;
    return html`
      <section>
        <h2>${t("Lernquellen")}</h2>
        <p class="small muted">${t("Zusätzliche Sensoren machen die gelernten Prognosen genauer. Keiner ist nötig. Unter jeder Auswahl steht, wie weit Home Assistant ihre Werte zurück aufhebt.")}</p>
        ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
        <div class="fields">
          ${KINDS.map((kind) => html`<div class="field">
            <span class="field-label">${kind.label}</span>
            ${this._select(kind.label, candidates[kind.candidates], sources[kind.key], (value) => this._save({ [kind.key]: value }))}
            ${sources[kind.key] ? html`${this._coverageText(sources[kind.key])}${this._qualityText(kind.key)}` : nothing}
            <span class="small muted">${kind.hint}</span>
          </div>`)}
        </div>
        ${planes.length
          ? html`<h3>${t("Modultemperatur je Dachfläche")}</h3>
              <p class="small muted">${t("Ein Fühler an den Modulen der Fläche. Warme Module liefern weniger, etwa 0,4 % je Grad über 25 °C. Ohne: aus Außentemperatur und Einstrahlung geschätzt.")}</p>
              <div class="fields">
                ${planes.map((plane) => {
                  const value = sources.module_temperature[plane.key];
                  return html`<div class="field">
                    <span class="field-label">${plane.name}</span>
                    ${this._select(t("Modultemperatur {name}", { name: plane.name }), candidates.temperature, value, (entity) => {
                      const modules = { ...sources.module_temperature };
                      if (entity) modules[plane.key] = entity;
                      else delete modules[plane.key];
                      this._save({ module_temperature: modules });
                    })}
                    ${value ? html`${this._coverageText(value)}${this._qualityText(`module_temperature:${plane.key}`)}` : nothing}
                  </div>`;
                })}
              </div>`
          : nothing}
      </section>
    `;
  }

  static styles = css`
    :host { display: block; }
    * { box-sizing: border-box; }
    section { border-top: 1px solid var(--sf-border); padding-top: 16px; display: flex; flex-direction: column; gap: 12px; }
    h2 { margin: 0; font-size: 16px; }
    h3 { margin: 8px 0 0; font-size: 15px; }
    p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .warn { color: var(--sf-warn-text); }
    .alert { padding: 10px 14px; border-radius: 8px; font-size: 14px; }
    .alert.error { background: var(--sf-error-bg); color: var(--sf-error-text); }
    .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; min-width: 0; }
    .field-label { font-weight: 500; }
    select {
      min-height: 40px; padding: 0 10px; font: inherit; font-size: 14px; border: 1px solid var(--sf-border-strong);
      border-radius: 8px; background: var(--sf-surface); color: var(--sf-text); max-width: 100%;
    }
  `;
}

customElements.define("sunflow-learning-sources", SunflowLearningSources);
