# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Generate the in-house GAEB DA XML 3.3 conformance fixtures.

These two files replace the publisher's certification files in our test
suite. They are written here, from the element model documented in the
public GAEB DA XML 3.3 Fachdokumentation, so that everything we commit is
our own text and our own numbers. No byte of any file published by GAEB,
DIN or BVBS is copied into them.

The pair deliberately reproduces the *shapes* that broke our importer,
because a fixture that avoids the hard cases stops testing the bugs it was
written for:

* Indexpositionen. Four items share an ``RNoPart`` with their base position
  and are distinguished only by ``RNoIndex`` (``1``, ``A``, ``y``, ``z``).
  An importer that drops the index collapses distinct positions onto one
  ordinal.
* An embedded graphic. One Langtext carries an inline base64 image, which
  must be stripped from the description without taking the human text with
  it, and must never reach the persisted metadata.
* A three-part OZ mask (3 / 3 / 4), which yields level-3 ordinals such as
  ``001.001.0010`` that a naive ordinal rule used to reject.
* Bedarfspositionen (``Provis``), which legitimately carry no price and
  used to be reported as pricing errors.
* An X84 whose items carry ``UP`` and ``IT`` but no ``Qty`` at all, plus a
  ``MarkupItem`` whose percentage applies to a partial base. This is the
  exact arrangement that once imported as 0.00.

Money in the X84 is chosen so the arithmetic is exact and checkable by
hand: every unit price is a round figure, every quantity is an integer, the
27 item totals sum to 1,915,000.00, the single markup adds 85,000.00 and
the declared grand total is 2,000,000.00.

Run::

    cd backend
    python scripts/generate_gaeb_conformance_fixtures.py

Both files are then validated by ``tests/unit/test_gaeb_export_xsd.py``
against the official GAEB 3.3 schema when it is available locally, and
against our own profile schema always.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "gaeb"

NS = "http://www.gaeb.de/GAEB_DA_XML/DA{dp}/3.3"

# Three-part OZ mask: two 3-digit hierarchy levels and a 4-digit item number,
# which is what produces ordinals of the shape 001.001.0010.
OZ_MASK = (("BoQLevel", "3"), ("BoQLevel", "3"), ("Item", "4"))

GRAND_TOTAL = Decimal("2000000.00")
MARKUP_BASE = Decimal("850000.00")
MARKUP_PERCENT = Decimal("10.000000")
MARKUP_AMOUNT = Decimal("85000.00")
ITEM_TOTAL = GRAND_TOTAL - MARKUP_AMOUNT  # 1,915,000.00


@dataclass
class Item:
    """One Position of the fixture LV."""

    rno_part: str
    rno_index: str | None
    unit: str
    quantity: int
    unit_price: Decimal
    outline: str
    detail: str
    provisional: bool = False
    graphic: bool = False

    @property
    def total(self) -> Decimal:
        return (Decimal(self.quantity) * self.unit_price).quantize(Decimal("0.01"))


@dataclass
class Category:
    """One BoQCtgy (Titel / Los) of the fixture LV."""

    rno_part: str
    label: str
    children: list[Category] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)


def _i(
    rno_part: str,
    unit: str,
    quantity: int,
    unit_price: str,
    outline: str,
    detail: str,
    *,
    index: str | None = None,
    provisional: bool = False,
    graphic: bool = False,
) -> Item:
    return Item(
        rno_part=rno_part,
        rno_index=index,
        unit=unit,
        quantity=quantity,
        unit_price=Decimal(unit_price),
        outline=outline,
        detail=detail,
        provisional=provisional,
        graphic=graphic,
    )


