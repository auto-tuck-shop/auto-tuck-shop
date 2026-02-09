"""
Ensure all locale files have the same keys and that every key
referenced in code via t() exists in every locale file.
"""

import json
import re
from pathlib import Path

import pytest

LOCALES_DIR = Path(__file__).parent.parent / "apps" / "whatsapp" / "locales"
WEBHOOK_HANDLER = (
    Path(__file__).parent.parent
    / "apps"
    / "whatsapp"
    / "services"
    / "webhook_handler.py"
)


def _flatten_keys(d: dict, prefix: str = "") -> set[str]:
    """Recursively flatten a nested dict into dot-notation keys."""
    keys = set()
    for k, v in d.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys |= _flatten_keys(v, full_key)
        else:
            keys.add(full_key)
    return keys


def _load_all_locales() -> dict[str, dict]:
    """Load all JSON locale files and return {name: data}."""
    locales = {}
    for path in sorted(LOCALES_DIR.glob("*.json")):
        locales[path.stem] = json.loads(path.read_text())
    return locales


def _extract_t_keys_from_source() -> set[str]:
    """Extract all t("...") keys from webhook_handler.py source code."""
    source = WEBHOOK_HANDLER.read_text()
    # Match t("some.key" and t('some.key' patterns
    return set(re.findall(r'\bt\(\s*["\']([a-z_]+\.[a-z_]+)["\']', source))


class TestLocalizations:
    def test_locale_files_exist(self):
        locales = _load_all_locales()
        assert len(locales) >= 2, f"Expected at least 2 locale files, found {list(locales.keys())}"

    def test_all_locales_have_same_keys(self):
        """Every locale file must define exactly the same set of keys."""
        locales = _load_all_locales()
        locale_keys = {name: _flatten_keys(data) for name, data in locales.items()}
        names = list(locale_keys.keys())

        reference_name = "en"
        assert reference_name in locale_keys, f"Reference locale '{reference_name}' not found"
        reference_keys = locale_keys[reference_name]

        for name in names:
            if name == reference_name:
                continue
            missing = reference_keys - locale_keys[name]
            extra = locale_keys[name] - reference_keys
            errors = []
            if missing:
                errors.append(f"{name}.json is missing keys present in {reference_name}.json: {sorted(missing)}")
            if extra:
                errors.append(f"{name}.json has extra keys not in {reference_name}.json: {sorted(extra)}")
            assert not errors, "\n".join(errors)

    def test_code_keys_exist_in_all_locales(self):
        """Every t() call in code must resolve to a key in every locale file."""
        code_keys = _extract_t_keys_from_source()
        assert code_keys, "Failed to extract any t() keys from source"

        locales = _load_all_locales()
        for name, data in locales.items():
            locale_keys = _flatten_keys(data)
            missing = code_keys - locale_keys
            assert not missing, (
                f"{name}.json is missing keys used in code: {sorted(missing)}"
            )

    def test_no_empty_values(self):
        """No locale string should be empty."""
        locales = _load_all_locales()
        for name, data in locales.items():
            keys = _flatten_keys(data)
            for key in sorted(keys):
                parts = key.split(".")
                value = data
                for p in parts:
                    value = value[p]
                assert value.strip(), f"{name}.json has empty value for key '{key}'"

    def test_format_placeholders_match(self):
        """All locales must use the same {placeholder} names for each key."""
        locales = _load_all_locales()
        locale_keys = {name: _flatten_keys(data) for name, data in locales.items()}

        # Use the union of all keys
        all_keys = set()
        for keys in locale_keys.values():
            all_keys |= keys

        placeholder_re = re.compile(r"\{(\w+)\}")

        for key in sorted(all_keys):
            placeholders_by_locale = {}
            for name, data in locales.items():
                parts = key.split(".")
                try:
                    value = data
                    for p in parts:
                        value = value[p]
                    placeholders_by_locale[name] = set(placeholder_re.findall(value))
                except (KeyError, TypeError):
                    continue  # Missing key is caught by other tests

            if len(placeholders_by_locale) < 2:
                continue

            reference = list(placeholders_by_locale.values())[0]
            for name, placeholders in placeholders_by_locale.items():
                assert placeholders == reference, (
                    f"Placeholder mismatch for '{key}': "
                    f"{name} has {placeholders}, expected {reference}"
                )
