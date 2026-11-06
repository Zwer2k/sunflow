/**
 * Last values of a view for this browser tab, so reopening it shows them at once while
 * fresh ones load. Storage may be unavailable (private mode, previews): then nothing is kept.
 */
const PREFIX = "sunflow.view.";

export function remember(key, value) {
  try {
    sessionStorage.setItem(PREFIX + key, JSON.stringify({ at: Date.now(), value }));
  } catch {
    // Full or unavailable: the view just loads as before
  }
}

/** The value kept for key, if not older than maxAgeMs. */
export function recall(key, maxAgeMs) {
  try {
    const kept = JSON.parse(sessionStorage.getItem(PREFIX + key) ?? "null");
    return kept && Date.now() - kept.at <= maxAgeMs ? kept.value : null;
  } catch {
    return null;
  }
}
