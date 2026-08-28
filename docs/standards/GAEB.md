# GAEB DA XML in OpenConstructionERP

OpenConstructionERP reads and writes GAEB DA XML 3.3 natively. This page
records what our implementation is built from, so that anyone who needs to
know can find out without asking us.

## What we ship

- A reader for GAEB DA XML exchange phases X83 (Angebotsaufforderung) and
  X84 (Angebotsabgabe), in `backend/app/modules/boq/importers/gaeb_xml.py`.
- A writer for the same two phases, in `backend/app/modules/boq/router.py`.
- A profile schema, `backend/app/modules/boq/gaeb_profile/`, in two files,
  one per exchange phase.
- Validation rules for GAEB documents, in
  `backend/app/core/validation/rules/`.
- Test fixtures under `backend/tests/fixtures/gaeb/`.

Every one of those is our own work.

## What we do not ship, and why

The GAEB DA XML specification is published by the Gemeinsamer Ausschuss
Elektronik im Bauwesen. Its Fachdokumentation carries the line
`© 2023 by DIN Deutsches Institut für Normung e. V.` on the title page of
the 3.3 edition, so the specification is a copyrighted work with a named
owner.

The schema files themselves, and the conformance files (Prüfdateien) used
in the BVBS certification programme, can be downloaded from the publishers
without payment or registration. Free to download is not the same as free
to redistribute, and we found no statement anywhere granting a right to
redistribute: not on the download pages, not in the site imprint, not in
the Fachdokumentation, and not inside the schema files, which carry no
notice of any kind. Where terms are silent, the conservative reading is
that no licence was granted. So we do not put those files in this
repository, in our packages, or in our installers.

That is a decision about redistribution, not about use. Downloading the
publisher's schema and validating a document against it is exactly what
the publisher offers it for, and our test suite does that. It fetches the
schema at test time when asked to, and never commits it.

We also note, and did not rely on, the argument that a work of a body
seated at a federal agency might fall under section 5 of the German
Copyright Act. The GAEB office is hosted by the Bundesamt für Bauwesen und
Raumordnung, which makes the question a real one, but the answer is not
settled enough to build on.

## What our implementation is built from

The element names, the document structure, the exchange phases, the
Ordnungszahl mask and the data types are facts about a public interchange
format. They are published so that software can implement them, and an
implementation has to use them or it does not interoperate. We took them
from the Fachdokumentation that GAEB publishes and from documents that
real German tendering systems produce.

What we did not do is take the publisher's expression of those facts. Our
profile schema was written from the documented element model and from our
own reader and writer. It is not an edited copy of anyone's file. It uses
type names of our own choosing, it covers only the subset of the standard
this product supports, and it carries none of the editorial history that
the published schema files contain.

## What we assert, and what we do not

We assert that our reader and writer produce and consume documents that
conform to GAEB DA XML 3.3 for exchange phases X83 and X84, and that this
is verified against the schema set the publisher issues rather than only
against our own. `backend/tests/unit/test_gaeb_export_xsd.py` runs that
check whenever a copy of the published schema is available locally, which
in continuous integration it is.

We do not claim certification. BVBS runs a certification programme for
GAEB DA XML; we have not been through it, and nothing in this product
should be read as saying otherwise. Passing schema validation is a
narrower statement than a certificate, and it is the only statement we
make.

Our profile schema describes a subset. A document that satisfies it is
valid GAEB DA XML 3.3. The reverse does not hold: the standard allows a
great deal our profile does not describe, and a file using those parts is
not defective because our schema rejects it. To check a document against
the whole standard, validate it against the schema set GAEB publishes.

## Sources

- GAEB DA XML downloads, including the 3.3 schema packages and the
  Fachdokumentation: <https://www.gaeb.de/de/service/downloads/gaeb-datenaustausch/>
- GAEB DA XML product page, which states that the technical information
  needed to implement the format is available free of charge:
  <https://www.gaeb.de/en/products/gaeb-data-exchange/>
- BVBS certification programme and its conformance files:
  <https://www.bvbs.de/zertifizierungen/>

Questions about this page: info@datadrivenconstruction.io