def build_lv() -> list[Category]:
    """The fixture LV: 12 categories, 27 items, one markup slot.

    The German position texts are written for this fixture. They describe
    ordinary building work in the wording an estimator would use, which is
    what makes the file a useful regression input.
    """
    return [
        Category(
            "001",
            "Rohbauarbeiten",
            children=[
                Category(
                    "001",
                    "Erdarbeiten",
                    items=[
                        _i(
                            "0010",
                            "m3",
                            2500,
                            "20.000",
                            "Oberboden abtragen",
                            "Oberboden abtragen, Dicke bis 30 cm, Boden der Klasse 1 nach DIN 18300, "
                            "seitlich auf dem Grundstueck lagern.",
                        ),
                        _i(
                            "0010",
                            "m3",
                            1200,
                            "25.000",
                            "Oberboden abtragen, erschwerte Zufahrt",
                            "Wie vorstehende Position, jedoch im Bereich der eingeschraenkten Zufahrt "
                            "an der Nordseite, Abtrag in Handschachtung ergaenzend.",
                            index="1",
                        ),
                        _i(
                            "0010",
                            "m3",
                            800,
                            "30.000",
                            "Oberboden abtragen, Abfuhr",
                            "Wie Grundposition, jedoch einschliesslich Verladen und Abfahren zur "
                            "Wiederverwertung, Entfernung bis 15 km.",
                            index="A",
                        ),
                        _i(
                            "0020",
                            "m3",
                            3400,
                            "25.000",
                            "Baugrube ausheben",
                            "Baugrube ausheben, Boden der Klassen 3 bis 5 nach DIN 18300, Aushub "
                            "seitlich lagern, Boeschung 1:1 herstellen und vorhalten.",
                        ),
                    ],
                ),
                Category(
                    "002",
                    "Beton- und Stahlbetonarbeiten",
                    items=[
                        _i(
                            "0010",
                            "m3",
                            900,
                            "150.000",
                            "Sohlplatte in Ortbeton",
                            "Sohlplatte in Ortbeton C25/30, Dicke 30 cm, Expositionsklasse XC2, "
                            "Oberflaeche abgezogen und geglaettet. Schalungs- und Bewehrungsplan "
                            "siehe beigefuegte Skizze.",
                            graphic=True,
                        ),
                        _i(
                            "0020",
                            "t",
                            400,
                            "1250.000",
                            "Betonstahl liefern und einbauen",
                            "Betonstahl B500B liefern, biegen und einbauen, Stabstahl und Matten, "
                            "einschliesslich Abstandhalter und Verbuegelung.",
                        ),
                        _i(
                            "0030",
                            "m2",
                            4300,
                            "50.000",
                            "Wandschalung beidseitig",
                            "Wandschalung beidseitig aus Systemschalung, Schalhaut saugend, "
                            "Sichtbetonklasse SB2, einschliesslich Auf- und Abbau.",
                        ),
                    ],
                ),
            ],
        ),
        Category(
            "002",
            "Ausbauarbeiten",
            children=[
                Category(
                    "001",
                    "Mauerwerksarbeiten",
                    items=[
                        _i(
                            "0010",
                            "m2",
                            1800,
                            "75.000",
                            "Aussenwand Mauerwerk 36,5 cm",
                            "Aussenwand aus Planziegeln, Dicke 36,5 cm, Rohdichteklasse 0,8, "
                            "im Duennbettmoertel versetzt.",
                        ),
                        _i(
                            "0020",
                            "m2",
                            2200,
                            "45.000",
                            "Innenwand Mauerwerk 17,5 cm",
                            "Innenwand aus Kalksandstein, Dicke 17,5 cm, Steinfestigkeitsklasse 12, "
                            "einschliesslich Verzahnung an den Anschluessen.",
                        ),
                        _i(
                            "0030",
                            "St",
                            140,
                            "250.000",
                            "Fenstersturz einbauen",
                            "Fertigteilsturz liefern und einbauen, Laenge bis 2,00 m, "
                            "einschliesslich Auflagerdruckverteilung.",
                        ),
                        _i(
                            "0040",
                            "m",
                            600,
                            "50.000",
                            "Ringanker herstellen",
                            "Ringanker in Ortbeton C20/25 herstellen, Querschnitt 20/24 cm, "
                            "einschliesslich Schalung und Bewehrung.",
                        ),
                    ],
                ),
                Category(
                    "002",
                    "Abdichtungsarbeiten",
                    items=[
                        _i(
                            "0010",
                            "m2",
                            1600,
                            "35.000",
                            "Bauwerksabdichtung erdberuehrt",
                            "Bauwerksabdichtung gegen Bodenfeuchte auf erdberuehrten Waenden, "
                            "zweilagig aufgebracht, einschliesslich Voranstrich.",
                        ),
                        _i(
                            "0020",
                            "m2",
                            1600,
                            "20.000",
                            "Schutzschicht aufbringen",
                            "Schutz- und Draenschicht aus Noppenbahn mit Gleitfolie auf der "
                            "Abdichtung befestigen, Stoesse ueberlappen.",
                        ),
                        _i(
                            "0030",
                            "m",
                            420,
                            "125.000",
                            "Dehnfuge herstellen",
                            "Dehnfuge herstellen, Fugenband einbauen, Fugenbreite 20 mm, "
                            "einschliesslich Hinterfuellprofil.",
                        ),
                    ],
                ),
            ],
        ),
        Category(
            "003",
            "Technische Anlagen",
            children=[
                Category(
                    "001",
                    "Entwaesserung",
                    items=[
                        _i(
                            "0010",
                            "m",
                            900,
                            "100.000",
                            "Grundleitung verlegen",
                            "Grundleitung aus Gussrohr DN 150 im Erdreich verlegen, "
                            "einschliesslich Sandbettung und Rohrumhuellung.",
                        ),
                        _i(
                            "0020",
                            "St",
                            60,
                            "500.000",
                            "Revisionsschacht setzen",
                            "Revisionsschacht DN 400 setzen, einschliesslich Abdeckung "
                            "Klasse B 125 und Anschluss an die Grundleitung.",
                        ),
                        _i(
                            "0030",
                            "m2",
                            1400,
                            "50.000",
                            "Dachabdichtung herstellen",
                            "Dachabdichtung zweilagig aus Bitumenbahnen, oberste Lage "
                            "beschiefert, einschliesslich Anschluss an aufgehende Bauteile.",
                        ),
                        _i(
                            "0040",
                            "St",
                            40,
                            "250.000",
                            "Dachablauf einbauen",
                            "Dachablauf DN 100 mit Anschlussmanschette einbauen, "
                            "einschliesslich Laubfang und Daemmkoerper.",
                        ),
                    ],
                ),
                Category(
                    "002",
                    "Elektrotechnik",
                    items=[
                        _i(
                            "0010",
                            "m",
                            2400,
                            "25.000",
                            "Leerrohr verlegen",
                            "Leerrohr M25 auf Rohdecke verlegen und befestigen, "
                            "einschliesslich Zugdraht und Abzweigdosen.",
                        ),
                        _i(
                            "0020",
                            "St",
                            560,
                            "100.000",
                            "Schalterdose setzen",
                            "Geraetedose in Mauerwerk setzen, einschliesslich Bohren der Dosenoeffnung und Einputzen.",
                        ),
                        _i(
                            "0030",
                            "St",
                            12,
                            "2500.000",
                            "Unterverteilung stellen",
                            "Unterverteilung als Standschrank stellen und anschliessen, "
                            "einschliesslich Beschriftung und Pruefprotokoll.",
                        ),
                    ],
                ),
            ],
        ),
        Category(
            "999",
            "Bedarf und Nachtrag",
            children=[
                Category(
                    "998",
                    "Bedarfspositionen",
                    items=[
                        _i(
                            "0010",
                            "m3",
                            400,
                            "50.000",
                            "Bodenaustausch bei Bedarf",
                            "Nicht tragfaehigen Boden ausbauen und durch Frostschutzmaterial "
                            "ersetzen, Ausfuehrung nur auf gesonderte Anordnung.",
                            provisional=True,
                        ),
                        _i(
                            "0020",
                            "m2",
                            300,
                            "125.000",
                            "Wasserhaltung bei Bedarf",
                            "Offene Wasserhaltung in der Baugrube vorhalten und betreiben, "
                            "Ausfuehrung nur auf gesonderte Anordnung.",
                            provisional=True,
                        ),
                        _i(
                            "0030",
                            "St",
                            25,
                            "200.000",
                            "Baugrunduntersuchung bei Bedarf",
                            "Zusaetzliche Rammsondierung ausfuehren und auswerten, "
                            "Ausfuehrung nur auf gesonderte Anordnung.",
                            provisional=True,
                        ),
                    ],
                ),
                Category(
                    "999",
                    "Nachtragspositionen",
                    items=[
                        _i(
                            "9990",
                            "psch",
                            1,
                            "20000.000",
                            "Baustelle raeumen",
                            "Baustelle nach Fertigstellung raeumen, Restmaterial entsorgen "
                            "und die Flaechen besenrein uebergeben.",
                        ),
                        _i(
                            "9999",
                            "St",
                            10,
                            "500.000",
                            "Nachtrag Sockelblech",
                            "Sockelblech aus Aluminium liefern und montieren, Nachtrag nach Anordnung der Bauleitung.",
                            index="y",
                        ),
                        _i(
                            "9999",
                            "St",
                            6,
                            "500.000",
                            "Nachtrag Kantenschutz",
                            "Kantenschutzprofil liefern und montieren, Nachtrag nach Anordnung der Bauleitung.",
                            index="z",
                        ),
                    ],
                ),
            ],
        ),
    ]


