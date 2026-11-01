// Entry point: the language has to be known before the App's modules are evaluated.
import { initI18n } from "./lib/i18n.js";

await initI18n();
await import("./app.js");
