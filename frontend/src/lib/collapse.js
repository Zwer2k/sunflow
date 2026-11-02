// Open or closed cards, remembered per browser; storage may be unavailable (private mode, previews)
const KEY = "sunflow.open";

function load() {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "{}") ?? {};
  } catch {
    return {};
  }
}

/** Whether a card is open: the last choice in this browser, else the fallback. */
export function isOpen(id, fallback = true) {
  const open = load()[id];
  return typeof open === "boolean" ? open : fallback;
}

/** Remember a card's state. */
export function setOpen(id, open) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...load(), [id]: open }));
  } catch {
    // Not remembered, the card still opens or closes for this visit
  }
}
