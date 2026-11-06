import { t } from "./i18n.js";

/**
 * Search index of the settings: area (step key), the label as shown on the page (it is also
 * what the result jumps to) and words users may type instead (German and English, any case).
 */
const ENTRIES = [
  ["integrations", "Gefundene Integrationen", "integration hacs solcast sofar huawei goe go-e evcc tibber octopus ha"],
  ["integrations", "Gerät direkt anbinden", "modbus tcp victron gx direkt ohne integration ip port"],
  ["integrations", "Sunflow-Integration in Home Assistant", "energie dashboard energy dashboard sunflow_ems sensoren automation schlüssel token"],
  ["integrations", "PV-Prognosen fürs Energie-Dashboard", "prognose energie dashboard forecast flächen"],
  ["integrations", "MCP-Server für KI-Assistenten", "mcp ki ai claude assistent schlüssel key token port"],
  ["devices", "Geräte", "wechselrichter inverter speicher batterie akku wallbox zähler meter heizstab profil rollen entitäten sensor"],
  ["devices", "Eigene Verbraucher", "verbraucher shelly steckdose schalter switch heizstab esphome manuell nur messen waschmaschine herd trockner"],
  ["devices", "Kurzbezeichnung", "kurzname abkürzung speicher name"],
  ["devices", "Verschleißkosten", "verschleiss zyklen akku batterie ct kwh wear degradation lebensdauer"],
  ["devices", "Akku laden", "e-bike ebike ladegerät charger akku voll laden"],
  ["devices", "Integration neu laden", "reload hängt neu starten integration"],
  ["test", "Gerätetest", "test vorzeichen steuerung prüfen laden entladen"],
  ["site", "Bilanzprüfung", "bilanz zusammenhang messwerte doppelt gezählt zähler pv netz"],
  ["optimizers", "Optimierer", "tigo huawei optimizer modul module dach anordnung"],
  ["optimizers", "Zuordnung", "anordnung modul platz dach reihe spalte layout"],
  ["vehicles", "Fahrzeuge", "auto e-auto ev car fahrzeug elektroauto"],
  ["vehicles", "Akkukapazität (nutzbar)", "kapazität kwh batterie auto"],
  ["vehicles", "Ladewirkungsgrad", "wirkungsgrad verluste effizienz efficiency"],
  ["vehicles", "Höchste Ladeleistung einphasig (optional)", "phasen einphasig 1p leistung kw"],
  ["vehicles", "Höchste Ladeleistung dreiphasig (optional)", "phasen dreiphasig 3p leistung kw"],
  ["vehicles", "12-V-Batterie schützen", "12v starterbatterie bordnetz 12 volt"],
  ["charging", "Laden", "wallbox laden fahrzeug zuordnung karte rfid"],
  ["charging", "Karte an der Wallbox", "rfid karte chip fahrzeug erkennen"],
  ["charging", "Ziel-Ladestand", "soc ziel prozent ladeziel target"],
  ["charging", "Fertig bis (täglich)", "abfahrt uhrzeit bereit deadline morgens"],
  ["charging", "Hausspeicher", "speicher batterie entladen ins auto"],
  ["consumers", "Verbraucher", "warmwasser heizstab wärmepumpe pool strategie pv überschuss"],
  ["consumers", "Alternative Wärmequelle (Fernwärme, Gas …)", "fernwärme gas heizung wärme alternative"],
  ["consumers", "Strom aus dem Hausspeicher", "speicher batterie verbraucher entladen"],
  ["consumers", "Einspeisevergütung", "einspeisung eeg vergütung feed in"],
  ["sources", "Bezugspreis", "tarif strompreis preis tibber octopus awattar dynamisch börse epex arbeitspreis"],
  ["sources", "Arbeitspreis brutto", "fester preis fix tarif ct kwh"],
  ["sources", "Aufschlag netto", "netzentgelt umlagen aufschlag börsenpreis"],
  ["sources", "Mehrwertsteuer", "mwst ust vat steuer"],
  ["sources", "PV-Prognose", "solcast forecast.solar prognose flächen dach sonne"],
  ["standards", "Strategie des Hausspeichers (Standard)", "strategie ausgewogen autark autarkie pv aufnehmen speichernutzung puffer"],
  ["standards", "Laden aus dem Netz", "netzladen günstig nachts grid charge"],
  ["standards", "Verschleißkosten Speicher", "verschleiss zyklen wear kosten"],
  ["standards", "Ladegrenze im Alltag", "max soc obergrenze 80 90 prozent schonen"],
  ["standards", "Voll laden alle", "vollladung balancing zellen ausgleich 100"],
  ["standards", "Spätestens nach … aus dem Netz", "vollladung netz überfällig"],
  ["standards", "Alterung bei hohem Ladestand", "kalendarische alterung hoher soc"],
  ["standards", "Notstrom-Reserve", "notstrom backup eps reserve stromausfall"],
  ["standards", "Hausverbrauch pro Tag (Startwert)", "verbrauch kwh tag lastprognose"],
  ["standards", "Großverbraucher ausklammern ab", "großverbraucher sauna backofen last ausreißer"],
  ["standards", "Netzdienlich", "netzdienlich einspeisegrenze 60 prozent peak"],
  ["standards", "Einspeisung möglichst unter", "einspeisegrenze export limit"],
  ["standards", "Netzbezug möglichst unter", "bezugsgrenze import limit spitzenlast"],
  ["standards", "Unsichere PV-Prognose", "risiko vorsichtig p10 unsicher wolken"],
  ["standards", "Wetter für die Bewölkung", "wetter bewölkung weather wolken"],
  ["standards", "Lernquellen", "lernen außentemperatur feuchte wetterstation helligkeit einstrahlung niederschlag ml"],
  ["standards", "Modultemperatur je Dachfläche", "modultemperatur pv temperatur optimierer"],
  ["display", "Wetter in der Live-Ansicht", "wetter live handy anzeige"],
  ["display", "Sprache", "sprache language deutsch englisch english"],
];

