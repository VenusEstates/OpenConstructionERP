# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""The worked examples printed in the BVBS guideline, version 3.1 of May 2021.

These are the guideline's own test data - the section it heads "Beispiele mit
Barcodes - Testdaten" - transcribed from the published document. They are the
only ground truth available for this format, and the checksum makes them
self-verifying: a transcription slip in any record changes the sum, so a
record that both reproduces its printed checksum and matches its printed
geometry has been copied correctly.

Three of the guideline's own examples do not agree with the guideline, and all
three are recorded here rather than quietly corrected:

* BF2D example 2 prints its geometry block twice. Standing alone it reads
  ``Gl100@w180@l800@w180@l100@w0@``; inside the complete record on the same
  page it reads ``l600`` for the middle leg. The checksum settles it - only
  ``l600`` reproduces the printed value 71 - and so does the arithmetic, since
  the note beside the drawing gives the overall length as 800 mm and
  100 + 600 + 100 is 800. The standalone line carries the total where the leg
  belongs.

* The BFGT example prints a checksum block, and its value does not satisfy the
  guideline's own checksum rule. The guideline's checksum table is consistent
  with that: it marks the checksum block for every super-group except BFGT.
  :data:`BFGT_EXAMPLE` is kept apart from the verified records for that reason,
  and :mod:`tests.modules.rebar_schedule.test_abs_checksum` pins the
  discrepancy rather than papering over it.

* The spacer example, BF2D example 11, omits two header fields the guideline's
  own applicability table marks as required for BF2D: the weight ``e`` and the
  author ``v``. Its checksum is correct, so the record is exactly what the
  guideline meant to print. It is listed in
  :data:`HEADER_INCOMPLETE_EXAMPLES` so the header rule can be held strict -
  the guideline's text is unambiguous that every applicable identifier must be
  present - without the sweep over the published data failing.

