import { html } from "../../vendor/lit-all.min.js";
import { t } from "./i18n.js";

/** Strategies of the home battery: short label (tiny on a phone), one-line hint and what the plan does differently. */
export const STRATEGIES = {
  balanced: {
    label: t("Ausgewogen"),
    short: t("Ausgewogen"),
    tiny: t("Ausgewogen"),
    hint: t("Geringste Stromkosten über den Planungszeitraum."),
    details: [
      t("Rechnet bis zu 96 Stunden voraus mit Preisen, Einspeisevergütung, PV- und Lastprognose, Verlusten und Verschleiß."),
      t("Lädt aus dem Netz, wenn der Preisunterschied Verluste und Verschleiß übersteigt – nur wenn „Laden aus dem Netz“ erlaubt ist."),
      t("Hält Energie für teure Stunden zurück und entlädt dort, statt in günstigen Stunden."),
      t("Lädt für einen Tag etwas über der Prognose: Verbrauch und PV schwanken, eine fehlende kWh kostet den teuren Preis, eine übrige wartet nur auf die nächste günstige Ladung. Reicht an etwa 4 von 5 Tagen."),
      t("Passt zu dynamischen Tarifen und Zeittarifen (z. B. günstiger Nachtstrom)."),
    ],
  },
  pv_absorption: {
    label: t("Möglichst viel PV aufnehmen"),
    short: t("PV aufnehmen"),
    tiny: t("PV"),
    hint: t("Lädt vor sonnigen Tagen nicht aus dem Netz und hält Platz für Solarstrom frei."),
    details: [
      t("Wie „Ausgewogen“, aber jede eingespeiste kWh zählt im Plan 5 ct weniger."),
      t("Dadurch kaum Netzladen vor sonnigen Tagen, der Speicher ist vor dem Mittag leerer, Überschuss geht eher an Verbraucher als ins Netz."),
      t("Kein Unterschied zu „Ausgewogen“, solange ohnehin kaum eingespeist wird, z. B. im Winter."),
      t("Kennt keine Einspeisebegrenzung – dafür „Netzdienlich“ mit Einspeisegrenze verwenden."),
    ],
  },
  autarky: {
    label: t("Möglichst autark"),
    short: t("Autarkie"),
    tiny: t("Autark"),
    hint: t("So wenig Netzbezug wie möglich: kein Netzladen, der Speicher deckt jeden Bedarf sofort. Kann mit Zeittarifen etwas mehr kosten."),
    details: [
      t("Jede kWh aus dem Netz zählt im Plan 1 € extra: Der Speicher deckt jeden Bedarf, solange er kann."),
      t("Lädt nie aus dem Netz, auch wenn „Laden aus dem Netz“ erlaubt ist. Ausnahme: eine überfällige Vollladung („Spätestens nach … aus dem Netz“)."),
      t("Verbraucher bekommen bevorzugt PV-Überschuss statt Netzstrom."),
      t("Kostet bei Zeittarifen mehr, weil günstiger Nachtstrom nicht für den Tag gespeichert wird."),
    ],
  },
  storage: {
    label: t("Speichernutzung"),
    short: t("Speichernutzung"),
    tiny: t("Speicher"),
    hint: t("Wie „Ausgewogen“, lädt aber in günstigen Stunden so weit nach, dass der Speicher auch an verbrauchsstarken oder trüben Tagen bis zur nächsten günstigen Ladung reicht."),
    details: [
      t("Der Puffer gegen einen Tag über der Prognose reicht an etwa 19 von 20 Tagen statt an 4 von 5."),
      t("Wie groß er ist, lernt Sunflow aus den Schwankungen deines Tagesverbrauchs und der Spanne der PV-Prognose (p10)."),
      t("An einem durchschnittlichen Tag bleibt der Puffer im Speicher und ersetzt einen Teil der nächsten Ladung – das kostet nur wenig Verluste."),
      t("Kosten sind zweitrangig: Netzstrom in teuren Phasen zählt im Plan 10 ct extra. So lädt Sunflow nachts und deckt den Tag aus dem Speicher, auch wenn Verluste und Verschleiß den Preisvorteil auffressen."),
      t("Braucht „Laden aus dem Netz“ und einen Zeittarif oder dynamischen Tarif, sonst deckt der Speicher nur möglichst viel aus PV."),
    ],
  },
};

/** Collapsible description of one strategy. */
export function strategyDetails(key) {
  const strategy = STRATEGIES[key];
  return html`<details class="strategy-details">
    <summary class="small">${t("Was macht „{label}“?", { label: strategy.label })}</summary>
    <ul class="small muted">${strategy.details.map((line) => html`<li>${line}</li>`)}</ul>
  </details>`;
}