def _synthetic_jfif_blob() -> str:
    """A small JFIF-headed byte stream, base64 encoded.

    Not a photograph and not decodable as an image: it is a JFIF header
    followed by filler and an end-of-image marker. What the test needs is a
    base64 payload that begins the way every JPEG begins, so the stripping
    path is exercised on a realistic prefix rather than on arbitrary text.
    """
    header = bytes(
        [
            0xFF,
            0xD8,  # SOI
            0xFF,
            0xE0,  # APP0
            0x00,
            0x10,
            0x4A,
            0x46,
            0x49,
            0x46,
            0x00,  # "JFIF\0"
            0x01,
            0x01,
            0x00,
            0x00,
            0x01,
            0x00,
            0x01,
            0x00,
            0x00,
        ]
    )
    filler = bytes((i * 37 + 11) % 256 for i in range(4200))
    return base64.b64encode(header + filler + bytes([0xFF, 0xD9])).decode("ascii")


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ml(tag: str, text: str, indent: str) -> list[str]:
    """Render a multi-line text element as paragraphs of spans."""
    out = [f"{indent}<{tag}>"]
    for line in text.split("\n"):
        out.append(f"{indent}  <p><span>{_esc(line)}</span></p>")
    out.append(f"{indent}</{tag}>")
    return out


