"""Every translatable text has an English translation with the same placeholders."""

import ast
import json
from pathlib import Path
import re

import pytest

REPO = Path(__file__).resolve().parents[1]
FRONTEND = REPO / "frontend" / "src"
BACKEND = REPO / "backend" / "sunflow"
# t("…") with a plain double-quoted literal; the catalog key is the literal's value
JS_CALL = re.compile(r'(?<![\w.$])t\(\s*"((?:[^"\\\n]|\\.)*)"')
# tn(count, "…") is t("…"), with an optional singular "one|…"
JS_COUNT_CALL = re.compile(r'(?<![\w.$])tn\([^"\n]*?"((?:[^"\\\n]|\\.)*)"')
# tc("context", "…"): the key is "context|…"
JS_CONTEXT_CALL = re.compile(r'(?<![\w.$])tc\(\s*"(\w+)",\s*"((?:[^"\\\n]|\\.)*)"')
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _js_string(literal: str) -> str:
    return json.loads(f'"{literal}"')


def frontend_texts() -> dict[str, str]:
    """Translatable texts of the frontend with the file they appear in."""
    texts = {}
    for path in sorted(FRONTEND.rglob("*.js")):
        if "locales" in path.parts:
            continue
        source = path.read_text()
        for literal in JS_CALL.findall(source):
            texts.setdefault(_js_string(literal), str(path.relative_to(REPO)))
        for literal in JS_COUNT_CALL.findall(source):
            texts.setdefault(_js_string(literal), str(path.relative_to(REPO)))
        for context, literal in JS_CONTEXT_CALL.findall(source):
            texts.setdefault(f"{context}|{_js_string(literal)}", str(path.relative_to(REPO)))
    return texts


def frontend_catalog(language: str) -> dict[str, str]:
    """The merged catalog; each part is `export default {` + JSON members + `};`."""
    catalog: dict[str, str] = {}
    for path in sorted((FRONTEND / "locales" / language).glob("*.js")):
        body = path.read_text()
        start, end = body.index("export default {") + len("export default "), body.rindex("}")
        members = re.sub(r",\s*$", "", body[start + 1 : end].strip())
        part = json.loads("{" + members + "}")
        assert not (duplicates := {k for k in part if k in catalog and catalog[k] != part[k]}), duplicates
        catalog |= part
    return catalog


def backend_texts() -> dict[str, str]:
    """Texts passed to t() or marked with N_() in the backend."""
    texts = {}
    for path in sorted(BACKEND.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name in ("t", "N_") and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                texts.setdefault(node.args[0].value, str(path.relative_to(REPO)))
    return texts


def _check(texts: dict[str, str], catalog: dict[str, str]) -> None:
    missing = {text: where for text, where in texts.items() if text not in catalog}
    assert not missing, f"{len(missing)} without translation, e.g. {list(missing.items())[:5]}"
    wrong = {
        text: catalog[text]
        for text in texts
        if set(PLACEHOLDER.findall(text)) != set(PLACEHOLDER.findall(catalog[text]))
    }
    assert not wrong, f"Placeholders differ: {list(wrong.items())[:5]}"


@pytest.mark.parametrize("language", ["en"])
def test_frontend_translated(language: str) -> None:
    """Every t("…") of the frontend is in the catalog."""
    _check(frontend_texts(), frontend_catalog(language))


def test_settings_search_labels_are_on_the_pages() -> None:
    """The search jumps to a label by its text: each one must be a text of the settings."""
    search = FRONTEND / "lib" / "settings-search.js"
    labels = re.findall(r'^  \["\w+", "([^"]+)"', search.read_text(), re.MULTILINE)
    texts = {text for text, where in frontend_texts().items() if not where.endswith("settings-search.js")}

    assert labels
    assert not [label for label in labels if label not in texts]


@pytest.mark.parametrize("language", ["en"])
def test_backend_translated(language: str) -> None:
    """Every t("…") and N_("…") of the backend is in the catalog."""
    _check(backend_texts(), json.loads((BACKEND / "locales" / f"{language}.json").read_text()))


def test_backend_translates() -> None:
    """t() fills placeholders in the current language and keeps German as source."""
    from sunflow import i18n  # noqa: PLC0415 - module state is reset below

    try:
        assert i18n.t("Speicher {name}", name="A") == "Speicher A"
        i18n.set_language("en")
        assert i18n.t("Unbekannter Text {n}", n=1) == "Unbekannter Text 1"
        assert i18n.resolve("auto", "en-GB") == "en"
        assert i18n.resolve("auto", "de") == "de"
        assert i18n.resolve("de", "en") == "de"
    finally:
        i18n.set_language("de")
