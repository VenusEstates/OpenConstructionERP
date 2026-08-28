# Importing your own cost database

Every rate the platform prices with lives in one table. The bundled national bases, a spreadsheet
you upload, and a row you type by hand all end up in the same place and behave identically
afterwards. This document covers the four ways in, the file format, and the parts that reject
your input so you can tell which one you hit.

Everything below is a route under `/api/v1/costs/`.

## Which way in

| You have | Use | Permission |
|---|---|---|
| A region we publish | `POST /load-cwicr/{db_id}` | `costs.read` |
| An Excel or CSV rate sheet | The import page, or `POST /import/file/` | `costs.create` |
| A system that can call an API | `POST /bulk/` | `costs.create` |
| One rate to add or correct | `POST /` | `costs.create` |

Loading a published base only needs `costs.read`, which is viewer level. That is deliberate:
it runs during first-run setup, it only ever fills a region that is empty, and gating it to
editor would lock a new viewer out of the step they are standing in. Every route that writes
rates you author needs `costs.create`.

**Trailing slashes are literal.** The application runs with `redirect_slashes=False`, so
`/api/v1/costs/bulk/` works and `/api/v1/costs/bulk` returns 404. The one exception worth
memorising is `load-cwicr`, which has no trailing slash. When a call 404s and the path looks
right, check the slash before you check anything else.

## Loading a region we publish

```bash
curl -X POST http://localhost:8000/api/v1/costs/load-cwicr/US_NEWYORK \
  -H "Authorization: Bearer $TOKEN"
```

`GET /base-catalog` lists every loadable base with its work-item count, currency, language and
whether it is already loaded. `GET /available-databases/` returns the same ids in a flatter
shape. Take the `db_id` from either.

The load is idempotent by region. If the region already holds rows it returns `already_loaded`
and does nothing, so re-running it is safe and is not a way to refresh stale data. To genuinely
reload a region, clear it first with `DELETE /actions/clear-region/{region}`.

The parquet files are downloaded on first use and cached under `~/.openestimator/cache`, so a
region you have loaded once survives losing network access later.

## Importing a spreadsheet

Two calls: preview to agree the column mapping, then import.

### Preview

```bash
curl -X POST http://localhost:8000/api/v1/costs/import/preview/ \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@rates.xlsx"
```

You get back the file's real headers, a few sample rows, and `suggested_map`, which is the
platform's guess at which of your columns is which. It also returns `required_fields`, which is
always `["description", "rate"]`, and `has_currency_column`, which tells you whether you will
need to name a currency at import time.

Nothing is written. Preview exists so the mapping is agreed before rows are created, rather than
discovered afterwards from a catalogue full of blank descriptions.

### Import

```bash
curl -X POST http://localhost:8000/api/v1/costs/import/file/ \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@rates.xlsx" \
  -F "catalog_name=Munich 2026" \
  -F "catalog_currency=EUR"
```

Pass `column_map` as a JSON object of canonical field to your header when auto-detection missed
something:

```
-F 'column_map={"description":"Leistungsbeschreibung","rate":"EP"}'
```

### The six fields

| Canonical field | Required | Notes |
|---|---|---|
| `code` | no | Generated as `IMPORT-<salt>-0001` when absent |
| `description` | **yes** | Must be covered by the mapping |
| `unit` | no | Defaults to `pcs` |
| `rate` | **yes** | Must be covered by the mapping |
| `currency` | no | Inherits the catalogue currency |
| `classification` | no | MasterFormat, DIN 276 KG, NRM, whatever you use |

A spreadsheet has one classification column and no way to say which scheme it belongs to, so an
imported code is stored under the generic key `code`, as `{"code": "03 30 53"}`. Over the API the
field is a dictionary keyed by scheme, so you can write `{"masterformat": "03 30 53"}` or
`{"din276": "330"}`, and an item can carry several at once. If you need scheme-tagged
classification on imported rows, patch them afterwards or use `POST /bulk/` instead.

Required means required *in the mapping*, not in every row. If neither auto-detection nor your
`column_map` covers `description` and `rate`, the import is refused with a 422 naming the fields
it could not find. This is the one rejection people meet most often, and it is deliberate: a file
imported without a rate column produces thousands of rows priced at zero, which looks like data
until someone builds an estimate on it.

