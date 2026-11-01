"""Translations of the App's messages: German is the source text, other languages map it.

One language for the whole installation, because plan notices and recommendations are
created in the background, not per browser.
"""

from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
from typing import Any, Literal

_LOGGER = logging.getLogger(__name__)

Language = Literal["de", "en"]
LANGUAGES: tuple[Language, ...] = ("de", "en")
LOCALES_DIR = Path(__file__).parent / "locales"
# Home Assistant's language rarely changes
HA_LANGUAGE_CACHE = timedelta(hours=1)

_language: Language = "de"
_catalogs: dict[str, dict[str, str]] = {}
_ha_language: tuple[datetime, str] | None = None


def _catalog(language: Language) -> dict[str, str]:
    if language not in _catalogs:
        path = LOCALES_DIR / f"{language}.json"
        _catalogs[language] = json.loads(path.read_text()) if path.exists() else {}
    return _catalogs[language]


def t(text: str, **params: Any) -> str:
    """The text in the current language, with {name} placeholders filled in."""
    translated = text if _language == "de" else _catalog(_language).get(text, text)
    return translated.format(**params) if params else translated


def N_(text: str) -> str:
    """Mark a text for translation where it is defined; translate it with t() where it is used."""
    return text


def number(value: float, digits: int = 1) -> str:
    """A number with the decimal separator of the current language, e.g. "1,5" or "1.5"."""
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if _language == "de" else text


def language() -> Language:
    """The current language."""
    return _language


def set_language(language: Language) -> None:
    """Switch the language for all following messages."""
    global _language  # noqa: PLW0603 - one language for the whole App
    _language = language


def resolve(setting: str, ha_language: str | None) -> Language:
    """The language for a setting: "auto" follows Home Assistant, German only for German."""
    if setting in LANGUAGES:
        return setting  # type: ignore[return-value]
    return "de" if (ha_language or "de").lower().startswith("de") else "en"


async def update_language(client: Any, setting: str) -> Language:
    """Apply the language setting; "auto" asks Home Assistant for its language (cached)."""
    global _ha_language  # noqa: PLW0603 - cached for all callers
    from sunflow.ha import HAError  # noqa: PLC0415 - sunflow.ha uses t() itself

    ha_language = None
    if setting not in LANGUAGES:
        now = datetime.now(UTC)
        if _ha_language is None or now - _ha_language[0] > HA_LANGUAGE_CACHE:
            try:
                core = await client.command("get_config")
                _ha_language = (now, str(core.get("language") or "de"))
            except HAError:
                _LOGGER.debug("Language of Home Assistant not available", exc_info=True)
        ha_language = _ha_language[1] if _ha_language else None
    set_language(resolve(setting, ha_language))
    return _language
