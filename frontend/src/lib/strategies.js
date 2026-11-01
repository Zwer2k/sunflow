import { html } from "../../vendor/lit-all.min.js";

/** Strategies of the home battery: short label, one-line hint and what the plan does differently. */
export const STRATEGIES = {
  balanced: {
    label: "Ausgewogen",
    short: "Ausgewogen",
    hint: "Geringste Stromkosten über den Planungszeitraum.",
    details: [
      "Rechnet bis zu 96 Stunden voraus mit Preisen, Einspeisevergütung, PV- und Lastprognose, Verlusten und Verschleiß.",
      "Lädt aus dem Netz, wenn der Preisunterschied Verluste und Verschleiß übersteigt – nur wenn „Laden aus dem Netz“ erlaubt ist.",
      "Hält Energie für teure Stunden zurück und entlädt dort, statt in günstigen Stunden.",
      "Passt zu dynamischen Tarifen und Zeittarifen (z. B. günstiger Nachtstrom).",
    ],
  },
  pv_absorption: {
    label: "Möglichst viel PV aufnehmen",
    short: "PV aufnehmen",
    hint: "Lädt vor sonnigen Tagen nicht aus dem Netz und hält Platz für Solarstrom frei.",
    details: [
      "Wie „Ausgewogen“, aber jede eingespeiste kWh zählt im Plan 5 ct weniger.",
      "Dadurch kaum Netzladen vor sonnigen Tagen, der Speicher ist vor dem Mittag leerer, Überschuss geht eher an Verbraucher als ins Netz.",
      "Kein Unterschied zu „Ausgewogen“, solange ohnehin kaum eingespeist wird, z. B. im Winter.",
      "Kennt keine Einspeisebegrenzung – dafür „Netzdienlich“ mit Einspeisegrenze verwenden.",
    ],
  },
  autarky: {
    label: "Möglichst autark",
    short: "Autarkie",
    hint: "So wenig Netzbezug wie möglich: kein Netzladen, der Speicher deckt jeden Bedarf sofort. Kann mit Zeittarifen etwas mehr kosten.",
    details: [
      "Jede kWh aus dem Netz zählt im Plan 1 € extra: Der Speicher deckt jeden Bedarf, solange er kann.",
      "Lädt nie aus dem Netz, auch wenn „Laden aus dem Netz“ erlaubt ist. Ausnahme: eine überfällige Vollladung („Spätestens nach … aus dem Netz“).",
      "Verbraucher bekommen bevorzugt PV-Überschuss statt Netzstrom.",
      "Kostet bei Zeittarifen mehr, weil günstiger Nachtstrom nicht für den Tag gespeichert wird.",
    ],
  },
};

/** Collapsible description of one strategy. */
export function strategyDetails(key) {
  const strategy = STRATEGIES[key];
  return html`<details class="strategy-details">
    <summary class="small">Was macht „${strategy.label}“?</summary>
    <ul class="small muted">${strategy.details.map((line) => html`<li>${line}</li>`)}</ul>
  </details>`;
}