Headers are matched case-insensitively against an alias list covering English, German and
Russian, plus common French, Spanish, Italian, Polish and Turkish forms. `Наименование`, `EP`,
`Einheit` and `Art.Nr.` all resolve without a manual step. Any header the list does not know is
what `column_map` is for, so no column is ever silently dropped for being in the wrong language.

### Choosing where the rows land

Pass **either** `catalog_id` for an existing catalogue **or** `catalog_name` to create one
inline. Both together is a 422. Neither is allowed too, and sends the rows into the shared global
catalogue.

When you create a catalogue inline, its currency is resolved in this order: the `catalog_currency`
you passed, or failing that the most common currency value in the file, or failing that a 422.
The catalogue name is also stamped on the rows as their region tag, truncated to 50 characters,
so your import is findable afterwards as one database rather than scattered.

Importing into an existing catalogue requires you to own it, or to be an admin. A catalogue you
do not own answers 404 rather than 403, so the API does not confirm that someone else's
catalogue id exists.

### Reading the result

```json
{
  "imported": 1180,
  "skipped": 12,
  "errors": [],
  "total_rows": 1192,
  "catalog": "Munich 2026",
  "catalog_id": "…",
  "catalog_currency": "EUR",
  "mixed_currency_count": 0,
  "rate_parse_failures": 3
}
```

The last two are warnings rather than failures, and both are worth looking at.

`mixed_currency_count` counts rows whose own currency differed from the catalogue's. Those rows
are imported exactly as they were, never rewritten. A non-zero count on a file you believed was
single-currency usually means a stray column value, and the rates it describes are not in the
currency the rest of the catalogue is in.

`rate_parse_failures` counts cells that held something that was not a number. Those rows import
with a rate of `0`. A blank cell is not counted here, only a cell with content that could not be
read, so a non-zero figure means real prices were lost. `1 234,50`, `€45.00` and `see note` all
land here.

`skipped` covers rows with neither a code nor a description, rows whose description is a summary
line (`total`, `subtotal`, `Summe`, `Gesamtsumme`, `Zwischensumme` and similar), and rows whose
code already exists in that region.

### Limits and refusals

Uploads are capped at 100 MB. Above that you get a 413 telling you the actual size, and the
answer is to split the file.

The extension is checked, but it is not the real gate: the first bytes of the content are.
An `.xlsx` must genuinely be an OOXML container, and a `.csv` must genuinely be text, so a
renamed binary is refused with a 415 rather than handed to the spreadsheet parser. A `.csv`
containing NUL bytes is refused for the same reason. `.xlsx` files are additionally checked
against their own declared uncompressed size before anything is inflated, so a small archive
that expands to gigabytes is rejected instead of being decompressed into memory.

## Importing over the API

`POST /bulk/` takes an array of cost items and returns the ones it created.

```json
[
  {
    "code": "LAB-CARP-01",
    "description": "Carpenter, journeyman",
    "unit": "hr",
    "rate": "68.50",
    "currency": "USD",
    "region": "US_CUSTOM",
    "classification": "03 10 00"
  }
]
```

**Rates travel as strings.** The field accepts a JSON number or a numeric string, but it is a
`Decimal` internally and is always serialised back to you as a string. This is not cosmetic:
JSON's number type is a float, and a rate of `199.99` that round-trips through one comes back as
`199.98999999999998`. If you are generating this payload, quote your rates. If you are consuming
the response, do not parse them into a float and then store the result.

Uniqueness is on `(code, region)`, not on `code` alone. The same code in two regions is two rows,
which is what lets one catalogue hold `03 30 53` for Munich and for New York at different prices.

## Resource components

A rate can be a single number, or it can carry the resources that make it up. Both are ordinary
cost items; the second just has a populated `components` array.

```json
{
  "code": "WI-FOOT-STRIP-2X1",
  "description": "Strip footing, 2 ft wide, reinforced, formed and poured",
  "unit": "lf",
  "rate": "38.50",
  "currency": "USD",
  "components": [
    {"type": "labor",     "code": "LAB-CARP-01",  "name": "Carpenter, journeyman", "quantity": 0.18,  "unit": "hr"},
    {"type": "material",  "code": "MAT-CONC-3K",  "name": "Concrete 3000 psi",     "quantity": 0.075, "unit": "yd3"},
    {"type": "equipment", "code": "EQP-EXC-SML",  "name": "Excavator, small",      "quantity": 0.025, "unit": "hr"}
  ]
}
```

