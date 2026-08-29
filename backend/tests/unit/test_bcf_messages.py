"""Tests for the BCF module's locale-scoped message bundle.

``app.modules.bcf.messages`` used to carry its own hand-rolled bundle class
(a copy of :class:`app.core.validation.messages.MessageBundle` that had
drifted) instead of constructing the shared class the way the other five
module-local bundles do. That copy resolved a locale by bare exact string
match, so a regional code such as ``de-AT`` fell straight past a complete
``de.json`` translation to English — the same defect ``translate()`` in the
core bundle had before it was fixed, silently indistinguishable from a
locale nobody had translated at all.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.modules.bcf.messages import (
    DEFAULT_LOCALE,
    available_locales,
    reload_bundle,
    translate,
)

_MESSAGES_DIR = Path(__file__).resolve().parents[2] / "app" / "modules" / "bcf" / "messages"


def _load_locale_keys(locale: str) -> set[str]:
    with (_MESSAGES_DIR / f"{locale}.json").open(encoding="utf-8") as fh:
        return set(json.load(fh).keys())


class TestMessages:
    def test_default_locale_is_english(self) -> None:
        assert DEFAULT_LOCALE == "en"

    def test_en_de_ru_present(self) -> None:
        locales = set(available_locales())
        for required in ("en", "de", "ru"):
            assert required in locales, f"missing locale bundle: {required}"

    def test_translate_resolves_known_key(self) -> None:
        en = translate("bcf.topic_not_found", locale="en")
        de = translate("bcf.topic_not_found", locale="de")
        assert en != "bcf.topic_not_found"
        assert de != en

    def test_translate_unknown_key_is_detected(self) -> None:
        # Imported inline: this name is new on the shared-class rewrite and
        # must not make the whole file fail to collect on the pre-fix
        # version, which would hide the real (behavioural) regression below.
        from app.modules.bcf.messages import is_key_present

        assert is_key_present("bcf.topic_not_found") is True
        assert is_key_present("bcf.nonexistent.key") is False


class TestRegionalLocaleChaining:
    """A regional code must resolve through its base language, not English.

    This is deliberately about the behaviour, not the class that produces
    it: it holds whichever way ``app.modules.bcf.messages`` is implemented
    underneath, as long as it keeps reusing the shared resolution chain.
    """

    def test_regional_variant_resolves_through_its_base_language(self) -> None:
        reload_bundle()
        de = translate("bcf.topic_not_found", locale="de")
        de_at = translate("bcf.topic_not_found", locale="de-AT")
        en = translate("bcf.topic_not_found", locale="en")
        assert de_at == de
        assert de_at != en

    def test_successful_base_language_resolution_logs_no_fallback_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        reload_bundle()
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="app.core.validation.messages"):
            translate("bcf.topic_not_found", locale="de-AT")
        assert not caplog.records, (
            f"a resolved regional lookup must log nothing, got {[r.message for r in caplog.records]}"
        )

    def test_unknown_regional_locale_still_falls_back_to_english(self) -> None:
        reload_bundle()
        result = translate("bcf.topic_not_found", locale="xx-YY")
        assert result == translate("bcf.topic_not_found", locale="en")


class TestLocaleKeyParity:
    def test_locale_key_parity(self) -> None:
        en_keys = _load_locale_keys("en")
        de_keys = _load_locale_keys("de")
        ru_keys = _load_locale_keys("ru")
        assert en_keys == de_keys, f"DE missing: {en_keys - de_keys}; DE extra: {de_keys - en_keys}"
        assert en_keys == ru_keys, f"RU missing: {en_keys - ru_keys}; RU extra: {ru_keys - en_keys}"
