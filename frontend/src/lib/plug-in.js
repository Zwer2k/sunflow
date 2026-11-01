import { html } from "../../vendor/lit-all.min.js";
import { formatValue } from "./format.js";

const time = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit" });

/** Cars at home but not plugged in that the plan gives energy soon. */
export function plugInHints(hints) {
  return (hints ?? []).map((h) => html`<div class="alert info" role="status">🔌 <strong>${h.vehicle}</strong> ist zu Hause,
    aber nicht angesteckt. Ab ${time.format(new Date(h.start))} sind ${formatValue(h.energy_wh, "Wh")} eingeplant${h.pv_wh >= 500
      ? `, davon ${formatValue(h.pv_wh, "Wh")} PV-Überschuss`
      : ""} – Einstecken an „${h.wallbox}“ lohnt sich.</div>`);
}
