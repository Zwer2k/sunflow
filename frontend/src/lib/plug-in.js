import { html } from "../../vendor/lit-all.min.js";
import { formatValue } from "./format.js";
import { locale, t } from "./i18n.js";

const time = new Intl.DateTimeFormat(locale(), { hour: "2-digit", minute: "2-digit" });

/** Cars at home but not plugged in that the plan gives energy soon. */
export function plugInHints(hints) {
  return (hints ?? []).map((h) => {
    const params = { time: time.format(new Date(h.start)), energy: formatValue(h.energy_wh, "Wh"), pv: formatValue(h.pv_wh, "Wh"), wallbox: h.wallbox };
    return html`<div class="alert info" role="status">🔌 <strong>${h.vehicle}</strong> ${t("ist zu Hause, aber nicht angesteckt.")}
    ${h.pv_wh >= 500
      ? t("Ab {time} sind {energy} eingeplant, davon {pv} PV-Überschuss – Einstecken an „{wallbox}“ lohnt sich.", params)
      : t("Ab {time} sind {energy} eingeplant – Einstecken an „{wallbox}“ lohnt sich.", params)}</div>`;
  });
}
