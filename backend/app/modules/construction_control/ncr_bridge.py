# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Shared NCR-raise bridge for the construction-control module.

The failed-inspection -> NCR bridge first shipped on ``ConstructionControlService``
(Pillar 1). As later pillars (as-built records, gates) also need to raise a
non-conformance without instantiating the inspection service, the low-level call into
the NCR module is extracted here as one reusable coroutine.

It is intentionally tiny: callers assemble the human description and the metadata, this
helper performs the lazy import and the create.

The import is lazy to keep the module-level import graph acyclic. It is NOT a degradation
guard, and this file used to claim it was. ``oe_ncr`` is a hard entry in the
construction-control manifest's ``depends``, and the manifest keeps a separate
``optional_depends`` for the three modules that genuinely are optional. With the NCR module
unavailable this coroutine raises and the operation that called it fails. That is
deliberate; ``raise_ncr`` records why.

Degrading is right in the two places this module actually implements it, and both carry a
real guard rather than a promise: ``HandoverService._open_ncr_count`` reads a count and
falls back to zero, and ``seed._raise_ncr`` returns ``str | None`` so a demo register still
seeds without NCRs. Neither is a live write whose result something downstream depends on.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def raise_ncr(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    title: str,
    description: str,
    ncr_type: str,
    severity: str,
    user_id: str | None,
    linked_inspection_id: str | None = None,
    location_description: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Create an NCR through the NCR module and return its id as a string.

    Raises rather than degrading when the NCR module is unavailable, because every caller
    treats the returned id as required. ``HandoverService.override_gate`` is the case that
    decides it: it raises the NCR first and stores the id in the override's audit metadata,
    so a silent fallback would issue an acceptance certificate over open items with nothing
    recording why. The other callers write the id into ``raised_ncr_id``, where ``None`` is
    indistinguishable from "the record passed and no NCR was due".

    The reachable failure is the NCR module missing from disk, not disabled. The loader
    rejects ``oe_ncr`` on the core check before it ever reaches the dependents check, so
    either alone would stop it. But ``ModuleLoader.resolve_order`` only logs a warning for a
    dependency it cannot find, and nothing enforces ``depends`` at startup, so
    construction-control still boots with the NCR module gone from disk.

    Coupled to ``ReferenceCountRepository.count_ncrs_on_inspection``, which reports zero
    holders when the NCR module cannot be imported and so lets the inspection be deleted.
    That zero means "no NCR can be counted", not "no NCR exists": rows written while the
    module was present outlive its removal in the database. It is sound for a deployment
    that never had the NCR module and optimistic for one that lost it. If this coroutine is
    ever made to degrade, that count turns into a hole and has to be revisited with it.
    """
    from app.modules.ncr.schemas import NCRCreate
    from app.modules.ncr.service import NCRService

    data = NCRCreate(
        project_id=project_id,
        title=title[:500],
        description=description[:10000],
        ncr_type=ncr_type,
        severity=severity,
        status="identified",
        location_description=location_description,
        linked_inspection_id=linked_inspection_id,
        metadata=metadata or {},
    )
    ncr = await NCRService(session).create_ncr(data, user_id=user_id)
    return str(ncr.id)
