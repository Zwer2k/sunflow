import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { formatSigned, formatValue, fromDisplay, toDisplay } from "../lib/format.js";
import { t, tn } from "../lib/i18n.js";
import { isOpen, setOpen } from "../lib/collapse.js";

/** Batteries of the inverters with their modules (setup step "Geräte", category "Speicher"). */
class SunflowBatteries extends LitElement {
  static properties = {
    _batteries: { state: true },
    _error: { state: true },
    _busy: { state: true },
    // By device id: edited wear inputs ({ mode, direct, price, cycles, capacity, dod, eol })
    _costEdits: { state: true },
  };

  constructor() {
    super();
    this._batteries = null;
    this._error = null;
    this._busy = false;
    this._costEdits = {};
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
  }

  async _load() {
    try {
      this._batteries = await api.batteries();
      this._error = null;
    } catch (err) {
      this._error = t("Speicher nicht verfügbar: {error}", { error: err.message });
    }
  }

  render() {
    return html`
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${(this._batteries ?? []).map((b) => this._renderBattery(b))}
    `;
  }

  _renderBattery(b) {
    const open = isOpen(`battery:${b.device_id}`);
    const assigned = b.modules.filter((m) => m.assigned);
    const sum = assigned.reduce((total, m) => total + (m.capacity_wh ?? 0), 0);
    return html`
      <section class="device ${open ? "" : "compact"}">
        <div class="device-head">
          <button class="device-toggle" aria-expanded=${open} @click=${() => this._toggle(b.device_id)}>
            <span class="caret" aria-hidden="true">${open ? "▾" : "▸"}</span>
            <span>
              <span class="device-title">${t("Speicher von {inverter}", { inverter: b.inverter })}</span>
              <span class="muted small device-sub">${assigned.length
                ? b.auto
                  ? tn(assigned.length, "{count} Module (automatisch zugeordnet)", { count: assigned.length })
                  : tn(assigned.length, "{count} Module", { count: assigned.length })
                : t("keine Module erkannt")}</span>
            </span>
          </button>
          <span class="chip ok">${t("gehört zu {inverter}", { inverter: b.inverter })}</span>
        </div>
        <div class="summary small">${[
          b.soc_pct !== null ? t("Ladestand {soc} %", { soc: Math.round(b.soc_pct) }) : null,
          b.power_w !== null ? t("Leistung {power}", { power: formatSigned(b.power_w, "W") }) : null,
          b.capacity_wh ? t("Kapazität {capacity}", { capacity: formatValue(b.capacity_wh, "Wh") }) : null,
        ].filter(Boolean).join(" · ")}</div>
        ${open ? this._renderModules(b, sum) : nothing}
        ${open ? this._renderCost(b) : nothing}
      </section>
    `;
  }

  _renderModules(b, sum) {
    return html`
      <div class="editor">
        <label class="inline">
          <input type="checkbox" .checked=${b.auto} ?disabled=${this._busy}
            @change=${(e) => this._save(b, e.target.checked ? null : b.modules.filter((m) => m.assigned).map((m) => m.ha_device_id))}>
          ${t("Module automatisch zuordnen (Untergeräte des Wechselrichters in Home Assistant)")}
        </label>
        <ul class="modules">
          ${b.modules.map((m) => html`<li>
            <label class="inline">
              <input type="checkbox" .checked=${m.assigned} ?disabled=${b.auto || this._busy}
                @change=${(e) => this._save(b, b.modules
                  .filter((o) => (o.ha_device_id === m.ha_device_id ? e.target.checked : o.assigned))
                  .map((o) => o.ha_device_id))}>
              ${m.name}</label>
            <span class="small muted">${[m.model, m.capacity_wh ? formatValue(m.capacity_wh, "Wh") : null, m.native ? null : t("Untergerät eines anderen Wechselrichters")]
              .filter(Boolean).join(" · ")}</span>
          </li>`)}
        </ul>
        ${sum
          ? html`<span class="small muted">${b.capacity_wh
              ? t("Summe der Module: {sum}. Die nutzbare Kapazität (Kenndaten) wird daraus vorgeschlagen, solange keine eigene eingetragen ist – aktuell {capacity}.", { sum: formatValue(sum, "Wh"), capacity: formatValue(b.capacity_wh, "Wh") })
              : t("Summe der Module: {sum}. Die nutzbare Kapazität (Kenndaten) wird daraus vorgeschlagen, solange keine eigene eingetragen ist.", { sum: formatValue(sum, "Wh") })}</span>`
          : nothing}
      </div>
    `;
  }

