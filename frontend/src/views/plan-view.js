import { LitElement, html, css, nothing } from "../../vendor/lit-all.min.js";
import { api } from "../lib/api.js";
import { poll } from "../lib/poll.js";
import { lineChart, priceChart, SERIES_COLORS, UNASSIGNED_COLOR } from "../components/charts.js";
import { formatValue, toDisplay } from "../lib/format.js";
import { plugInHints } from "../lib/plug-in.js";
import { decimal, locale, t, tn } from "../lib/i18n.js";
import { isOpen, setOpen } from "../lib/collapse.js";
import { ACTIONS } from "../lib/actions.js";

const LOAD_QUALITY = {
  day_type: t("nach Wochentag"),
  all_days: t("Mittel aller Tage"),
  mixed: t("teils nach Wochentag"),
  default: t("Standardwert"),
};
// Chart window limits: statistics go back about a month, Solcast looks a week ahead
const MAX_BACK_DAYS = 31;
const MAX_AHEAD_DAYS = 7;
// Movement before a press on a chart counts as dragging, not as a click
const DRAG_THRESHOLD_PX = 5;
// The plan never reaches further than this; beyond it only the PV forecast is shown
const MAX_PLAN_HOURS = 48;
// The plan is kept by the background runner; reloading only fetches it and the statistics
const REFRESH_MS = 60 * 1000;
// Remembered per browser; storage may be unavailable (private mode, previews)
const TOTAL_KEY = "sunflow.plan.total";
const RAW_PV_KEY = "sunflow.plan.rawPv";

const time = new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit" });
const dayTime = new Intl.DateTimeFormat(locale(), { weekday: "short", hour: "2-digit", minute: "2-digit" });
const euro = new Intl.NumberFormat(locale(), { style: "currency", currency: "EUR" });

/** Name of the forecast source(s) in use, e.g. "Solcast" or "Solcast + Forecast.Solar". */
function rawLabel(plan) {
  return plan.pv_sources?.length ? plan.pv_sources.join(" + ") : t("PV-Prognose");
}

/** Energy in Wh of a power column over 15 minute slots. */
function energy(slots, key) {
  return slots.reduce((sum, s) => sum + s[key] / 4, 0);
}

/** Consecutive slots in which a flexible load runs, with energy and main source. */
function flexWindows(slots, name) {
  const windows = [];
  for (const slot of slots) {
    const power = slot.flex_w?.[name] ?? 0;
    const last = windows.at(-1);
    if (power < 50) {
      if (last) last.closed = true;
      continue;
    }
    const surplus = Math.max(slot.pv_w - slot.load_w, 0);
    if (last && !last.closed) {
      last.end = slot.end;
      last.energy += power / 4;
      last.pv += Math.min(power, surplus) / 4;
    } else {
      windows.push({ start: slot.start, end: slot.end, energy: power / 4, pv: Math.min(power, surplus) / 4, closed: false });
    }
  }
  return windows.map((w) => ({
    ...w,
    source: w.pv >= w.energy * 0.8 ? t("aus PV-Überschuss") : w.pv > 0 ? t("teils PV, teils Netz/Speicher") : t("aus Netz/Speicher"),
  }));
}

/** A fixed color per controllable consumer, in the order of the plan; load and PV keep the first two. */
function consumerColors(flex) {
  return new Map((flex ?? []).map((f, i) => [f.device_id, SERIES_COLORS[i + 2] ?? UNASSIGNED_COLOR]));
}

/** % axis up to 100; above it (e.g. a tank hotter than its target) it grows and 100 % is drawn as line. */
function percentAxis(highest) {
  const over = highest > 100;
  return {
    formatY: (v) => formatValue(v, "%"),
    yMax: over ? highest : 100,
    limits: over ? [{ value: 100, label: "100 %" }] : [],
  };
}

/** Group consecutive slots with the same action. */
/** Reference lines for the SoC chart: full, backup reserve (if active) and discharge limit. */
function socLimits(battery) {
  if (!battery) return [];
  const pct = (wh) => `${Math.round((wh / battery.capacity_wh) * 100)} %`;
  const limits = [{ value: battery.capacity_wh, label: t("voll · {energy}", { energy: formatValue(battery.capacity_wh, "Wh") }) }];
  if (battery.limit_wh && battery.limit_wh < battery.capacity_wh && !battery.full_charge_at) {
    limits.push({ value: battery.limit_wh, label: t("Ladegrenze {pct}", { pct: pct(battery.limit_wh) }) });
  }
  if (battery.reserve_wh > battery.min_soc_wh) {
    limits.push({ value: battery.reserve_wh, label: t("Notstromreserve {pct}", { pct: pct(battery.reserve_wh) }) });
  }
  if (battery.min_soc_wh > 0) {
    limits.push({ value: battery.min_soc_wh, label: t("Entladegrenze {pct}", { pct: pct(battery.min_soc_wh) }) });
  }
  return limits;
}

/** Runs of equal actions; with several batteries a run ends when any battery's action changes. */
function segments(slots, batteries = []) {
  const several = batteries.length > 1;
  const key = (slot) => (several ? batteries.map((b) => slot.batteries?.[b.id]?.action).join("|") : slot.action);
  const result = [];
  for (const slot of slots) {
    const last = result.at(-1);
    if (last && last.key === key(slot)) {
      last.end = slot.end;
      last.slots.push(slot);
    } else {
      result.push({
        key: key(slot),
        action: slot.action,
        start: slot.start,
        end: slot.end,
        slots: [slot],
        batteries: several
          ? batteries.map((b) => ({ name: b.name, action: slot.batteries?.[b.id]?.action ?? "auto", power: slot.batteries?.[b.id]?.action_power_w }))
          : [],
      });
    }
  }
  return result;
}

/** "Label · power" of a segment, per battery when several are planned. */
function segmentLabel(segment) {
  const one = (action, power) => `${ACTIONS[action].label}${power ? ` · ${formatValue(Math.abs(power), "W")}` : ""}`;
  return segment.batteries.length
    ? segment.batteries.map((b) => `${b.name}: ${one(b.action, b.power)}`).join(" · ")
    : one(segment.action, segment.slots[0].action_power_w);
}

class SunflowPlan extends LitElement {
  static properties = {
    _plan: { state: true },
    _loading: { state: true },
    _error: { state: true },
    _history: { state: true },
    _historyHours: { state: true },
    _segmentsOpen: { state: true },
    _shift: { state: true },
    _pvAhead: { state: true },
    _focus: { state: true },
    _levelFocus: { state: true },
    _showTotal: { state: true },
    _showRawPv: { state: true },
    _rawPast: { state: true },
    _historyError: { state: true },
    _scales: { state: true },
  };

  constructor() {
    super();
    this._plan = null;
    this._loading = false;
    this._error = null;
    this._history = null;
    // Width of the chart window; it starts a third of it in the past (shifted by _shift hours)
    this._historyHours = 48;
    this._shift = 0;
    this._pvAhead = [];
    this._pvAheadRaw = [];
    this._rawPast = [];
    // Chosen axis maximum per chart; peaks otherwise squeeze everything else to the bottom
    this._scales = {};
    this._timer = null;
    try {
      this._showTotal = localStorage.getItem(TOTAL_KEY) === "1";
      this._showRawPv = localStorage.getItem(RAW_PV_KEY) === "1";
    } catch {
      this._showTotal = false;
      this._showRawPv = false;
    }
    this._segmentsOpen = isOpen("plan.segments");
  }