def _item_xml(item: Item, dp: str, seq: list[int], indent: str) -> list[str]:
    seq[0] += 1
    handle = f"oceI{seq[0]:04d}"
    attrs = f'ID="{handle}" RNoPart="{item.rno_part}"'
    if item.rno_index:
        attrs += f' RNoIndex="{item.rno_index}"'
    out = [f"{indent}<Item {attrs}>"]

    if dp == "83":
        # Provis precedes the quantity in the Item sequence.
        if item.provisional:
            out.append(f"{indent}  <Provis>WithTotal</Provis>")
        out.append(f"{indent}  <Qty>{item.quantity}.000</Qty>")
        out.append(f"{indent}  <QU>{item.unit}</QU>")
    else:
        # An X84 states the binding unit price and the binding line total.
        # It carries no Qty at all, which is what made the money vanish.
        out.append(f"{indent}  <UP>{item.unit_price}</UP>")
        out.append(f"{indent}  <IT>{item.total}</IT>")

    # CompleteText orders the Langtext before the Kurztext.
    out.append(f"{indent}  <Description>")
    out.append(f"{indent}    <CompleteText>")
    if dp == "83":
        out.append(f"{indent}      <DetailTxt>")
        out.append(f"{indent}        <Text>")
        for line in item.detail.split("\n"):
            out.append(f"{indent}          <p><span>{_esc(line)}</span></p>")
        if item.graphic:
            blob = _synthetic_jfif_blob()
            out.append(
                f'{indent}          <p><image align="left" Type="image/jpeg" '
                f'Name="sohlplatte-skizze.jpg">{blob}</image></p>'
            )
        out.append(f"{indent}        </Text>")
        out.append(f"{indent}      </DetailTxt>")
    else:
        # The X84 schema restricts DetailTxt to a text complement; the long
        # text lives in the paired X83 and is not restated in the bid.
        out.append(f"{indent}      <DetailTxt/>")
    if dp == "83":
        out.append(f"{indent}      <OutlineText>")
        out.append(f"{indent}        <OutlTxt>")
        out.extend(_ml("TextOutlTxt", item.outline, f"{indent}          "))
        out.append(f"{indent}        </OutlTxt>")
        out.append(f"{indent}      </OutlineText>")
    out.append(f"{indent}    </CompleteText>")
    out.append(f"{indent}  </Description>")
    out.append(f"{indent}</Item>")
    return out


