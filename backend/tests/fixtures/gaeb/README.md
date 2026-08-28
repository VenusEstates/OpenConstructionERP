# GAEB test fixtures

Every file in this directory is our own work. None of it is a copy of a file
published by GAEB, DIN or BVBS. `docs/standards/GAEB.md` explains why we work
that way and what we assert about our GAEB support.

## oce_conformance_x83.x83 and oce_conformance_x84.x84

The pair our importer and validators are regression-tested against. They are
produced by `backend/scripts/generate_gaeb_conformance_fixtures.py`, which is
also where the arithmetic and the reasoning live; run it to regenerate them.

X83 is an Angebotsaufforderung (call for bids, unpriced), X84 is the matching
Angebotsabgabe (priced bid), both in namespace
`http://www.gaeb.de/GAEB_DA_XML/DA8x/3.3`. They share one LV: 12 categories on
a three-part OZ mask (3 / 3 / 4) and 27 positions, plus a Zuschlagsposition in
the X84.

They deliberately carry the shapes that broke the importer, because a fixture
that avoids the hard cases stops testing the bugs it was written for:

- **Indexpositionen.** Four items share an `RNoPart` with their base position
  and differ only in `RNoIndex` (`1`, `A`, `y`, `z`). The index is part of the
  Ordnungszahl; dropping it collapses distinct positions onto one ordinal, and
  the persistence layer then answers 409.
- **An embedded graphic.** One Langtext carries an inline base64 image, which
  must be stripped from the description without taking the human text with it,
  and must never reach the persisted metadata. The payload is a synthetic
  JFIF-headed byte stream, not a photograph.
- **A level-3 OZ.** Ordinals such as `001.001.0010` used to be rejected by an
  ordinal rule that only understood two levels.
- **Bedarfspositionen.** Three items carry `Provis` and no price, which the
  pricing rule used to report as an error.
- **An X84 with no quantities.** Every priced item states `UP` and `IT` and no
  `Qty` at all. That is the arrangement that once imported as 0.00.
- **A partial markup base.** The `MarkupItem` applies 10 percent to
  `ITMarkup` 850,000.00, which is the sum of the surcharged positions and not
  of the whole bill, so its exact `IT` of 85,000.00 is the figure that has to
  survive. Re-deriving the percentage against the full direct cost inflates the
  total by 106,500.00.

The money is chosen so it can be checked by hand: 27 item totals summing to
1,915,000.00, plus 85,000.00 of markup, equals the declared grand total of
2,000,000.00.

Both files validate against the schema set GAEB publishes for version 3.3.
`tests/unit/test_gaeb_export_xsd.py` performs that check whenever a local copy
of that schema set is available; see its module docstring for the two ways to
provide one. Without a copy those tests skip and the fixtures are still checked
against the profile schema we ship in `app/modules/boq/gaeb_profile/`.

## frankfurt_rohbau_x83.x83

The everyday-shaped companion: a clean German X83 in the same namespace, for a
project named "Bürogebäude Frankfurt Europaviertel", one Gewerk (Rohbau) with
five sub-sections (Baustelleneinrichtung, Erdarbeiten, Beton- und
Stahlbetonarbeiten, Mauerwerksarbeiten, Abdichtungsarbeiten), 21 positions with
real German construction texts, units m2/m3/psch/St/kg and non-round quantities
(386.500, 57522.500). It carries no prices and no company names.

Where the conformance pair stresses edge cases, this file is the ordinary LV a
reader will meet on a Tuesday. Used by `tests/unit/test_gaeb_frankfurt_fixture.py`
and the import integration tests.