  connectedCallback() {
    super.connectedCallback();
    this._load();
    this._stopPolling = poll(() => this._load(), REFRESH_MS);
  }

  firstUpdated() {
    // One listener for all charts: dragging any of them moves the shared time window
    this.renderRoot.addEventListener("pointerdown", (e) => this._dragStart(e));
    // A drag ends with a click on the chart (e.g. on a legend or axis label): it must not count
    this.renderRoot.addEventListener("click", (e) => {
      if (this._dragged) {
        e.stopPropagation();
        e.preventDefault();
        this._dragged = false;
      }
    }, true);
  }

  _dragStart(e) {
    const chart = e.composedPath().find((el) => el instanceof SVGSVGElement && el.classList.contains("chart"));
    if (!chart || e.button !== 0) return;
    const width = chart.getBoundingClientRect().width;
    if (!width) return;
    const startX = e.clientX;
    const startShift = this._shift;
    const hoursPerPx = this._historyHours / width;
    let dragging = false;
    this._dragged = false;
    const move = (ev) => {
      const dx = ev.clientX - startX;
      if (!dragging && Math.abs(dx) < DRAG_THRESHOLD_PX) return;
      if (!dragging) {
        dragging = true;
        this.toggleAttribute("dragging", true);
      }
      // Dragging to the right shows earlier hours, like moving a sheet of paper
      this._shift = this._clampShift(startShift - dx * hoursPerPx);
    };
    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      if (!dragging) return;
      this._dragged = true;
      this.toggleAttribute("dragging", false);
      // Whole quarter hours: the slots and the history's buckets
      this._shift = this._clampShift(Math.round(this._shift * 4) / 4);
      Promise.all([this._loadHistory(), this._loadPvAhead(), this._loadRawPast()]);
    };
    // On the window: the pointer may leave the chart before the drag starts
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  }

  /** The shift kept within what history and forecasts cover. */
  _clampShift(shift) {
    const past = this._historyHours / 3;
    const earliest = -MAX_BACK_DAYS * 24 + past;
    const latest = MAX_AHEAD_DAYS * 24 + past - this._historyHours;
    return Math.min(Math.max(shift, earliest), latest);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._stopPolling();
  }

  async _load(fresh = false) {
    this._loading = true;
    // The measured history comes from Home Assistant's database; without it the plan still shows
    const history = this._loadHistory();
    this._loadPvAhead();
    this._loadRawPast();
    try {
      this._plan = await (fresh ? api.plan() : api.latestPlan());
      this._error = null;
    } catch (err) {
      this._error = t("Plan konnte nicht berechnet werden: {error}", { error: err.message });
    } finally {
      await history;
      this._loading = false;
    }
  }

  render() {
    const plan = this._plan;
    return html`
      <div class="heading">
        <div>
          <h1>${t("Planung")}</h1>
          <p class="muted">${t("Links von „jetzt“ gemessene Werte, rechts Prognosen und die geplante Steuerung. Eingriffe im Tab „Steuerung“, Voreinstellungen unter „Einstellungen → Planung“.")}
            ${plan ? this._controlNote(plan) : nothing}</p>
        </div>
        <button class="secondary" ?disabled=${this._loading} @click=${() => this._load(true)}>
          ${this._loading ? t("Berechne …") : t("Neu berechnen")}
        </button>
      </div>
      ${this._error ? html`<div class="alert error" role="alert">${this._error}</div>` : nothing}
      ${plan === null && !this._error ? html`<p class="muted">${t("Berechne Plan …")}</p>` : nothing}
      ${plan ? this._renderPlan(plan) : nothing}
    `;
  }

  _renderPlan(plan) {
    const slots = plan.slots;
    if (!slots.length) {
      return html`${plan.warnings.map((w) => html`<div class="alert warn">⚠ ${w}</div>`)}
        <p class="muted">${t("Kein Plan möglich ({status}).", { status: plan.status })}</p>`;
    }
    const segs = segments(slots, plan.batteries ?? []);
    const now = segs[0];
    const history = this._history?.points ?? [];
    const capacity =
      plan.battery?.capacity_wh || Math.max(...slots.map((s) => s.soc_wh), ...history.map((p) => p.soc_wh ?? 0)) || 1;
    // Measured up to the last full 5 minutes, which lie inside the plan's first quarter hour
    const measuredEnd = history.length ? new Date(history.at(-1).end) : null;
    const nowDate = measuredEnd && measuredEnd > new Date(slots[0].start) ? measuredEnd : new Date(slots[0].start);
    const curtailed = slots.some((s) => (s.curtail_w ?? 0) > 0);
    // Battery power in the grid chart: + charging, − discharging; several batteries one by one
    const batteryList = plan.batteries ?? [];
    const batteryColor = (i) => SERIES_COLORS[3 + i] ?? UNASSIGNED_COLOR;
    const battery = !plan.battery
      ? []
      : batteryList.length > 1
        ? batteryList.flatMap((b, i) => [
            {
              name: t("{name} gemessen", { name: b.name }),
              color: batteryColor(i),
              points: history.map((p) => ({ start: p.start, end: p.end, value: p.battery_powers?.[b.id] ?? null })),
            },
            {
              name: t("{name} geplant", { name: b.name }),
              color: batteryColor(i),
              dashed: true,
              points: slots.map((s) => {
                const share = s.batteries?.[b.id];
                return { start: s.start, end: s.end, value: share ? share.charge_w - share.discharge_w : null };
              }),
            },
          ])
        : [
            { name: t("Speicher gemessen"), color: SERIES_COLORS[3], points: this._past("battery_w") },
            { name: t("Speicher geplant"), color: SERIES_COLORS[3], dashed: true, points: slots.map((s) => ({ start: s.start, end: s.end, value: s.charge_w - s.discharge_w })) },
          ];
    const hasLoadpoints =
      history.some((p) => p.loadpoint_w !== null && p.loadpoint_w !== undefined) || Boolean(plan.flex?.length);
    const colors = consumerColors(plan.flex);
    const consumers = (plan.flex ?? []).map((f) => ({ key: f.device_id, name: f.name, color: colors.get(f.device_id) }));
    return html`
      ${plan.warnings.map((w) => html`<div class="alert warn">⚠ ${w}</div>`)}
      ${plugInHints(plan.plug_in_hints)}
      <div class="tiles">
        <div class="tile now">
          <div class="small muted">${t("Geplante Steuerung jetzt (bis {time})", { time: time.format(new Date(now.end)) })}</div>
          ${now.batteries.length
            ? html`<ul class="now-batteries">${now.batteries.map((b) => html`<li>
                <span class="small muted">${b.name}</span>
                <span class="value-small" title=${ACTIONS[b.action].hint}>${ACTIONS[b.action].label}${b.power ? ` · ${formatValue(Math.abs(b.power), "W")}` : ""}</span>
              </li>`)}</ul>`
            : html`<div class="value">${ACTIONS[now.action].label}</div>
              <div class="small muted">${this._actionHint(now, plan)}</div>`}
          ${this._renderActual(plan, now)}
        </div>
        <div class="tile">
          <div class="small muted">${t("Erwartete Ersparnis (nächste {hours} h)", { hours: Math.round(slots.length / 4) })}</div>
          <div class="value">${euro.format(plan.savings ?? 0)}</div>
          <div class="small muted">${t("gegenüber reinem Eigenverbrauch")}</div>
        </div>
        <div class="tile">
          <div class="small muted">${plan.alternative_cost || plan.baseline_alternative_cost ? t("Erwartete Energiekosten") : t("Erwartete Stromkosten")}</div>
          <div class="value">${euro.format(plan.cost)}</div>
          <div class="small muted">${t("ohne Sunflow {cost}", { cost: euro.format(plan.baseline_cost) })}${plan.alternative_cost || plan.baseline_alternative_cost
            ? html` · ${t("inkl. Ersatzheizung {planned} bzw. {baseline}", { planned: euro.format(plan.alternative_cost), baseline: euro.format(plan.baseline_alternative_cost) })}`
            : nothing}</div>
        </div>
        <div class="tile">
          <div class="small muted">${t("Erwarteter Ladestand am Ende")}</div>
          <div class="value">${formatValue(slots.at(-1).soc_wh, "Wh")}</div>
          <div class="small muted">${dayTime.format(new Date(slots.at(-1).end))}</div>
        </div>
      </div>

      <section>
        <h2>
          <button class="toggle" aria-expanded=${this._segmentsOpen} @click=${() => this._toggleSegments()}>
            <span class="caret" aria-hidden="true">${this._segmentsOpen ? "▾" : "▸"}</span>${t("Geplante Steuerung")}
          </button>
          ${this._controlTag(plan, plan.battery_device)}
        </h2>
        ${this._segmentsOpen
          ? nothing
          : html`<p class="small muted">${tn(segs.length, "{count} Abschnitte", { count: segs.length })}${segs[1]
            ? ` · ${t("als Nächstes ab {time}: {action}", { time: dayTime.format(new Date(segs[1].start)), action: segmentLabel(segs[1]) })}`
            : ""}</p>`}
        ${this._segmentsOpen ? html`<ol class="timeline">
          ${segs.map(
            (s) => html`
              <li class="seg ${s.action}">
                <span class="when">${dayTime.format(new Date(s.start))} – ${time.format(new Date(s.end))}</span>
                <span class="what">${segmentLabel(s)}</span>
                <span class="soc small muted">${t("Speicher {from} → {to}", { from: formatValue(s.slots[0].soc_wh, "Wh"), to: formatValue(s.slots.at(-1).soc_wh, "Wh") })}</span>
              </li>
            `,
          )}
        </ol>` : nothing}
      </section>

      ${plan.flex?.length ? this._renderFlex(plan) : nothing}

      <section>
        <div class="section-head">
          <h2>${t("Verlauf und Prognose")}</h2>
          <div class="window-nav">
            <div class="range" role="group" aria-label=${t("Zeitraum")}>
              ${[24, 48, 168].map(
                (h) => html`<button class=${this._historyHours === h ? "active" : ""} aria-pressed=${this._historyHours === h}
                  @click=${() => this._setHistoryHours(h)}>${h === 168 ? t("7 Tage") : `${h} h`}</button>`,
              )}
            </div>
            <div class="range" role="group" aria-label=${t("Zeitraum verschieben")}>
              <button ?disabled=${!this._canShift(-1)} aria-label=${t("früher")} @click=${() => this._shiftWindow(-1)}>‹</button>
              <button class=${this._shift === 0 ? "active" : ""} @click=${() => this._shiftWindow(0)}>${t("Jetzt")}</button>
              <button ?disabled=${!this._canShift(1)} aria-label=${t("später")} @click=${() => this._shiftWindow(1)}>›</button>
            </div>
          </div>
        </div>
        <p class="small muted window-label">${dayTime.format(this._range.start)} – ${dayTime.format(this._range.end)}${this._range.end > new Date(slots.at(-1).end)
          ? ` · ${t("nach dem Planende nur die PV-Prognose")}`
          : ""} · ${t("zum Verschieben ein Diagramm seitlich ziehen")}</p>
        ${this._historyError
          ? html`<div class="alert warn">⚠ ${this._history
              ? t("Gemessener Verlauf nicht abrufbar ({error}) – angezeigt wird der zuletzt geladene.", { error: this._historyError })
              : t("Gemessener Verlauf nicht abrufbar ({error}) – die Diagramme zeigen nur die Planung.", { error: this._historyError })}</div>`
          : nothing}
        <div class="legend">
          <span class="legend-item"><span class="line solid"></span>${t("gemessen")}</span>
          <span class="legend-item"><span class="line dashed"></span>${t("Prognose / Planung")}</span>
          <span class="legend-item muted">${t("Klick auf eine Zahl der Leistungsachse skaliert bis dorthin, erneuter Klick zeigt wieder alles")}</span>
        </div>
      </section>

      ${this._renderLevels(plan, slots, capacity, nowDate)}

      <section>
        <h3>${t("PV-Erzeugung und Hausverbrauch")}</h3>
        <div class="legend">
          <span class="legend-item">${this._legendButton("pv", SERIES_COLORS[1], t("PV-Erzeugung"))}
            ${plan.pv_band?.some((b) => b[0] !== null && b[1] !== null)
              ? html`<span class="muted" title=${t("Solcast gibt neben dem Mittelwert einen vorsichtigen (p10) und einen günstigen Fall (p90) an; das Band zeigt den Bereich, korrigiert wie die Prognose.")}>
                  <span class="band-swatch" style="background:${SERIES_COLORS[1]}"></span>${t("Bandbreite p10–p90")}</span>`
              : nothing}
            ${plan.pv_correction
              ? html`<span class="muted">${t("(Prognose an die Anlage angepasst: Ø {pct} % aus {days} Tagen)", { pct: Math.round(plan.pv_correction.overall * 100), days: plan.pv_correction.days })}</span>`
              : nothing}</span>
          ${consumers.length
            ? consumers.map((c) => this._legendButton(c.key, c.color, c.name))
            : hasLoadpoints
              ? this._legendButton("consumers", UNASSIGNED_COLOR, t("steuerbare Verbraucher"))
              : nothing}
          <span class="legend-item">${this._legendButton("load", SERIES_COLORS[0], hasLoadpoints ? t("Hausverbrauch ohne steuerbare Verbraucher") : t("Hausverbrauch"))}
            <span class="muted">(${t("Prognose:")} ${plan.load_model
              ? t("gelerntes Modell mit Außentemperatur aus {days} Tagen, Abweichung {error} statt {average}", { days: plan.load_model.days, error: formatValue(plan.load_model.error_model, "W"), average: formatValue(plan.load_model.error_average, "W") })
              : html`${LOAD_QUALITY[plan.load_quality] ?? "–"}, ${t("{hours} h Historie", { hours: plan.load_history_hours })}${plan.load_scaled_to_kwh
              ? `, ${t("skaliert auf {kwh} kWh/Tag", { kwh: toDisplay(plan.load_scaled_to_kwh, "kWh") })}`
              : ""}${plan.load_separated_hours ? `, ${t("ohne steuerbare Verbraucher ({hours} h getrennt)", { hours: plan.load_separated_hours })}` : ""}`})</span></span>
          <span class="legend-item total-toggle">
            <input type="checkbox" id="show-raw-pv" aria-label=${t("Unkorrigierte Prognose einblenden")} .checked=${this._showRawPv}
              @change=${(e) => this._setShowRawPv(e.target.checked)}>
            ${this._showRawPv
              ? this._legendButton("raw", SERIES_COLORS[1], t("{name} (unkorrigiert)", { name: rawLabel(plan) }))
              : html`<label for="show-raw-pv">${t("{name} (unkorrigiert)", { name: rawLabel(plan) })}</label>`}
          </span>
          ${hasLoadpoints
            ? html`<span class="legend-item total-toggle">
                <input type="checkbox" id="show-total" aria-label=${t("Gesamtverbrauch einblenden")} .checked=${this._showTotal}
                  @change=${(e) => this._setShowTotal(e.target.checked)}>
                ${this._showTotal
                  ? this._legendButton("total", UNASSIGNED_COLOR, t("Gesamtverbrauch (mit steuerbaren Verbrauchern)"))
                  : html`<label for="show-total">${t("Gesamtverbrauch (mit steuerbaren Verbrauchern)")}</label>`}
              </span>`
            : nothing}
        </div>
        ${lineChart(
          this._focused([
            { key: "load", name: t("Verbrauch gemessen"), color: SERIES_COLORS[0], points: this._pastBaseLoad() },
            { key: "load", name: t("Verbrauch Prognose"), color: SERIES_COLORS[0], dashed: true, points: this._future(slots, "load_w") },
            { key: "pv", name: t("PV gemessen"), color: SERIES_COLORS[1], points: this._past("pv_w") },
            ...(this._showRawPv
              ? [{ key: "raw", name: t("{name} unkorrigiert", { name: rawLabel(plan) }), color: SERIES_COLORS[1], thin: true, points: this._rawPv(plan, slots) }]
              : []),
            ...(consumers.length
              ? consumers.flatMap((c) => [
                  { key: c.key, name: t("{name} gemessen", { name: c.name }), color: c.color, points: this._pastConsumer(c.key) },
                  {
                    key: c.key,
                    name: t("{name} geplant", { name: c.name }),
                    color: c.color,
                    dashed: true,
                    points: slots.map((s) => ({ start: s.start, end: s.end, value: s.flex_w?.[c.name] ?? 0 })),
                  },
                ])
              : hasLoadpoints
                ? [{ key: "consumers", name: t("Verbraucher gemessen"), color: UNASSIGNED_COLOR, points: this._past("loadpoint_w") }]
                : []),
            {
              key: "pv",
              name: t("PV Prognose"),
              color: SERIES_COLORS[1],
              dashed: true,
              points: [...this._future(slots, "pv_w"), ...this._pvAhead.filter((p) => new Date(p.start) >= new Date(slots.at(-1).end))],
            },
            ...(hasLoadpoints && this._showTotal
              ? [
                  { key: "total", name: t("Gesamtverbrauch gemessen"), color: UNASSIGNED_COLOR, points: this._past("load_w") },
                  {
                    key: "total",
                    name: t("Gesamtverbrauch geplant"),
                    color: UNASSIGNED_COLOR,
                    dashed: true,
                    points: slots.map((s) => ({
                      start: s.start,
                      end: s.end,
                      value: s.load_w + Object.values(s.flex_w ?? {}).reduce((a, b) => a + b, 0),
                    })),
                  },
                ]
              : []),
          ]),
          {
            label: t("PV-Erzeugung und Hausverbrauch, gemessen und Prognose"),
            formatY: (v) => formatValue(v, "W"),
            now: nowDate,
            range: this._range,
            ...this._scaling("power"),
            bands: !this._focus || this._focus === "pv"
              ? [{
                  name: t("PV Bandbreite (p10–p90)"),
                  color: SERIES_COLORS[1],
                  points: slots.map((s, i) => ({ start: s.start, end: s.end, low: plan.pv_band?.[i]?.[0], high: plan.pv_band?.[i]?.[1] })),
                }]
              : [],
          },
        )}
      </section>

      <section>
        <h3>${t("Netzbezug und Einspeisung")}</h3>
        <div class="legend">
          <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[0]}"></span>${t("Netzbezug")}</span>
          <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[1]}"></span>${t("Einspeisung")}</span>
          ${curtailed
            ? html`<span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[2]}"></span>${t("PV abgeregelt")}</span>`
            : nothing}
          ${battery.length && batteryList.length > 1
            ? batteryList.map((b, i) => html`<span class="legend-item"><span class="swatch" style="background:${batteryColor(i)}"></span>${t("{name} (+ laden, − entladen)", { name: b.name })}</span>`)
            : battery.length
              ? html`<span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[3]}"></span>${t("Speicher (+ laden, − entladen)")}</span>`
              : nothing}
          <span class="legend-item muted">${t("erwartet: Bezug {import}, Einspeisung {export}", { import: formatValue(energy(slots, "import_w"), "Wh"), export: formatValue(energy(slots, "export_w"), "Wh") })}${curtailed
              ? `, ${t("abgeregelt {energy}", { energy: formatValue(energy(slots, "curtail_w"), "Wh") })}`
              : ""}</span>
        </div>
        ${lineChart(
          [
            { name: t("Bezug gemessen"), color: SERIES_COLORS[0], points: this._pastGrid((g) => Math.max(g, 0)) },
            { name: t("Bezug erwartet"), color: SERIES_COLORS[0], dashed: true, points: this._future(slots, "import_w") },
            { name: t("Einspeisung gemessen"), color: SERIES_COLORS[1], points: this._pastGrid((g) => Math.max(-g, 0)) },
            { name: t("Einspeisung erwartet"), color: SERIES_COLORS[1], dashed: true, points: this._future(slots, "export_w") },
            ...(curtailed
              ? [{ name: t("PV abgeregelt"), color: SERIES_COLORS[2], dashed: true, points: this._future(slots, "curtail_w") }]
              : []),
            ...battery,
          ],
          {
            label: t("Netzbezug und Einspeisung, gemessen und erwartet"),
            formatY: (v) => formatValue(v, "W"),
            // Discharging is drawn below zero
            yMin: Math.min(0, ...battery.flatMap((b) => b.points.map((p) => p.value ?? 0))),
            now: nowDate,
            range: this._range,
            ...this._scaling("grid"),
          },
        )}
      </section>

      ${this._renderCostCurve(plan, slots)}

      <section>
        <h3>${t("Bezugspreis")} <span class="tag">${t("Vorschau")}</span></h3>
        ${priceChart([...this._pastPrices(slots), ...slots.map((s) => ({ start: s.start, end: s.end, price: s.price, estimated: s.price_estimated }))], t("Bezugspreis im Plan"), {
          // Same time axis as the charts above
          range: this._range,
          now: nowDate,
        })}
      </section>

    `;
  }

  _renderFlex(plan) {
    const colors = consumerColors(plan.flex);
    const fmt = (v, unit) => (v === null || v === undefined ? "–" : `${decimal(Math.round(v * 10) / 10)} ${unit}`);
    return html`
      <section>
        <h2>${t("Steuerbare Verbraucher")}</h2>
        <div class="flex-list">
          ${plan.flex.map((f) => {
            const windows = flexWindows(plan.slots, f.name);
            return html`
              <div class="flex-card">
                <div class="flex-head">
                  <strong><span class="swatch inline" style="background:${colors.get(f.device_id)}"></span>${f.name}${this._controlTag(plan, f.device_id)}</strong>
                  <span class="small muted">${f.kind === "vehicle" && f.vehicle ? f.vehicle : { heater: t("Warmwasser"), vehicle: t("Fahrzeug") }[f.kind] ?? t("Verbraucher")}</span>
                </div>
                <div class="small">
                  ${f.kind === "load" ? nothing : html`${fmt(f.current, f.unit)} → ${fmt(f.target, f.unit)}`}
                  ${f.need_wh > 0 ? html` · ${t("Bedarf {energy}", { energy: formatValue(f.need_wh, "Wh") })}` : nothing}
                  ${f.deadline
                    ? html` · ${t("bis {time}", { time: dayTime.format(new Date(f.deadline)) })}${f.learned_departure ? ` ${t("(übliche Abfahrt, gelernt)")}` : ""}`
                    : nothing}
                  ${f.daily_wh > 0 ? html` · ${t("danach täglich {energy}", { energy: formatValue(f.daily_wh, "Wh") })}` : nothing}
                </div>
                ${f.reason
                  ? html`<div class="small muted">${t("Nicht eingeplant: {reason}", { reason: f.reason })}</div>`
                  : html`
                      <div class="small">${t("Geplant {energy}", { energy: formatValue(f.planned_wh, "Wh") })}${f.unmet_wh > 50
                        ? html` · <span class="warn-text">${t("{energy} nicht erfüllbar", { energy: formatValue(f.unmet_wh, "Wh") })}</span>`
                        : nothing}</div>
                      <ul class="windows small">
                        ${windows.map(
                          (w) => html`<li>${dayTime.format(new Date(w.start))}–${time.format(new Date(w.end))} ·
                            ${formatValue(w.energy, "Wh")} · ${w.source}</li>`,
                        )}
                      </ul>
                    `}
              </div>
            `;
          })}
        </div>
      </section>
    `;
  }

  _renderCostCurve(plan, slots) {
    const baseline = plan.baseline_slot_costs ?? [];
    if (baseline.length !== slots.length) return nothing;
    let planned = 0;
    let without = 0;
    const withSunflow = [];
    const withoutSunflow = [];
    slots.forEach((s, i) => {
      planned += (s.price * s.import_w - s.feed_in * s.export_w) * 0.25 / 1000;
      without += baseline[i];
      withSunflow.push({ start: s.start, end: s.end, value: planned });
      withoutSunflow.push({ start: s.start, end: s.end, value: without });
    });
    const lowest = Math.min(0, ...withSunflow.map((p) => p.value), ...withoutSunflow.map((p) => p.value));
    return html`
      <section>
        <h3>${t("Kostenverlauf")} <span class="tag">${t("Vorschau")}</span></h3>
        <div class="legend">
          <span class="legend-item"><span class="swatch" style="background:${SERIES_COLORS[0]}"></span>${t("mit Sunflow {cost}", { cost: euro.format(planned) })}</span>
          <span class="legend-item"><span class="swatch" style="background:${UNASSIGNED_COLOR}"></span>${t("reiner Eigenverbrauch {cost}", { cost: euro.format(without) })}</span>
          <span class="muted small">${t("aufsummierte Stromkosten (Bezug minus Einspeisung)")}${plan.alternative_cost || plan.baseline_alternative_cost
            ? `, ${t("ohne Ersatzheizung ({planned} bzw. {baseline})", { planned: euro.format(plan.alternative_cost), baseline: euro.format(plan.baseline_alternative_cost) })}`
            : ""}</span>
        </div>
        ${lineChart(
          [
            { name: t("mit Sunflow"), color: SERIES_COLORS[0], points: withSunflow },
            { name: t("reiner Eigenverbrauch"), color: UNASSIGNED_COLOR, dashed: true, points: withoutSunflow },
          ],
          // Same time axis as the charts above; the sum starts now
          { label: t("Aufsummierte Stromkosten mit und ohne Sunflow"), formatY: (v) => euro.format(v), yMin: lowest, now: new Date(slots[0].start), range: this._range },
        )}
      </section>
    `;
  }

  /** What the current action means; a limit is named with its power. */
  _actionHint(segment, plan) {
    const power = segment.slots[0]?.action_power_w;
    if (!power) return ACTIONS[segment.action].hint;
    const value = formatValue(Math.abs(power), "W");
    if (segment.action === "limit_discharge" && plan?.discharge_budget) {
      const energy = formatValue(Math.abs(power) / 4, "Wh");
      return t("Speicher gibt je Viertelstunde bis zu {energy} ab und deckt dabei auch Spitzen ganz, danach hält er", { energy });
    }
    if (segment.action === "limit_discharge") return t("Speicher entlädt höchstens {power}, darüber hinaus kommt der Bedarf aus dem Netz", { power: value });
    if (segment.action === "limit_charge") return t("Speicher lädt höchstens {power} aus PV, der übrige Überschuss wird eingespeist", { power: value });
    return ACTIONS[segment.action].hint;
  }

  _renderActual(plan, now) {
    const actual = plan.actual;
    if (!actual || actual.battery_w === null || actual.battery_w === undefined) return nothing;
    const power = formatValue(Math.abs(actual.battery_w), "W");
    // The source goes by the last minutes, like the deviation below
    const fromGrid = (actual.recent_grid_w ?? actual.grid_w ?? 0) > 100;
    const what =
      actual.battery_w > 100 ? (fromGrid ? t("lädt {power} aus dem Netz", { power }) : t("lädt {power} aus PV", { power }))
      : actual.battery_w < -100 ? t("entlädt {power}", { power })
      : t("ruht");
    // Limits count as their basic behaviour; the measurement cannot tell them apart
    const planned = { limit_charge: "auto", limit_discharge: "limit_discharge" }[now.action] ?? now.action;
    // A discharge limit above the demand never bites: discharging at most the limit keeps to the plan
    const battery = actual.recent_battery_w ?? actual.battery_w;
    const limit = Math.abs(now.slots?.[0]?.action_power_w ?? 0);
    // With the energy per slot the battery covers peaks fully and holds once it is used
    const withinLimit = now.action === "limit_discharge" && (plan.discharge_budget
      ? ["auto", "hold"].includes(actual.action)
      : actual.action === "auto" && (!limit || -battery <= limit + Math.max(100, 0.1 * limit)));
    const differs = actual.action && actual.action !== planned && !withinLimit;
    return html`<div class="small ${differs ? "warn-text" : "muted"}">
      ${t("Tatsächlich: Speicher {what}", { what })}${differs
        ? plan.controlling
          ? ` – ${t("weicht seit einigen Minuten vom Plan ab (Sunflow steuert; Grenzen des Wechselrichters oder eine Viertelstunde Verzögerung)")}`
          : ` – ${t("nicht von Sunflow, eine andere Steuerung ist aktiv (Sunflow plant nur)")}`
        : ""}${differs && actual.recent_grid_w !== null && actual.recent_grid_w !== undefined
        ? ` · ${t("Ø letzte Minuten: Netz {grid}, Speicher {battery}", { grid: formatValue(actual.recent_grid_w, "W"), battery: formatValue(actual.recent_battery_w, "W") })}`
        : ""}
    </div>`;
  }

  /** Whether the plan is carried out: nothing, everything, or which devices. */
  _controlNote(plan) {
    const controlled = new Set(plan.controlled ?? []);
    const names = new Map([[plan.battery_device, t("Hausspeicher")], ...(plan.flex ?? []).map((f) => [f.device_id, f.name])]);
    const planned = [...names.keys()].filter((id) => id);
    if (!controlled.size) return t("Sunflow steuert noch nichts – die Planung zeigt, was es tun würde.");
    if (planned.every((id) => controlled.has(id))) return t("Sunflow setzt den Plan um.");
    const doing = planned.filter((id) => controlled.has(id)).map((id) => names.get(id)).join(", ");
    return t("Sunflow setzt den Plan bei {devices} um; Geräte mit „nur Plan“ zeigen nur, was es tun würde.", { devices: doing });
  }

  /** "Only plan" for a device Sunflow does not control, "carried out" for one it does. */
  _controlTag(plan, deviceId) {
    if (!deviceId) return nothing;
    return (plan.controlled ?? []).includes(deviceId)
      ? html`<span class="tag on">${t("wird umgesetzt")}</span>`
      : html`<span class="tag">${t("nur Plan")}</span>`;
  }

  _toggleSegments() {
    this._segmentsOpen = !this._segmentsOpen;
    setOpen("plan.segments", this._segmentsOpen);
  }

  /** Prices of the recorded past (the tariff each slot was planned with), up to the plan. */
  _pastPrices(slots) {
    const planStart = slots.length ? new Date(slots[0].start) : null;
    return (this._history?.points ?? [])
      .filter((p) => p.price !== null && p.price !== undefined && (!planStart || new Date(p.end) <= planStart))
      .map((p) => ({ start: p.start, end: p.end, price: p.price }));
  }

  _past(key) {
    const points = (this._history?.points ?? []).map((p) => ({ start: p.start, end: p.end, value: p[key] }));
    return this._withLive(points, this._plan?.actual?.[key]);
  }

  _scaling(chart) {
    return {
      scaleMax: this._scales[chart] ?? null,
      onScale: (value) => (this._scales = { ...this._scales, [chart]: value }),
    };
  }

  _setShowRawPv(show) {
    this._showRawPv = show;
    if (!show && this._focus === "raw") this._focus = null;
    try {
      localStorage.setItem(RAW_PV_KEY, show ? "1" : "0");
    } catch {
      // Not remembered, the switch still works for this visit
    }
    if (show) this._loadRawPast();
  }

  /** The forecast as its source delivers it: recorded in the past, from the plan ahead, the longer one after the plan. */
  _rawPv(plan, slots) {
    const ahead = slots.map((s, i) => ({ start: s.start, end: s.end, value: plan.raw_pv?.[i] ?? null }));
    const planStart = new Date(slots[0].start);
    const planEnd = new Date(slots.at(-1).end);
    return [
      ...this._rawPast.filter((p) => new Date(p.end) <= planStart),
      ...ahead,
      ...this._pvAheadRaw.filter((p) => new Date(p.start) >= planEnd),
    ];
  }

  /** The forecast the shadow mode recorded for each past slot (before learning). */
  async _loadRawPast() {
    const hours = Math.ceil((Date.now() - this._range.start) / 3600_000);
    if (!this._showRawPv || hours <= 0) return;
    try {
      const shadow = await api.shadow(Math.min(hours, 90 * 24));
      this._rawPast = shadow.rows
        .filter((r) => r.forecast_pv_w !== null && r.forecast_pv_w !== undefined)
        .map((r) => ({ start: r.start, end: new Date(new Date(r.start).getTime() + 15 * 60_000).toISOString(), value: r.forecast_pv_w }));
    } catch {
      this._rawPast = [];
    }
  }

  _setShowTotal(show) {
    this._showTotal = show;
    if (!show && this._focus === "total") this._focus = null;
    try {
      localStorage.setItem(TOTAL_KEY, show ? "1" : "0");
    } catch {
      // Not remembered, the switch still works for this visit
    }
  }

  /** Like the forecast: the house without the controllable consumers, which have their own lines. */
  _pastBaseLoad() {
    const base = (load, consumers) => (load === null || load === undefined ? null : Math.max(load - (consumers ?? 0), 0));
    const points = (this._history?.points ?? []).map((p) => ({ start: p.start, end: p.end, value: base(p.load_w, p.loadpoint_w) }));
    const actual = this._plan?.actual;
    return this._withLive(points, actual ? base(actual.load_w, actual.loadpoint_w) : null);
  }

  _pastConsumer(deviceId) {
    return (this._history?.points ?? []).map((p) => ({
      start: p.start,
      end: p.end,
      value: p.consumers ? (p.consumers[deviceId] ?? null) : null,
    }));
  }

  /** Storage levels: battery, cars and tanks in % on one axis; a legend click shows one in kWh or °C. */
  _renderLevels(plan, slots, capacity, now) {
    const history = this._history?.points ?? [];
    const pastOf = (key) => history.map((p) => ({ start: p.start, end: p.end, value: p.levels?.[key] ?? null }));
    const futureOf = (flex) => (flex?.levels ?? []).map((value, i) => ({ start: slots[i].start, end: slots[i].end, value }));
    // A car in %, as energy in its battery when shown alone; its target while it is at a wallbox
    const car = (key, name, capacityWh, flex) => ({
      key,
      name,
      past: pastOf(key),
      future: futureOf(flex),
      pct: (v) => v,
      native: capacityWh ? (v) => (v / 100) * capacityWh : (v) => v,
      format: capacityWh ? (v) => formatValue(v, "Wh") : (v) => formatValue(v, "%"),
      scale: {
        yMax: (capacityWh || 100) * 1.05,
        limits: flex?.target
          ? [{ value: capacityWh ? (flex.target / 100) * capacityWh : flex.target, label: t("Ziel {value}", { value: formatValue(flex.target, "%") }) }]
          : [],
      },
    });
    const tank = (f) => {
      const [low, high] = f.level_range;
      const past = pastOf(f.device_id);
      const future = futureOf(f);
      const values = [...past, ...future].map((p) => p.value).filter((v) => v !== null && v !== undefined);
      return {
        key: f.device_id,
        name: f.name,
        past,
        future,
        // 0 % at cold water, 100 % at the target
        pct: (v) => Math.max(((v - low) / (high - low)) * 100, 0),
        native: (v) => v,
        format: (v) => formatValue(v, "°C"),
        scale: {
          yMin: Math.floor(Math.min(low, ...values) / 5) * 5,
          yMax: Math.max(high, ...values) + 2,
          limits: [{ value: high, label: t("Ziel {value}", { value: formatValue(high, "°C") }) }],
        },
      };
    };
    const flex = plan.flex ?? [];
    const batteries = plan.batteries ?? [];
    const several = batteries.length > 1;
    // Several batteries: their sum and each one; care limits and reserve belong to the first
    const each = several
      ? batteries.map((b, i) => ({
          key: `battery:${b.id}`,
          name: b.name,
          past: history.map((p) => ({ start: p.start, end: p.end, value: p.batteries?.[b.id] ?? null })),
          future: this._futureLevel(slots, (slot) => slot.batteries?.[b.id]?.soc_wh ?? null),
          pct: (wh) => (wh / b.capacity_wh) * 100,
          native: (wh) => wh,
          format: (wh) => formatValue(wh, "Wh"),
          scale: {
            yMax: b.capacity_wh * 1.05,
            limits: i === 0 ? socLimits({ ...plan.battery, capacity_wh: b.capacity_wh, min_soc_wh: b.min_soc_wh }) : socLimits(b),
          },
        }))
      : [];
    const levels = [
      {
        key: "battery",
        name: several ? t("Speicher gesamt") : t("Hausspeicher"),
        past: this._past("soc_wh"),
        // The measured SoC is the planned battery's; with several only their sum is planned
        future: this._futureLevel(slots, (slot) => slot.soc_wh, several ? null : this._plan?.actual?.soc_wh),
        pct: (wh) => (wh / capacity) * 100,
        native: (wh) => wh,
        format: (wh) => formatValue(wh, "Wh"),
        // Charge limit, reserve and discharge limit belong to the battery only
        scale: { yMax: capacity * 1.05, limits: several ? [] : socLimits(plan.battery) },
      },
      ...each,
      ...(plan.vehicles ?? []).map((v) => car(v.key, v.name, v.capacity_wh, flex.find((f) => f.vehicle_id === v.id))),
      // A wallbox without assigned cars shows the SoC it knows
      ...flex.filter((f) => f.kind === "vehicle" && f.level_range && !f.vehicle_id).map((f) => car(f.device_id, f.name, f.capacity_wh, f)),
      ...flex.filter((f) => f.kind === "heater" && f.level_range).map(tank),
    ]
      .filter((l) => [...l.past, ...l.future].some((p) => p.value !== null && p.value !== undefined))
      .map((l, i) => ({ ...l, color: SERIES_COLORS[i] ?? UNASSIGNED_COLOR }));
    const focus = levels.find((l) => l.key === this._levelFocus);
    const convert = (points, f) => points.map((p) => ({ ...p, value: p.value === null || p.value === undefined ? null : f(p.value) }));
    const series = (focus ? [focus] : levels).flatMap((l) => {
      const f = focus ? l.native : l.pct;
      return [
        { name: t("{name} gemessen", { name: l.name }), color: l.color, points: convert(l.past, f) },
        { name: t("{name} erwartet", { name: l.name }), color: l.color, dashed: true, points: convert(l.future, f) },
      ];
    });
    const options = focus
      ? { formatY: focus.format, yMin: focus.scale.yMin ?? 0, yMax: focus.scale.yMax, limits: focus.scale.limits }
      : percentAxis(Math.max(...series.flatMap((x) => x.points.map((p) => p.value ?? 0))));
    return html`<section>
      <h3>${t("Ladestand")}</h3>
      ${levels.length > 1
        ? html`<div class="legend">
            ${levels.map((l) => {
              const active = this._levelFocus === l.key;
              return html`<button type="button" class="legend-item legend-button ${active ? "active" : ""}" aria-pressed=${active}
                title=${active ? t("Alle in % zeigen") : t("Nur diesen in seiner Einheit zeigen")}
                @click=${() => (this._levelFocus = active ? null : l.key)}>
                <span class="swatch" style="background:${l.color}"></span>${l.name}</button>`;
            })}
            <span class="legend-item muted">${focus ? t("Klick zeigt wieder alle in %") : t("alle in % · Klick zeigt einen in kWh bzw. °C")}</span>
          </div>`
        : nothing}
      ${lineChart(series, { label: t("Ladestand gemessen und erwartet"), now, range: this._range, ...options })}
      ${(plan.buffers ?? []).slice(0, 2).map((b) => html`<p class="small muted">${t(
        "Puffer: Um {time} sollen noch {energy} über der Entladegrenze übrig sein – für einen Tag mit mehr Verbrauch oder weniger Sonne als erwartet (Tagesverbrauch schwankt um etwa {spread}).",
        {
          time: `${new Date(b.at).toLocaleDateString(locale(), { weekday: "short" })} ${time.format(new Date(b.at))}`,
          energy: formatValue(b.wh, "Wh"),
          spread: formatValue((plan.load_spread ?? 0.15) * 100, "%"),
        },
      )}</p>`)}
      ${(plan.headrooms ?? []).slice(0, 2).map((h) => html`<p class="small muted">${t(
        "Platz für mehr Sonne: Um {time} soll der Speicher höchstens {max} halten, damit {energy} mehr PV als geplant noch hineinpasst statt ins Netz zu gehen.",
        {
          time: `${new Date(h.at).toLocaleDateString(locale(), { weekday: "short" })} ${time.format(new Date(h.at))}`,
          max: formatValue(h.max_wh, "Wh"),
          energy: formatValue(h.wh, "Wh"),
        },
      )}</p>`)}
      ${(plan.vehicles ?? []).map((v) => {
        const at = flex.find((f) => f.vehicle_id === v.id && f.expected_arrival);
        return html`<p class="small muted">${v.name}: ${v.usage_days
          ? `${t("Verbrauch aus {days} Tagen gelernt (Ø {pct} am Tag)", { days: v.usage_days, pct: formatValue(v.daily_pct, "%") })}${at
            ? ` – ${t("unterwegs, zurück erwartet gegen {time} mit etwa {soc}; ab dann eingeplant.", { time: time.format(new Date(at.expected_arrival)), soc: formatValue(at.arrival_soc, "%") })}`
            : "."}`
          : t("Verbrauch noch nicht gelernt (braucht 14 Tage, an denen der Ladestand mindestens 20 Stunden aufgezeichnet ist) – geplant wird erst, wenn es angesteckt ist; bis dahin bleibt der erwartete Ladestand gleich.")}</p>`;
      })}
      ${flex.filter((f) => f.kind === "heater" && f.level_range).map((f) => html`<p class="small muted">${f.name}: ${f.usage_days
        ? t("Verbrauch je Stunde aus {days} Tagen gelernt – geheizt wird vor der gewohnten Hauptzeit.", { days: f.usage_days })
        : t("Verbrauch noch nicht gelernt (braucht eine Woche Temperatur und Heizleistung) – gleichmäßig über den Tag angenommen.")}${f.level_floor != null
        ? ` ${t("Eine andere Wärmequelle (z. B. Fernwärme) hält ihn bei etwa {temperature} – die Prognose fällt nicht darunter.", { temperature: formatValue(f.level_floor, "°C") })}`
        : ""}</p>`)}
    </section>`;
  }

  /** Legend entry that highlights its lines in the chart; a second click shows all again. */
  _legendButton(key, color, label) {
    const active = this._focus === key;
    return html`<button type="button" class="legend-item legend-button ${active ? "active" : ""}" aria-pressed=${active}
      title=${active ? t("Alle Linien zeigen") : t("Hervorheben")} @click=${() => (this._focus = active ? null : key)}>
      <span class="swatch" style="background:${color}"></span>${label}</button>`;
  }

  _focused(series) {
    if (!this._focus || !series.some((s) => s.key === this._focus)) return series;
    return series.map((s) => ({ ...s, dim: s.key !== this._focus }));
  }

  _pastGrid(transform) {
    const points = (this._history?.points ?? []).map((p) => ({
      start: p.start,
      end: p.end,
      value: p.grid_w === null ? null : transform(p.grid_w),
    }));
    const grid = this._plan?.actual?.grid_w;
    return this._withLive(points, grid === null || grid === undefined ? null : transform(grid));
  }

  /** The statistics lag behind by up to a quarter of an hour; the latest reading closes the gap to "now". */
  _withLive(points, value) {
    const time = this._plan?.actual?.time;
    if (value === null || value === undefined || !time) return points;
    const last = points.at(-1);
    const start = last ? new Date(last.end) : new Date(new Date(time).getTime() - 15 * 60_000);
    if (new Date(time) <= start) return points;
    return [...points, { start, end: new Date(time), value }];
  }

  _future(slots, key) {
    return slots.map((s) => ({ start: s.start, end: s.end, value: s[key] }));
  }

  /** A planned level holds at the end of its slot; from the level measured now on, not from the slot's start. */
  _futureLevel(slots, value, current = null) {
    const time = this._plan?.actual?.time;
    const now = current !== null && current !== undefined && time ? [{ start: time, end: time, value: current }] : [];
    return [...now, ...slots.map((s) => ({ start: s.end, end: s.end, value: value(s) }))];
  }

  /** The chart window: a third in the past, shifted by half windows. */
  get _range() {
    const hour = 3600_000;
    const start = new Date(Date.now() - (this._historyHours / 3) * hour + this._shift * hour);
    return { start, end: new Date(start.getTime() + this._historyHours * hour) };
  }

  _canShift(direction) {
    return this._clampShift(this._shift + direction * (this._historyHours / 2)) !== this._shift;
  }

  async _shiftWindow(direction) {
    this._shift = direction === 0 ? 0 : this._clampShift(this._shift + direction * (this._historyHours / 2));
    await Promise.all([this._loadHistory(), this._loadPvAhead(), this._loadRawPast()]);
  }

  async _setHistoryHours(hours) {
    this._historyHours = hours;
    this._shift = 0;
    await Promise.all([this._loadHistory(), this._loadPvAhead(), this._loadRawPast()]);
  }

  /** Measured values of the window's past part; keeps the last on failure (charts show it with a hint). */
  async _loadHistory() {
    const { start, end } = this._range;
    const until = new Date(Math.min(end.getTime(), Date.now()));
    const key = `${this._historyHours}/${this._shift}`;
    if (until <= start) {
      this._history = { points: [] };
      return;
    }
    try {
      const hours = Math.ceil((until - start) / 3600_000);
      const history = await api.history(hours, end.getTime() < Date.now() ? until : null);
      if (key === `${this._historyHours}/${this._shift}`) {
        this._history = history;
        this._historyError = null;
      }
    } catch (err) {
      if (key === `${this._historyHours}/${this._shift}`) this._historyError = err.message;
    }
  }

  /** PV forecast beyond the plan (prices end earlier than the forecast), with the learned correction. */
  async _loadPvAhead() {
    const end = this._range.end;
    const hours = Math.ceil((end - Date.now()) / 3600_000);
    if (hours <= MAX_PLAN_HOURS) {
      this._pvAhead = [];
      this._pvAheadRaw = [];
      return;
    }
    try {
      const data = await api.forecastSeries(Math.min(hours, MAX_AHEAD_DAYS * 24));
      const factor = this._plan?.pv_correction?.overall ?? 1;
      const sums = new Map();
      for (const plane of data.planes) {
        for (const p of plane.points) {
          const entry = sums.get(p.start) ?? { start: p.start, end: p.end, value: 0 };
          entry.value += p.power;
          sums.set(p.start, entry);
        }
      }
      this._pvAheadRaw = [...sums.values()].sort((a, b) => new Date(a.start) - new Date(b.start));
      this._pvAhead = this._pvAheadRaw.map((p) => ({ ...p, value: p.value * factor }));
    } catch {
      // Optional: the charts end with the plan then
      this._pvAhead = [];
    }
  }

  static styles = css`
    .band-swatch { display: inline-block; width: 14px; height: 10px; border-radius: 2px; opacity: 0.25; margin: 0 4px 0 8px; vertical-align: middle; }
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
    .alert.info { background: var(--sf-primary-bg); color: var(--sf-primary-text); }
    .alert.error { background: var(--sf-error-bg); color: var(--sf-error-text); }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
    .tile { background: var(--sf-subtle); border-radius: 8px; padding: 12px 14px; }
    .tile.now { background: var(--sf-primary-bg); }
    .tile .value { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
    .now-batteries { list-style: none; margin: 4px 0 0; padding: 0; display: grid; gap: 4px; }
    .now-batteries li { display: flex; flex-direction: column; }
    .value-small { font-size: 15px; font-weight: 600; }
    .timeline { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
    .seg {
      display: grid; grid-template-columns: minmax(150px, auto) 1fr auto; gap: 4px 12px; align-items: baseline;
      padding: 8px 12px; border-radius: 8px; background: var(--sf-subtle); border-left: 4px solid var(--sf-border);
    }
    .seg.hold { border-left-color: var(--sf-muted); }
    .seg.auto { border-left-color: var(--sf-ok); }
    .seg.force_charge { border-left-color: var(--sf-primary); }
    .seg.force_discharge { border-left-color: var(--sf-warn-text); }
    .seg.limit_discharge, .seg.limit_charge { border-left-color: var(--sf-neutral-text); }
    .seg .when { font-variant-numeric: tabular-nums; font-size: 14px; }
    .seg .what { font-weight: 600; }
    @media (max-width: 560px) { .seg { grid-template-columns: 1fr; } }
    .chart { width: 100%; height: auto; display: block; cursor: grab; touch-action: pan-y; user-select: none; }
    :host([dragging]) .chart { cursor: grabbing; }
    .chart .grid { stroke: var(--sf-border); stroke-width: 1; }
    .chart .axis { fill: var(--sf-muted); font-size: 11px; }
    .chart .scalable { cursor: pointer; }
    .chart .scalable:hover { fill: var(--sf-text); text-decoration: underline; }
    .chart .hit { fill: transparent; }
    .chart .hit:hover { fill: var(--sf-text); fill-opacity: 0.06; }
    .legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; margin-bottom: 6px; }
    .window-nav { display: flex; flex-wrap: wrap; gap: 8px; }
    .window-label { margin: 0 0 6px; }
    .legend-item { display: inline-flex; align-items: center; gap: 6px; }
    .swatch { width: 12px; height: 12px; border-radius: 3px; flex: none; }
    .swatch.inline { display: inline-block; margin-right: 6px; vertical-align: -1px; }
    .legend-button { font: inherit; color: inherit; background: none; border: 1px solid transparent; border-radius: 999px;
      padding: 2px 8px; margin: -3px 0 -3px -9px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }
    .total-toggle label, .total-toggle input { cursor: pointer; }
    .total-toggle input { margin: 0; }
    .legend-button:hover { background: var(--sf-subtle); }
    .legend-button.active { border-color: var(--sf-border, var(--sf-muted)); background: var(--sf-subtle); font-weight: 600; }
    .warn-text { color: var(--sf-warn-text); }
    .flex-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }
    .flex-card { background: var(--sf-subtle); border-radius: 8px; padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
    .flex-head { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
    .windows { margin: 4px 0 0; padding-left: 18px; }
    h3 { margin: 0 0 8px; font-size: 15px; }
    h2 .toggle {
      display: inline-flex; align-items: center; gap: 6px; padding: 0; border: none; background: none;
      font: inherit; color: inherit; cursor: pointer;
    }
    h2 .caret { font-size: 12px; color: var(--sf-muted); }
    .tag {
      font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; margin-left: 6px;
      background: var(--sf-neutral-bg); color: var(--sf-neutral-text); vertical-align: middle;
    }
    .tag.on { background: var(--sf-ok-bg); color: var(--sf-ok-text); }
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
    .chart .limit { stroke: var(--sf-muted); stroke-width: 1; stroke-dasharray: 6 4; }
    .chart .limit-label { paint-order: stroke; stroke: var(--sf-surface); stroke-width: 3px; }
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

customElements.define("sunflow-plan", SunflowPlan);