/** Lowercase without accents, ß and punctuation: "Verschleiß-Kosten" → "verschleiss kosten". */
export function normalize(text) {
  return text
    .toLowerCase()
    .replaceAll("ß", "ss")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** Edit distance with swapped neighbours counting once, stopping early above max. */
function distance(a, b, max) {
  if (Math.abs(a.length - b.length) > max) return max + 1;
  let before = null;
  let previous = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    let best = i;
    for (let j = 1; j <= b.length; j++) {
      let value = Math.min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      if (before && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) value = Math.min(value, before[j - 2] + 1);
      row.push(value);
      best = Math.min(best, value);
    }
    if (best > max) return max + 1;
    before = previous;
    previous = row;
  }
  return previous[b.length];
}

/** How well one typed word matches the words of a text: 0 none, up to 1 exact. */
function wordScore(query, words) {
  let best = 0;
  for (const word of words) {
    if (word === query) return 1;
    if (word.startsWith(query)) best = Math.max(best, 0.9);
    // Inside compound words: "kosten" in "verschleisskosten"
    else if (query.length >= 3 && word.includes(query)) best = Math.max(best, 0.7);
    // Typos only from the second letter on: few words share it, so little noise
    else if (query.length >= 4 && word[0] === query[0]) {
      const typos = query.length >= 9 ? 2 : 1;
      // A typo in a word typed so far ("verschleis" for "verschleiss…")
      const head = word.slice(0, query.length);
      if (distance(query, head, typos) <= typos || distance(query, word, typos) <= typos) best = Math.max(best, 0.5);
    }
  }
  return best;
}

/**
 * Entries matching every typed word, best first. extra: further entries of this installation
 * ({step, label, keywords, hint}), e.g. devices by their name.
 */
export function searchSettings(query, steps, extra = [], limit = 8) {
  const words = normalize(query).split(" ").filter(Boolean);
  if (!words.length) return [];
  const entries = [
    // The German label counts as a keyword too, so it is found in every language
    ...ENTRIES.map(([step, label, keywords]) => ({ step, label: t(label), keywords: `${label} ${keywords}` })),
    ...steps.map(([step, label]) => ({ step, label, keywords: "", area: true })),
    ...extra,
  ];
  const results = [];
  for (const entry of entries) {
    const label = normalize(entry.label).split(" ");
    // Keywords count in both languages; the area name helps ("planung netz")
    const other = normalize(`${entry.keywords} ${entry.hint ?? ""} ${steps.find(([key]) => key === entry.step)?.[1] ?? ""}`).split(" ");
    let score = 0;
    for (const word of words) {
      const value = Math.max(wordScore(word, label), 0.6 * wordScore(word, other));
      if (value === 0) {
        score = 0;
        break;
      }
      score += value;
    }
    if (score > 0) results.push({ ...entry, score: score / words.length + (entry.area ? 0.05 : 0) });
  }
  results.sort((a, b) => b.score - a.score || a.label.length - b.label.length);
  const seen = new Set();
  return results.filter((r) => !seen.has(`${r.step}|${r.label}`) && seen.add(`${r.step}|${r.label}`)).slice(0, limit);
}

export const SEARCH_LABELS = ENTRIES.map(([, label]) => label);

/** Elements in the page and all shadow roots below root. */
function* deepElements(root) {
  for (const element of root.querySelectorAll("*")) {
    yield element;
    if (element.shadowRoot) yield* deepElements(element.shadowRoot);
  }
}

/** The element showing this text itself (a heading, label …), not one of its ancestors. */
export function findLabel(root, text) {
  const wanted = normalize(text);
  let fallback = null;
  for (const element of deepElements(root)) {
    if (["STYLE", "SCRIPT", "OPTION"].includes(element.tagName)) continue;
    const own = normalize([...element.childNodes].filter((n) => n.nodeType === Node.TEXT_NODE).map((n) => n.textContent).join(" "));
    if (own === wanted) return element;
    if (!fallback && own.startsWith(wanted) && own.length < wanted.length + 40) fallback = element;
  }
  return fallback;
}
