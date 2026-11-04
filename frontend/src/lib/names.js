/** Name suggestions for consumers and cars from the entities chosen for them. */

/** The HA device's name, else the entity's name. */
export function suggestedName(candidate) {
  return candidate ? (candidate.device || candidate.name || "").trim() : "";
}

/**
 * The form with its name set to the suggestion of the chosen entity, as long as the name is
 * empty or still the last suggestion (a name typed by the user stays).
 */
export function withSuggestedName(form, candidate) {
  const suggestion = suggestedName(candidate);
  if (!suggestion || (form.name && form.name !== form.suggestedName)) return form;
  return { ...form, name: suggestion, suggestedName: suggestion };
}
