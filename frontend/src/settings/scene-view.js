import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { t } from "../lib/i18n.js";
import { toggleSheet } from "../lib/display-toggles.js";
import { FIGURE_LABELS, guessFigure, ROOF_FACES, ROOF_LABELS, roofKey } from "../lib/scene.js";

/** Settings "Anzeige": what goes where in the live view's house pictures. */
class SunflowSceneSettings extends LitElement {
  static properties = {
    _live: { state: true },
    _scene: { state: true },
    _error: { state: true },
  };

  constructor() {
    super();
    this._live = null;
    this._scene = null;
    this._error = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
  }

  async _load() {
    try {
      const [live, display] = await Promise.all([api.live(), api.display()]);
      this._live = live;
      this._scene = display.scene;
      this._error = null;
    } catch (err) {
      this._error = t("Zuordnung nicht abrufbar: {error}", { error: err.message });
    }
  }

  async _save(changes) {
    const scene = { ...this._scene, ...changes };
    try {
      this._scene = await api.setScene(scene);
      this._error = null;
    } catch (err) {
      this._error = t("Einstellung nicht gespeichert: {error}", { error: err.message });
    }
  }

  /** The map without the key when "auto" is chosen. */
  _choose(field, key, value) {
    const { [key]: _old, ...rest } = this._scene[field];
    return this._save({ [field]: value ? { ...rest, [key]: value } : rest });
  }

  render() {
    if (this._error) return html`<div class="alert error">${this._error}</div>`;
    if (!this._live || !this._scene) return html`<p class="muted">${t("Lade …")}</p>`;
    return html`
      <section class="device compact">
        <div class="device-title">${t("Hausbilder in der Live-Ansicht")}</div>
        <p class="small muted help">${t("Wo Strings, Wechselrichter und Verbraucher in den Ansichten „Haus“ und „3D“ erscheinen. „Automatisch“ richtet sich nach den Namen der Prognose-Flächen und Verbraucher.")}</p>
        ${this._renderRoofs()}
        ${this._renderInverters()}
        ${this._renderConsumers()}
      </section>
    `;
  }

  _select(label, value, options, onChange) {
    return html`<label class="field"><span class="field-label">${label}</span>
      <select @change=${(e) => onChange(e.target.value || null)}>
        ${options.map(([key, text]) => html`<option value=${key} ?selected=${(value ?? "") === key}>${text}</option>`)}
      </select></label>`;
  }

  _renderRoofs() {
    const plants = this._live.pv;
    if (!plants.length) return nothing;
    const options = [["", t("Automatisch")], ...[...ROOF_FACES, "hidden"].map((face) => [face, ROOF_LABELS[face]])];
    const rows = plants.flatMap((plant) =>
      plant.strings?.length
        ? plant.strings.map((s) => [roofKey(plant, s), s.plane ? `${s.plane} (${plant.name} ${s.name})` : `${plant.name} ${s.name}`])
        : [[roofKey(plant), plant.name]],
    );
    return html`<h3>${t("Dachflächen (nur 3D)")}</h3>
      <div class="fields">
        ${rows.map(([key, name]) => this._select(name, this._scene.roofs[key], options, (v) => this._choose("roofs", key, v)))}
      </div>`;
  }

  _renderInverters() {
    const plants = this._live.pv;
    if (plants.length < 2) return nothing;
    const [first, second] = this._scene.inverters;
    const options = (exclude) => [["", t("Automatisch")], ...plants.filter((p) => p.device_id !== exclude).map((p) => [p.device_id, p.name])];
    const save = (list) => this._save({ inverters: list.filter(Boolean) });
    return html`<h3>${t("Wechselrichter (nur 3D)")}</h3>
      <p class="small muted help">${t("Zwei Plätze an der Wand des Anbaus; die Speicher hängen beim eigenen Wechselrichter.")}</p>
      <div class="fields">
        ${this._select(t("Neben dem Haus"), first, options(second), (v) => save([v, v ? second : null]))}
        ${first ? this._select(t("Am Carport"), second, options(first), (v) => save([first, v])) : nothing}
      </div>`;
  }

  _renderConsumers() {
    const seen = new Set();
    const consumers = [...this._live.consumers, ...(this._live.appliances ?? [])].filter((c) => !seen.has(c.device_id) && seen.add(c.device_id));
    if (!consumers.length) return nothing;
    return html`<h3>${t("Verbraucher")}</h3>
      <div class="fields">
        ${consumers.map((consumer) => {
          const guess = guessFigure(consumer);
          const figures = Object.keys(FIGURE_LABELS).filter((f) => (f === "vehicle") === (consumer.kind === "vehicle") || f === "house");
          const options = [
            ["", t("Automatisch: {figure}", { figure: FIGURE_LABELS[guess ?? "house"] })],
            ...figures.map((f) => [f, FIGURE_LABELS[f]]),
          ];
          return this._select(consumer.name, this._scene.consumers[consumer.device_id], options, (v) => this._choose("consumers", consumer.device_id, v));
        })}
      </div>`;
  }

  static styles = [toggleSheet, css`
    :host { display: block; }
    * { box-sizing: border-box; }
    select { font: inherit; color: inherit; }
    h3 { margin: 4px 0 0; font-size: 15px; }
    p { margin: 0; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
    .device { border: 1px solid var(--sf-border); border-radius: 10px; padding: 18px; display: flex; flex-direction: column; gap: 12px; }
    .device-title { font-weight: 600; font-size: 16px; display: block; }
    .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .field-label { font-weight: 500; font-size: 13px; }
    .field select { min-height: 44px; font-size: 14px; border: 1px solid var(--sf-border-strong); border-radius: 8px;
      background: var(--sf-surface); color: var(--sf-text); padding: 0 10px; }
  `];
}

customElements.define("sf-scene-settings", SunflowSceneSettings);
