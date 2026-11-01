// Translations: German is the source text, other languages map it (missing ones stay German).
// The language is fixed before the App's modules load, so module constants can be translated.

let language = "de";
let catalog = {};

/** Load the App's language; call before importing the App. */
export async function initI18n() {
  try {
    const response = await fetch("api/v1/language", { signal: AbortSignal.timeout(10000) });
    if (response.ok) language = (await response.json()).language;
  } catch {
    // Without an answer the App starts in German
  }
  if (language !== "de") {
    try {
      catalog = (await import(`../locales/${language}.js`)).default;
    } catch {
      language = "de";
    }
  }
  document.documentElement.lang = language;
}

function fill(text, params) {
  return params ? text.replace(/\{(\w+)\}/g, (match, name) => (name in params ? String(params[name]) : match)) : text;
}

/** The text in the App's language, with {name} placeholders filled in. */
export function t(text, params) {
  return fill(catalog[text] ?? text, params);
}

/**
 * Like t() for a German text with more than one meaning in other languages, e.g. "Speicher" as a
 * heading ("Batteries") or "1 Optimierer" (singular). The catalog key is "context|text".
 */
export function tc(context, text, params) {
  return fill(catalog[`${context}|${text}`] ?? catalog[text] ?? text, params);
}

/** t() for a text with {count}: a count of 1 uses the "one|…" entry (singular) where there is one. */
export function tn(count, text, params) {
  return count === 1 ? tc("one", text, params) : t(text, params);
}

/** "de" or "en". */
export function lang() {
  return language;
}

/** Locale for Intl formats. */
export function locale() {
  return language === "de" ? "de-DE" : "en-GB";
}

/** A number already turned into text with the App's decimal separator. */
export function decimal(text) {
  return language === "de" ? String(text).replace(".", ",") : String(text);
}