  _costInputs(b) {
    const stored = b.cost.calculation ?? {};
    return {
      mode: b.cost.calculation ? "calc" : "direct",
      direct: toDisplay(b.cost.wear_ct_per_kwh, "ct"),
      price: toDisplay(stored.price_eur, "ct"),
      cycles: toDisplay(stored.cycles, "ct"),
      capacity: toDisplay(stored.capacity_kwh ?? (b.capacity_wh ? b.capacity_wh / 1000 : undefined), "ct"),
      dod: toDisplay(stored.depth_of_discharge_pct ?? 90, "ct"),
      eol: toDisplay(stored.end_of_life_pct ?? 70, "ct"),
      ...(this._costEdits[b.device_id] ?? {}),
    };
  }

  /** The cost to store, or null when an input is invalid. */
  _parseCost(b) {
    const inputs = this._costInputs(b);
    const number = (text) => fromDisplay(text ?? "", "ct");
    if (inputs.mode === "direct") {
      const wear = number(inputs.direct);
      return typeof wear === "number" && wear >= 0 ? { include: b.cost.include, wear_ct_per_kwh: wear, calculation: null } : null;
    }
    const calculation = {
      price_eur: number(inputs.price),
      cycles: number(inputs.cycles),
      capacity_kwh: number(inputs.capacity),
      depth_of_discharge_pct: number(inputs.dod),
      end_of_life_pct: number(inputs.eol),
    };
    const valid = Object.values(calculation).every((v) => typeof v === "number" && v > 0)
      && calculation.depth_of_discharge_pct <= 100 && calculation.end_of_life_pct <= 100;
    return valid ? { include: b.cost.include, calculation } : null;
  }

  _renderCost(b) {
    const inputs = this._costInputs(b);
    const edit = (field, value) => (this._costEdits = {
      ...this._costEdits, [b.device_id]: { ...(this._costEdits[b.device_id] ?? {}), [field]: value },
    });
    const edited = b.device_id in this._costEdits;
    const parsed = inputs.mode === "calc" ? this._parseCost(b) : null;
    // Same formula as the backend: price / (cycles x mean capacity x depth of discharge)
    const result = parsed?.calculation
      ? (parsed.calculation.price_eur * 100) / (parsed.calculation.cycles * parsed.calculation.capacity_kwh
        * ((1 + parsed.calculation.end_of_life_pct / 100) / 2) * (parsed.calculation.depth_of_discharge_pct / 100))
      : null;
    const [resultBefore, resultAfter] = t("ergibt {value} je entladener kWh").split("{value}");
    const field = (label, key, unit, hint = null) => html`
      <label class="field">
        <span class="field-label">${label}</span>
        <span class="input-unit"><input inputmode="decimal" .value=${inputs[key] ?? ""} ?disabled=${!b.cost.include}
          @input=${(e) => edit(key, e.target.value)}>
          <span class="muted">${unit}</span></span>
        ${hint ? html`<span class="small muted">${hint}</span>` : nothing}
      </label>`;
    return html`
      <div class="editor">
        <span class="field-label">${t("Verschleißkosten")}</span>
        <label class="inline">
          <input type="checkbox" .checked=${b.cost.include} ?disabled=${this._busy}
            @change=${(e) => this._saveCost(b, { ...b.cost, include: e.target.checked })}>
          ${t("In der Planung berücksichtigen")}
        </label>
        <span class="small muted">${b.cost.include
          ? t("Je entladener kWh – verhindert Zyklen für Kleinstbeträge.")
          : t("Ausgeschaltet zählen Zyklen dieses Speichers als kostenlos: er wird auch für kleine Preisunterschiede genutzt.")}</span>
        ${b.cost.include ? html`
          <div class="segmented" role="group" aria-label=${t("Verschleißkosten angeben")}>
            ${[["direct", t("Direkt in ct/kWh")], ["calc", t("Aus Preis und Zyklen")]].map(
              ([value, label]) => html`<button class=${inputs.mode === value ? "on" : ""} aria-pressed=${inputs.mode === value}
                @click=${() => edit("mode", value)}>${label}</button>`,
            )}
          </div>
          ${inputs.mode === "direct"
            ? html`<span class="input-unit">
                  <input inputmode="decimal" aria-label=${t("Verschleißkosten in ct/kWh")}
                    .value=${inputs.direct ?? ""} @input=${(e) => edit("direct", e.target.value)}>
                  <span class="muted">ct/kWh</span>
                </span>`
            : html`<div class="fields">
                  ${field(t("Anschaffungspreis"), "price", "€", t("Speicher ohne Wechselrichter, inkl. Montage"))}
                  ${field(t("Zyklen laut Hersteller"), "cycles", t("Zyklen"))}
                  ${field(t("Kapazität"), "capacity", "kWh")}
                  ${field(t("bei Entladetiefe"), "dod", "%")}
                  ${field(t("Restkapazität danach"), "eol", "%", t("z. B. 70 % – die Kapazität sinkt bis dahin"))}
                </div>
                <span class="small">${result !== null
                  ? html`${resultBefore}<strong>${toDisplay(Math.round(result * 100) / 100, "ct")} ct/kWh</strong>${resultAfter}`
                  : html`<span class="muted">${t("Preis, Zyklen und Kapazität eingeben")}</span>`}</span>`}
          ${edited
            ? html`<div class="actions">
                <button class="ok" ?disabled=${this._busy} @click=${() => this._saveEditedCost(b)}>${t("Speichern und neu planen")}</button>
              </div>`
            : nothing}` : nothing}
      </div>
    `;
  }