def _markup_xml(indent: str) -> list[str]:
    """The Zuschlagsposition of the X84.

    Its percentage applies to a partial base (the surcharged positions
    only), so the exact IT is the figure that must survive an import.
    """
    out = [f'{indent}<MarkupItem ID="oceM0001" RNoPart="0040">']
    out.append(f"{indent}  <ITMarkup>{MARKUP_BASE}</ITMarkup>")
    out.append(f"{indent}  <Markup>{MARKUP_PERCENT}</Markup>")
    out.append(f"{indent}  <IT>{MARKUP_AMOUNT}</IT>")
    out.append(f"{indent}  <Description>")
    out.append(f"{indent}    <CompleteText>")
    out.append(f"{indent}      <DetailTxt/>")
    out.append(f"{indent}    </CompleteText>")
    out.append(f"{indent}  </Description>")
    out.append(f"{indent}</MarkupItem>")
    return out


def _category_xml(
    ctgy: Category,
    dp: str,
    seq: list[int],
    ctgy_seq: list[int],
    indent: str,
    markup_at: tuple[str, str] | None,
    path: tuple[str, ...] = (),
) -> list[str]:
    ctgy_seq[0] += 1
    here = path + (ctgy.rno_part,)
    out = [f'{indent}<BoQCtgy ID="oceC{ctgy_seq[0]:04d}" RNoPart="{ctgy.rno_part}">']
    if dp == "83":
        # A priced bid carries no category labels; they live in the paired
        # X83 the bidder was sent.
        out.extend(_ml("LblTx", ctgy.label, f"{indent}  "))
    out.append(f"{indent}  <BoQBody>")
    for child in ctgy.children:
        out.extend(_category_xml(child, dp, seq, ctgy_seq, f"{indent}    ", markup_at, here))
    if ctgy.items or (dp == "84" and markup_at == here):
        out.append(f"{indent}    <Itemlist>")
        for item in ctgy.items:
            out.extend(_item_xml(item, dp, seq, f"{indent}      "))
        if dp == "84" and markup_at == here:
            out.extend(_markup_xml(f"{indent}      "))
        out.append(f"{indent}    </Itemlist>")
    out.append(f"{indent}  </BoQBody>")
    if dp == "84":
        # A priced bid states the running total of every category.
        out.append(f"{indent}  <Totals>")
        out.append(f"{indent}    <Total>{_category_total(ctgy, markup_at, here)}</Total>")
        out.append(f"{indent}  </Totals>")
    out.append(f"{indent}</BoQCtgy>")
    return out


def _category_total(
    ctgy: Category,
    markup_at: tuple[str, str] | None,
    here: tuple[str, ...],
) -> Decimal:
    """Sum one category: its own items, its markup slot, its children."""
    total = sum((i.total for i in ctgy.items), Decimal("0.00"))
    if markup_at == here:
        total += MARKUP_AMOUNT
    for child in ctgy.children:
        total += _category_total(child, markup_at, here + (child.rno_part,))
    return total


