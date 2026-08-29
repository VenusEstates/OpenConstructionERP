# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Locale-scoped message bundle for the BCF module.

OpenEstimate principle #2: i18n EVERYWHERE - zero hardcoded user-facing
strings. This mirrors the design of :mod:`app.core.validation.messages`: a
module-local ``messages/`` directory makes the BCF module "plugin-like" - it
carries its own translations without touching the global locales.

This used to carry its own hand-rolled bundle class instead of constructing
the shared :class:`~app.core.validation.messages.MessageBundle`, which is
exactly the kind of divergence this note exists to warn against: a bundle
that resolves a regional locale code by exact string match does not fail
loudly when it misses. It silently answers in English, and that answer is
indistinguishable from a language nobody has translated yet - nothing
downstream can tell "``es-MX`` has a translator" from "``es-MX`` never
shipped one". The five other module-local bundles (compliance,
compliance_ai, cost_match, dashboards, and rebar_schedule's own
``messages/`` directory) already construct ``MessageBundle`` directly, so
they inherited the base-language chaining fixed in
:mod:`app.core.validation.messages` for free. This one is now the sixth.

Public API
    * :func:`translate(key, locale="en", **params) -> str`
    * :func:`is_key_present(key, locale)` - diagnostic used by tests.
    * :func:`available_locales() -> list[str]`
    * :func:`reload_bundle()` - test helper.
"""

from __future__ import annotations

from pathlib import Path

from app.core.validation.messages import MessageBundle

DEFAULT_LOCALE = "en"
_MESSAGES_DIR = Path(__file__).parent
_bundle = MessageBundle(messages_dir=_MESSAGES_DIR)


def translate(key: str, locale: str = DEFAULT_LOCALE, **params: object) -> str:
    """Resolve a BCF message key for ``locale`` with ``str.format`` params."""
    return _bundle.translate(key, locale=locale, **params)


def is_key_present(key: str, locale: str = DEFAULT_LOCALE) -> bool:
    """Return ``True`` if ``key`` exists in ``locale`` without any fallback."""
    return _bundle.is_key_present(key, locale=locale)


def available_locales() -> list[str]:
    """List locales currently loaded into the BCF bundle."""
    return _bundle.available_locales()


def reload_bundle() -> None:
    """Force a cache refresh (test helper)."""
    _bundle.reload()


__all__ = ["available_locales", "is_key_present", "reload_bundle", "translate"]