  async _saveEditedCost(b) {
    const cost = this._parseCost(b);
    if (!cost) {
      this._error = this._costInputs(b).mode === "direct"
        ? t("Bitte gültige Verschleißkosten eingeben.")
        : t("Bitte Preis, Zyklen und Kapazität für den Verschleiß angeben.");
      return;
    }
    if (await this._saveCost(b, cost)) {
      const { [b.device_id]: _, ...rest } = this._costEdits;
      this._costEdits = rest;
    }
  }

  async _saveCost(battery, cost) {
    this._busy = true;
    try {
      this._batteries = await api.setBatteryCost(battery.device_id, cost);
      this._error = null;
      return true;
    } catch (err) {
      this._error = t("Verschleißkosten nicht gespeichert: {error}", { error: err.message });
      return false;
    } finally {
      this._busy = false;
    }
  }

  _toggle(id) {
    setOpen(`battery:${id}`, !isOpen(`battery:${id}`));
    this.requestUpdate();
  }

  async _save(battery, modules) {
    this._busy = true;
    try {
      this._batteries = await api.setBatteryModules(battery.device_id, modules);
      this._error = null;
    } catch (err) {
      this._error = t("Module nicht gespeichert: {error}", { error: err.message });
    } finally {
      this._busy = false;
    }
  }

  static styles = css`
    :host { display: flex; flex-direction: column; gap: 12px; }
    * { box-sizing: border-box; }
    button { font: inherit; color: inherit; }
    .muted { color: var(--sf-muted); }
    .small { font-size: 13px; }
    .device { border: 1px solid var(--sf-border); border-radius: 10px; padding: 18px; display: flex; flex-direction: column; gap: 10px; }
    .device.compact { padding: 12px 18px; gap: 6px; }
    .device-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; align-items: center; }
    .device-title { font-weight: 600; font-size: 16px; display: block; }
    .device-sub { display: block; }
    .device-toggle { display: flex; gap: 8px; align-items: flex-start; border: 0; background: none; padding: 0; cursor: pointer; text-align: left; }
    .caret { width: 14px; color: var(--sf-muted); line-height: 22px; }
    .summary { color: var(--sf-muted); padding-left: 22px; }
    .chip { font-size: 12px; padding: 3px 10px; border-radius: 999px; background: var(--sf-ok-bg); color: var(--sf-ok-text); }
    .editor { display: flex; flex-direction: column; gap: 10px; padding: 14px; background: var(--sf-subtle); border-radius: 8px; }
    .modules { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
    .modules li { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline; }
    .inline { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 14px; }
    .inline input { width: 18px; height: 18px; margin: 0; }
    .field-label { font-weight: 500; font-size: 14px; }
    .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
    .fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 6px 12px; }
    /* Label, input and hint share their rows across a line, however long a label wraps */
    .fields .field { display: grid; grid-template-rows: subgrid; grid-row: span 3; align-items: end; padding-bottom: 6px; }
    .fields .field .small { align-self: start; }
    .segmented { display: flex; flex-wrap: wrap; gap: 4px; background: var(--sf-surface); padding: 4px; border-radius: 10px; align-self: flex-start; }
    .segmented button {
      min-height: 36px; padding: 0 12px; border: 0; border-radius: 8px; background: transparent;
      cursor: pointer; font-size: 14px; color: var(--sf-muted);
    }
    .segmented button.on { background: var(--sf-subtle); color: var(--sf-text); font-weight: 600; box-shadow: 0 1px 2px rgb(0 0 0 / 0.1); }
    .input-unit { display: flex; align-items: center; gap: 8px; max-width: 320px; }
    .input-unit input {
      flex: 1; min-width: 0; min-height: 44px; padding: 0 12px; font: inherit; font-size: 15px;
      border: 1px solid var(--sf-border-strong); border-radius: 8px; background: var(--sf-surface); color: var(--sf-text);
    }
    .actions { display: flex; gap: 10px; }
    .ok { min-height: 44px; padding: 0 18px; border-radius: 8px; cursor: pointer; font-weight: 600; border: 0; background: var(--sf-ok); color: var(--sf-on-ok); }
    button:disabled { cursor: not-allowed; opacity: .6; }
    .alert.error { padding: 10px 14px; border-radius: 8px; background: var(--sf-error-bg); color: var(--sf-error-text); }
  `;
}

customElements.define("sf-batteries", SunflowBatteries);