`components` is a free-form JSON column. Nothing validates its contents on the way in, which
means a component that is shaped wrongly is stored successfully and simply never does anything.
Two parts of the platform read it, and it is worth knowing exactly what each one looks for.

**Repricing** (`POST /resource-prices/{region}/reprice/`) reads `code`, `name`, `quantity` and
`type`. It looks up a unit price per resource, multiplies it by `quantity`, and writes `unit_rate`
and `cost` back onto each component along with a new total on the item. A component with no
`quantity` is read as a quantity of zero rather than as missing, so it contributes nothing to the
total while still counting as successfully priced.

**Resource extraction** (`POST /api/v1/catalog/extract/`) reads `code`, `type` and `name`. It
groups components across the whole catalogue by `type` and `code` together, so `labor:LAB-CARP-01`
and `material:LAB-CARP-01` stay separate, and builds a resource catalogue with a representative
price per resource. A component with no `code` is skipped, and so is one whose `type` is not
`material`, `equipment`, `labor` or `operator`.

So the shape both readers agree on is `{type, code, name, quantity, unit}`. Use that.

> **A shape that does not work, and fails quietly.**
> `data/templates/cost_database_with_assemblies.json`, which ships in this repository, writes
> components as `{code, factor, unit, type}`. Nothing reads `factor`.
>
> This does not merely do nothing. A missing `quantity` is read as a quantity of zero, not as an
> absent value. So once the resources involved carry real prices, each component prices at zero
> cost and *counts as priced*: every line succeeds, the item is reported as fully priced with no
> missing resources, and its rate is overwritten with `0.00`.
>
> The one thing that hides this is an empty price sheet. A resource priced below half a cent is
> treated as unpriced, and an item on which nothing priced is left alone rather than zeroed, so a
> reprice run before you have set any prices reports the items as unpriced and changes nothing.
> The damage lands on the first run after the prices are real.
>
> Extraction is unaffected, because it only needs `code` and `type`.
>
> If you have built files against that template, rename `factor` to `quantity` before you
> reprice. The template is a known defect and is being corrected separately.

## Adding to a database that already exists

Import never updates. Every path skips a row whose `(code, region)` is already present and
reports it under `skipped`; none of them overwrite the existing rate. This is true of
`POST /bulk/`, of file import, and of `load-cwicr`.

The practical consequence is that re-importing a corrected spreadsheet does nothing at all. It
returns `imported: 0` with every row counted as skipped, which is easy to misread as a broken
upload. To replace rates you have already loaded, either delete the region first with
`DELETE /actions/clear-region/{region}` and import again, or patch the individual items with
`PATCH /{item_id}`.

Two smaller consequences are worth knowing. Codes that are auto-generated because your file had
no code column are salted per request, so importing a second code-less file into the same
catalogue does not collide with the first. And a duplicate *within* one payload is skipped the
same way a duplicate against the database is, so a spreadsheet with a repeated row imports it
once rather than failing the whole batch.

## When something looks wrong

**Everything imported but the rates are all zero.** Your rate column was not mapped, or it was
mapped to a column of formatted text. Check `rate_parse_failures` in the response. If the import
was refused with a 422 instead, that is the same problem caught earlier.

**`imported: 0` and everything skipped.** The codes already exist in that region. See above.

**404 on a path that looks correct.** Trailing slash.

**Rows imported but the catalogue looks empty.** The rows carry a region tag taken from the
catalogue name. If you imported without naming a catalogue, they went into the shared global
catalogue rather than a named one.

**A rate is slightly wrong in the last decimal places.** Something in your pipeline parsed the
string rate into a float. See the note under Importing over the API.

**Every rate became `0.00` after a reprice, and the reprice reported success.** The components
carry `factor` rather than `quantity`. See the note under Resource components. This is the one
failure in this document that looks like a clean run in the response body, so check a rate
directly rather than trusting the counts.
