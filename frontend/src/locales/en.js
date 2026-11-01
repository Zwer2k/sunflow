// English catalog: German source text → English, one part per group of modules.
import app from "./en/app.js";
import settings from "./en/settings.js";
import viewsA from "./en/views-a.js";
import viewsB from "./en/views-b.js";

export default { ...app, ...settings, ...viewsA, ...viewsB };
