import { t } from "./i18n.js";

/**
 * What goes where in the live view's house pictures: the user's choice (settings "Anzeige"),
 * otherwise guessed from names and kinds.
 */

// Free roof faces in this order; names of the forecast plane pick north and carport first
export const ROOF_FACES = ["south", "annex", "carport", "north"];
const ROOF_HINTS = [[/nord|north/i, "north"], [/carport|garage|vordach|port/i, "carport"]];

export const ROOF_LABELS = {
  south: t("Hauptdach vorne"),
  annex: t("Anbau"),
  carport: t("Carport"),
  north: t("Hauptdach hinten (Nordseite)"),
  hidden: t("nicht zeigen"),
};

export const FIGURE_LABELS = {
  heatpump: t("Wärmepumpe"),
  tank: t("Warmwasserspeicher"),
  vehicle: t("Auto im Carport"),
  bike: t("E-Bike"),
  washer: t("Waschmaschine"),
  dryer: t("Trockner"),
  stove: t("Herd"),
  house: t("nur im Hausverbrauch"),
};

/** Which figure a consumer is by its kind and name; null: only part of the house load. */
export function guessFigure(consumer) {
  const name = consumer.name ?? "";
  if (consumer.kind === "vehicle") return "vehicle";
  if (consumer.kind === "heat_pump" || /wärmepumpe|waermepumpe|heat ?pump|\bwp\b/i.test(name)) return "heatpump";
  if (consumer.kind === "heater" || /warmwasser|boiler|heizstab|hot water/i.test(name)) return "tank";
  if (/e-?bike|fahrrad|pedelec|\bbike\b/i.test(name)) return "bike";
  if (/wasch|washer|washing/i.test(name)) return "washer";
  if (/trockner|dryer/i.test(name)) return "dryer";
  if (/herd|kochfeld|backofen|\bofen\b|stove|oven|cooktop/i.test(name)) return "stove";
  return null;
}

/** The figure a consumer is shown as; null: only part of the house load. */
export function figureOf(consumer, scene) {
  const chosen = scene?.consumers?.[consumer.device_id];
  if (chosen) return chosen === "house" ? null : chosen;
  return guessFigure(consumer);
}

/** The plants on the two inverter places: the chosen ones first, then the others in their order. */
export function placedPlants(live, scene) {
  const chosen = (scene?.inverters ?? []).map((id) => live.pv.find((p) => p.device_id === id)).filter(Boolean);
  return [...chosen, ...live.pv.filter((p) => !chosen.includes(p))].slice(0, 2);
}

/** Key of a string, or of a plant without measured strings, for the roof choice. */
export const roofKey = (plant, string) => (string ? `${plant.device_id}:${string.index}` : plant.device_id);

/**
 * Measured strings (or whole plants) on the roof faces, with the inverter place they belong to.
 * Chosen faces first; the rest fill the free faces, several on one face are added up.
 */
export function placeRoofs(plants, scene) {
  const parts = plants.flatMap((plant, inverter) =>
    plant.strings?.length
      ? plant.strings.map((s) => ({ key: roofKey(plant, s), name: s.plane ?? `${plant.name} ${s.name}`, power: s.power_w ?? 0, inverter, display: s.display }))
      : [{ key: roofKey(plant), name: plant.name, power: plant.power_w ?? 0, inverter, display: plant.display }],
  );
  const roofs = {};
  const put = (face, part) => {
    if (roofs[face]) {
      roofs[face].power += part.power;
      roofs[face].names.push(part.name);
    } else {
      roofs[face] = { power: part.power, names: [part.name], inverter: part.inverter, display: part.display };
    }
  };
  const chosen = (part) => scene?.roofs?.[part.key];
  for (const part of parts.filter((p) => chosen(p) && chosen(p) !== "hidden")) put(chosen(part), part);
  for (const part of parts.filter((p) => !chosen(p))) {
    const hinted = ROOF_HINTS.find(([pattern]) => pattern.test(part.name))?.[1];
    const face = [hinted, ...ROOF_FACES].find((f) => f && !roofs[f]) ?? ROOF_FACES.findLast((f) => roofs[f]);
    put(face, part);
  }
  return roofs;
}