Also worth knowing when reading the document: the complete record printed for
BF2D example 1 renders the leg identifier of ``l600`` as a capital I. Field
identifiers are lowercase by definition, the standalone geometry line on the
same page uses the lowercase letter, and only the lowercase letter reproduces
the printed checksum.
"""

#: Records that reproduce their own printed checksum, keyed by the label the
#: guideline gives them.
VERIFIED: dict[str, str] = {
    "bf2d-1": "BF2D@HjTestPDF@r417@ia@p1@l1000@n10@e0.888@d12@gB500A@s48@v@Gl400@w90@l600@w0@C72@",
    "bf2d-2-hooks": ("BF2D@HjTestPDF@r417@ia@p1@l800@n10@e0.710@d12@gB500A@s48@v@Gl100@w180@l600@w180@l100@w0@C71@"),
    "bf2d-3-cranked": (
        "BF2D@HjTestPDF@r417@ia@p1@l1224@n10@e1.087@d12@gB500A@s48@v@"
        "Gl100@w90@l300@w45@l424@w-45@l300@w-90@l100@w0@C82@"
    ),
    "bf2d-4-arc": "BF2D@HjTestPDF@r417@ia@p1@l1428@n10@e1.268@d12@gB500A@s48@v@Gl400@w0@r400@w90@w0@l400@w0@C79@",
    "bf2d-5-arc-opening-angle": (
        "BF2D@HjTestPDF@r417@ia@p1@l1428@n10@e1.268@d12@gB500A@s48@v@Gl400@w45@r400@w90@w45@l400@w0@C93@"
    ),
    "bf2d-6-coupler": (
        "BF2D@HjTestPDF@r417@ia@p1@l200@n1@e0.178@d12@gB500A@s48@v@Gl200@w0@MaLenton@bA12@c1@n@o@p@C78@"
    ),
    "bf2d-7-thread": (
        "BF2D@HjTestPDF@r417@ia@p1@l350@n1@e0.311@d12@gB500A@s48@v@Gl150@w90@l200@w0@MaAncon@bTTS@c2@n@o@p@C90@"
    ),
    "bf2d-8-coupler-and-thread": (
        "BF2D@HjTestPDF@r417@ia@p1@l200@n1@e0.178@d12@gB500A@s48@v@Gl200@w0@MaLenton@bA12@c1@nLenton@oP13@p2@C88@"
    ),
    "bf2d-9a-staggered": "BF2D@HjTestPDF@r417@ia@p10.1@l700@n1@e0.522@d12@gB500A@s48@v@c10@Gl400@w90@l300@w0@C65@",
    "bf2d-9b-staggered": "BF2D@HjTestPDF@r417@ia@p10.2@l1000@n1@e0.888@d12@gB500A@s48@v@c10@Gl400@w90@l600@w0@C68@",
    "bf2d-9c-staggered": "BF2D@HjTestPDF@r417@ia@p10.3@l1300@n1@e1.154@d12@gB500A@s48@v@c10@Gl400@w90@l900@w0@C74@",
    "bf2d-10-running-metre": "BF2D@HjTestPDF@r417@ia@p1@l500000@n1@e444.000@d12@gB500A@s48@v@Gl500000@w0@C81@",
    "bf2d-11-spacers": "BF2D@HjTestPDF@r417@ia@p1@l1334@n1@d10@gIV@s40@a1@At6@p140@p667@p1194@C78@",
    "bf3d-1": (
        "BF3D@HjTestPDF@r417@ia@p1@l1500@n10@e1.332@d12@gB500A@s48@v@"
        "Gx294@y0@z0@x0@y288@z0@x0@y0@z288@x0@y-288@z0@x294@y0@z0@C82@"
    ),
    "bfwe-1-square-column": (
        "BFWE@HjTestPDF@r417@ia@p1@l32856@n10@e29.176@d12@gB500A@s48@v@"
        "Gl340@w90@l340@w90@l340@w90@l340@w90@n6@g100@n12@g200@n6@g100@C69@"
    ),
    "bfwe-2-round-column": (
        "BFWE@HjTestPDF@r417@ia@p1@l45398@n10@e40.313@d12@gB500A@s48@v@Gr300@w360@n6@g100@n12@g200@n6@g100@C69@"
    ),
    "bfma-1-stock-mesh": "BFMA@HjTestPDF@r417@ia@p1@l4000@n10@e35.28@gB500A@s48@mQ257@b2150@v@C68@",
    "bfma-2-drawn-mesh": (
        "BFMA@HjTestPDF@r417@ia@p1@l5000@n10@e36.695@gB500A@s48@mZMPDF@b3000@v@"
        "Yd6d@x175;175@y600;600@l4400;4400@e25,1;250,1@"
        "Yd6d@x700;700@y0;0@l5000;5000@e250,9@"
        "Xd5@x500@y250@l2500@e250,1@"
        "Xd5@x0@y750@l3000@e250,16@C68@"
    ),
    "bfma-3-bent-drawn-mesh": (
        "BFMA@HjTestPDF@r417@ia@p1@l5000@n10@e36.695@gB500A@s48@mZMPDF@b3000@v@"
        "Gyl200@w-90@l2000@w90@l600@w90@l2000@w-90@l200@w0@"
        "Yd6d@x175;175@y600;600@l4400;4400@e25,1;250,1@"
        "Yd6d@x700;700@y0;0@l5000;5000@e250,9@"
        "Xd5@x500@y250@l2500@e250,1@"
        "Xd5@x0@y750@l3000@e250,16@C73@"
    ),
    "bfau-1-spacer-strip": "BFAU@HjTestPDF@r417@i@p1@l2000@n5@e4.5@mDS16@h160@C68@",
    "bfau-2-support-cage": "BFAU@HjTestPDF@r417@i@p1@l1@n150@e1.514@mDBV-200-L1/F/T@h200@C88@",
}

#: The lattice-girder example, kept apart because its printed checksum does not
#: satisfy the guideline's own checksum rule. See the module docstring.
BFGT_EXAMPLE = "BFGT@HjTestPDF@r417@ia@p1@l2356@n1@e4.302@mKT80910@h90@a1@Ex10@y300@l2356@w0@z38@C81@"

#: The value the guideline prints for that record, and the value its own rule
#: produces for the same characters.
BFGT_PRINTED_CHECKSUM = 81
BFGT_COMPUTED_CHECKSUM = 91

#: Published records whose header omits an identifier the applicability table
#: requires. See the module docstring.
HEADER_INCOMPLETE_EXAMPLES: frozenset[str] = frozenset({"bf2d-11-spacers"})

#: The header fields that one record leaves out: the weight and the author.
HEADER_INCOMPLETE_MISSING_FIELDS: list[str] = ["e", "v"]

#: The geometry block BF2D example 2 prints standing alone, which disagrees
#: with the same example's complete record and with its own drawing note.
BF2D_EXAMPLE_2_STANDALONE_GEOMETRY = "Gl100@w180@l800@w180@l100@w0@"

#: The checksum illustration the guideline works through by hand on page 12.
CHECKSUM_ILLUSTRATION = ("abcde@C", 78)


def spec_file() -> bytes:
    """The verified records as one ABS file, with CRLF record terminators."""
    return "".join(f"{record}\r\n" for record in VERIFIED.values()).encode("ascii")
