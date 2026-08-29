# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Deletion guards for the construction-control register.

Construction control is an evidence register. An inspection that carries a
result, a reviewed material, a recorded test, a signed as-built, a released gate
and an issued completion certificate are all statements somebody made on the
record, several of them under an e-signature. Removing one does not correct a
mistake, it erases the account of what was checked, so the register refuses.

Two independent reasons to refuse, and they are deliberately different codes:

* **Locked by its own state** - the record has been acted on through the
  workflow. Refused with ``409`` and prose naming the state that locked it.
  The sibling ``update_*`` guards refuse the same records with ``400``; the
  distinction is that an edit is merely too late, whereas a delete is being
  asked to destroy evidence, which is a conflict with what the register is.
* **Held by another record** - something else points at this row. Refused with
  ``409`` naming the holders by count and kind, so the caller is told what to
  clear rather than being told "no".

Every referencing column in this module is a soft ``String(36)`` and not a
database foreign key, so PostgreSQL enforces none of this on its own: without
these guards a delete leaves the holders pointing at nothing and reports success.
"""

from __future__ import annotations

from typing import NamedTuple

from fastapi import HTTPException, status


class HolderCount(NamedTuple):
    """How many records of one kind point at the row being deleted.

    ``singular`` and ``plural`` are the human names of the holding kind, chosen
    at the call site so the sentence reads in the domain's own words rather than
    in table names.

    The tally is ``total`` and not the obvious ``count`` because a NamedTuple
    field of that name shadows ``tuple.count``: the inherited method stops being
    callable and becomes an ``int``, so anything reaching for it breaks. Renaming
    it back reintroduces that.
    """

    singular: str
    plural: str
    total: int

    def phrase(self) -> str:
        """Render as ``"3 inspections"`` / ``"1 test result"``."""
        noun = self.singular if self.total == 1 else self.plural
        return f"{self.total} {noun}"


def _join(phrases: list[str]) -> str:
    """Join holder phrases as an English list: ``"a, b and c"``.

    The empty list is the common case, not an edge one: every delete that goes
    through arrives here with every holder count at zero.
    """
    if not phrases:
        return ""
    if len(phrases) == 1:
        return phrases[0]
    return f"{', '.join(phrases[:-1])} and {phrases[-1]}"


def held_phrase(holders: list[HolderCount]) -> str:
    """Render the non-zero holders as one readable phrase.

    Zero-count holders are dropped, so a caller can hand over the full holder
    list of a kind without filtering it first.
    """
    return _join([h.phrase() for h in holders if h.total > 0])


def refuse_if_held(subject: str, holders: list[HolderCount], *, advice: str) -> None:
    """Raise ``409`` naming the holders, or return quietly when there are none.

    Args:
        subject: The thing being deleted, as it should read in the sentence,
            for example ``"acceptance criterion ACC-004"``.
        holders: Counts per holding kind. Zero counts are ignored.
        advice: What the caller can do instead, appended as its own sentence.

    Raises:
        HTTPException: 409 when at least one holder was counted.
    """
    phrase = held_phrase(holders)
    if not phrase:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"This {subject} is referenced by {phrase} and cannot be deleted. {advice}",
    )


def refuse_if_locked(subject: str, record_status: str, locked: set[str] | frozenset[str], *, reason: str) -> None:
    """Raise ``409`` when ``record_status`` is one the workflow has locked.

    Args:
        subject: The thing being deleted, as it should read in the sentence.
        record_status: The record's current status value.
        locked: The statuses that make the record evidence.
        reason: Why the register keeps it, appended as its own sentence.

    Raises:
        HTTPException: 409 when the record is in a locked state.
    """
    if record_status not in locked:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"This {subject} is {record_status} and cannot be deleted. {reason}",
    )
