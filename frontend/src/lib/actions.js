import { t } from "./i18n.js";

/** Battery actions of the plan with their label and explanation. */
export const ACTIONS = {
  auto: { label: t("Eigenverbrauch"), hint: t("Speicher lädt aus PV-Überschuss und deckt den Bedarf") },
  hold: { label: t("Speicher halten"), hint: t("Speicher wird geschont, Bedarf kommt aus dem Netz") },
  force_charge: { label: t("Laden aus dem Netz"), hint: t("Speicher wird gezielt geladen") },
  force_discharge: { label: t("Entladen ins Netz"), hint: t("Speicher speist gezielt ein") },
  limit_discharge: { label: t("Entladen begrenzt"), hint: t("Speicher entlädt höchstens die geplante Leistung, darüber hinaus kommt der Bedarf aus dem Netz") },
  limit_charge: { label: t("Laden begrenzt"), hint: t("Speicher lädt höchstens die geplante Leistung aus PV, der übrige Überschuss wird eingespeist") },
};
