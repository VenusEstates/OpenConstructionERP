"""OpenConstructionERP - Hungary country pack (item orders, material and fee split, VAT).

This package exports a module-level ``MANIFEST`` instance of
:class:`PartnerPackManifest` referenced from ``pyproject.toml``::

    [project.entry-points."openconstructionerp.partner_packs"]
    hungary-hu = "openconstructionerp_hungary_hu:MANIFEST"

The OCERP core discovers this entry point at boot, validates the
manifest, and applies the pack overrides (branding, locale, currency,
validation rule sets, onboarding script).
"""

from __future__ import annotations

from .manifest import MANIFEST

__all__ = ["MANIFEST"]
__version__ = "0.1.0"