def build_document(dp: str) -> str:
    """Render the whole fixture for one exchange phase."""
    lv = build_lv()
    items = [i for top in lv for sub in top.children for i in sub.items]
    total = sum((i.total for i in items), Decimal("0.00"))
    if total != ITEM_TOTAL:
        raise SystemExit(f"item totals are {total}, expected {ITEM_TOTAL} - adjust the LV")

    # The markup base must be the sum of the positions it actually
    # surcharges. If those drift apart the fixture stops representing the
    # bug it was written for (a percentage applied to a partial base).
    surcharged = [
        i for top in lv if top.rno_part == "001" for sub in top.children if sub.rno_part == "002" for i in sub.items
    ]
    base = sum((i.total for i in surcharged), Decimal("0.00"))
    if base != MARKUP_BASE:
        raise SystemExit(f"surcharged positions total {base}, expected {MARKUP_BASE}")
    if (base * MARKUP_PERCENT / Decimal(100)).quantize(Decimal("0.01")) != MARKUP_AMOUNT:
        raise SystemExit("markup percentage and amount disagree")

    ns = NS.format(dp=dp)
    out = ['<?xml version="1.0" encoding="UTF-8"?>']
    out.append(
        "<!-- OpenConstructionERP GAEB DA XML 3.3 conformance fixture. "
        "Authored in-house, generated by backend/scripts/"
        "generate_gaeb_conformance_fixtures.py. -->"
    )
    out.append(f'<GAEB xmlns="{ns}">')
    out.append("  <GAEBInfo>")
    out.append("    <Version>3.3</Version>")
    out.append("    <VersDate>2021-05</VersDate>")
    out.append("    <Date>2026-08-28</Date>")
    out.append("    <ProgSystem>OpenConstructionERP</ProgSystem>")
    out.append("    <ProgName>Conformance fixture</ProgName>")
    out.append("  </GAEBInfo>")
    out.append("  <PrjInfo>")
    out.append("    <NamePrj>Muster Neubau Verwaltung</NamePrj>")
    out.append("    <LblPrj>Muster Neubau Verwaltung, Bauabschnitt 1</LblPrj>")
    if dp == "83":
        out.append("    <Cur>EUR</Cur>")
    out.append("  </PrjInfo>")
    out.append("  <Award>")
    out.append(f"    <DP>{dp}</DP>")
    out.append("    <AwardInfo>")
    out.append("      <Cur>EUR</Cur>")
    out.append("      <CurLbl>EUR</CurLbl>")
    out.append("    </AwardInfo>")
    if dp == "84":
        out.append("    <CTR>")
        out.append("      <Address>")
        out.append("        <Name1>Musterbau GmbH</Name1>")
        out.append("        <Street>Musterweg 1</Street>")
        out.append("        <PCode>10115</PCode>")
        out.append("        <City>Berlin</City>")
        out.append("      </Address>")
        out.append("      <CntryType>EEA</CntryType>")
        out.append("    </CTR>")
    out.append('    <BoQ ID="oceBoQ0001">')
    out.append("      <BoQInfo>")
    out.append("        <Name>LV 001</Name>")
    if dp == "83":
        out.append("        <LblBoQ>Leistungsverzeichnis Bauabschnitt 1</LblBoQ>")
        out.append("        <Date>2026-08-28</Date>")
        out.append("        <OutlCompl>AllTxt</OutlCompl>")
    for kind, length in OZ_MASK:
        out.append("        <BoQBkdn>")
        out.append(f"          <Type>{kind}</Type>")
        out.append(f"          <Length>{length}</Length>")
        out.append("          <Num>Yes</Num>")
        out.append("        </BoQBkdn>")
    if dp == "84":
        out.append("        <Totals>")
        out.append(f"          <Total>{GRAND_TOTAL}</Total>")
        out.append("        </Totals>")
    out.append("      </BoQInfo>")
    out.append("      <BoQBody>")
    seq = [0]
    ctgy_seq = [0]
    # The surcharge sits with the work it surcharges: the Beton- und
    # Stahlbetonarbeiten, whose item totals are exactly the markup base.
    markup_at = ("001", "002")
    for top in lv:
        out.extend(_category_xml(top, dp, seq, ctgy_seq, "        ", markup_at))
    out.append("      </BoQBody>")
    out.append("    </BoQ>")
    out.append("  </Award>")
    out.append("</GAEB>")
    return "\n".join(out) + "\n"


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for dp, name in (("83", "oce_conformance_x83.x83"), ("84", "oce_conformance_x84.x84")):
        target = FIXTURE_DIR / name
        target.write_bytes(build_document(dp).encode("utf-8"))
        print(f"wrote {target} ({target.stat().st_size} bytes)")

    lv = build_lv()
    items = [i for top in lv for sub in top.children for i in sub.items]
    print(f"categories: {sum(1 + len(t.children) for t in lv)}")
    print(f"items: {len(items)}")
    print(f"item totals: {sum((i.total for i in items), Decimal('0.00'))}")
    print(f"grand total: {sum((i.total for i in items), Decimal('0.00')) + MARKUP_AMOUNT}")


if __name__ == "__main__":
    main()
