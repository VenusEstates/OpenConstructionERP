# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""The ABS checksum rule, against the guideline's own worked examples.

The checksum is the only self-verifying part of the format, which makes it the
oracle for everything else: a record that reproduces its printed checksum has
been transcribed correctly, and a codec that reproduces the checksum for a
record it built has laid the characters out the way the standard asks.
"""

import pytest

from app.modules.rebar_schedule.abs_format import (
    compute_checksum,
    parse_record,
    render_record,
    split_checksum,
    verify_checksum,
)
from tests.modules.rebar_schedule import spec_examples


def test_hand_worked_illustration_reproduces_the_printed_value() -> None:
    """The guideline works one string through by hand and prints the answer."""
    text, expected = spec_examples.CHECKSUM_ILLUSTRATION
    assert compute_checksum(text) == expected


@pytest.mark.parametrize(("label", "record"), sorted(spec_examples.VERIFIED.items()))
def test_every_verified_example_reproduces_its_printed_checksum(label: str, record: str) -> None:
    assert verify_checksum(record), label


def test_the_lattice_girder_example_does_not_satisfy_the_guidelines_own_rule() -> None:
    """The BFGT example is the one printed record whose checksum does not hold.

    Pinned rather than corrected. The guideline's checksum table does not mark
    BFGT as carrying a checksum block at all, so the example is inconsistent
    with its own document, and a reader who trusts the example over the rule
    would build a codec that rejects conforming files.
    """
    body, declared = split_checksum(spec_examples.BFGT_EXAMPLE)
    assert declared == spec_examples.BFGT_PRINTED_CHECKSUM
    assert compute_checksum(body + "C") == spec_examples.BFGT_COMPUTED_CHECKSUM
    assert not verify_checksum(spec_examples.BFGT_EXAMPLE)


def test_the_standalone_geometry_of_example_two_contradicts_its_own_record() -> None:
    """One example prints its geometry twice, with different middle legs.

    The complete record checksums; the standalone line does not, and its own
    drawing note gives the overall length as 800 mm, which the record's
    100 + 600 + 100 satisfies and the standalone line's 100 + 800 + 100 does
    not. The record is the one to follow.
    """
    record = spec_examples.VERIFIED["bf2d-2-hooks"]
    standalone = spec_examples.BF2D_EXAMPLE_2_STANDALONE_GEOMETRY
    assert "Gl100@w180@l600@w180@l100@w0@" in record
    assert standalone not in record
    swapped = record.replace("Gl100@w180@l600@w180@l100@w0@", standalone)
    assert not verify_checksum(swapped)


def test_the_checksum_always_lands_in_the_range_the_rule_allows() -> None:
    """96 minus a value modulo 32 can only be 65 through 96."""
    for record in spec_examples.VERIFIED.values():
        assert 65 <= parse_record(record).declared_checksum <= 96


def test_a_record_with_no_checksum_block_reports_no_declared_value() -> None:
    body, declared = split_checksum("BF2D@HjTest@r1@i@p1@l100@n1@e0.1@d12@gB500A@s48@v@Gl100@w0@")
    assert declared is None
    assert not verify_checksum(body)


def test_a_single_altered_character_is_caught() -> None:
    """A digit changed by one shifts the sum by one, which the rule sees."""
    record = spec_examples.VERIFIED["bf2d-1"]
    damaged = record.replace("@l1000@", "@l1001@")
    assert verify_checksum(record)
    assert not verify_checksum(damaged)


def test_separators_and_spaces_are_invisible_to_the_checksum() -> None:
    """A property of the rule, not of this implementation, and worth knowing.

    The sum is taken modulo 32, and both '@' (64) and the space (32) are exact
    multiples of 32. Dropping either leaves the checksum unchanged, so the rule
    catches transcription damage and is no defence against tampering.
    """
    record = spec_examples.VERIFIED["bf2d-1"]
    body, declared = split_checksum(record)
    assert compute_checksum(body + "C") == declared
    assert compute_checksum(body.replace("@v@", "@v@@") + "C") == declared
    assert compute_checksum(body + " " + "C") == declared


def test_rendering_a_record_produces_the_checksum_the_guideline_prints() -> None:
    """Building example 1 from its blocks arrives at the printed record."""
    rendered = render_record(
        "BF2D",
        ["HjTestPDF@r417@ia@p1@l1000@n10@e0.888@d12@gB500A@s48@v@", "Gl400@w90@l600@w0@"],
    )
    assert rendered == spec_examples.VERIFIED["bf2d-1"]


def test_rendering_rejects_a_super_group_the_standard_does_not_define() -> None:
    with pytest.raises(ValueError, match="unknown super-group"):
        render_record("BF4D", ["Hj@r@i@p@l@n@e@d@g@s@v@"])
