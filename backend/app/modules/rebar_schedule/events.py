# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Rebar schedule event topic names.

Published best-effort through ``event_bus.publish_detached`` from the service.
Subscribers - procurement (to raise a steel call-off), progress (to book
placed reinforcement), notifications - can consume these without importing
this module's ORM.
"""

from __future__ import annotations

# A bending schedule was taken into a project. Carries the record count and
# the validation status, so a subscriber can decide whether to act on it.
SCHEDULE_IMPORTED = "rebar_schedule.import.completed"

# An import carried validation errors. Separate from the import event so a
# subscriber that only wants to hear about trouble does not have to inspect
# every import. The file is still stored.
SCHEDULE_HAS_ERRORS = "rebar_schedule.import.has_errors"

# An import was deleted, with everything that came in with it.
SCHEDULE_DELETED = "rebar_schedule.import.deleted"
