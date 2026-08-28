"""‌⁠‍
i18n coverage audit for OpenConstructionERP.

Reads frontend/src/app/locales/*.ts and reports:
  - missing keys (present in en.ts but answered by neither the locale nor,
    for a regional variant, its base language)
  - identical values (likely untranslated — equal to EN verbatim)
  - per-locale gap counts and top-20 missing examples
  - v3.0.5 critical-key coverage check (nav.*, modules.dev_guide, support.*)
  - top-20 keys missing across the MOST locales

Audit-only — does NOT modify any locale file.

A regional variant is measured against its base language, not against English.
en-US, es-MX, es-CL, es-CO and pt-BR carry only the words that differ from en,
es and pt; every other key is answered by the base file, which is the whole
point of an overlay and not a gap. This audit used to compare all 43 files
against en.ts, and so reported the one deliberate overlay we own, en-US, as
33643 keys short and painted it RED. That number is not a coverage problem, it
is the model working, and printing it as a problem is an instruction to destroy
the overlay: the cheapest way to make the line go away is to paste a full copy
of English into en-US.ts. The same miscount once turned a 1499-key overlay into
25280 errors in the orphan guard, which is where the rule below comes from.

The rule is imported from scripts/check_i18n_orphan_keys.py rather than
restated here. Two implementations of "which file answers a variant" drift, and
the drift is silent because both look right in isolation.

Deliberately unchanged while fixing the model: the reader. Keys are still found
by the line regex in parse_locale rather than by parsing TypeScript, so this
tool's key counts stay comparable with its own previous runs, and stay slightly
below a real parse. Do not quote a number from this report beside one from a
parser-derived census; they are different instruments.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCALES_DIR = ROOT / "frontend" / "src" / "app" / "locales"
REPORT_PATH = Path(__file__).resolve().parent / "i18n_coverage_report.txt"

# Importable whether this file is run as a script, imported from the scripts
# directory, or loaded by path from somewhere else in the tree.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_i18n_orphan_keys import base_of  # noqa: E402  (import after sys.path tweak)

MASTER = "en"

# Heuristic skips for identical-value detection.
ACRONYMS = {
    "CPI",
    "SPI",
    "KPI",
    "BIM",
    "IFC",
    "CAD",
    "BOQ",
    "HVAC",
    "MEP",
    "AI",
    "CV",
    "RFI",
    "EAC",
    "DDC",
    "DIN",
    "NRM",
    "GAEB",
    "RFP",
    "RFQ",
    "PO",
    "CO",
    "FX",
    "VAT",
    "QC",
    "QA",
    "ID",
    "URL",
    "API",
    "PDF",
    "CSV",
    "XML",
    "JSON",
    "IT",
    "OK",
    "UI",
    "UX",
    "EVM",
    "OCR",
    "BCF",
    "ESG",
    "GDPR",
    "SSO",
    "JWT",
    "RBAC",
    "CRUD",
}
BRANDS = {
    "OpenConstructionERP",
    "DDC",
    "GitHub",
    "Slack",
    "GitLab",
    "Anthropic",
    "OpenAI",
    "Google",
    "PostgreSQL",
    "Redis",
    "MinIO",
    "PyPI",
    "DataDrivenConstruction",
    "Excel",
    "Word",
    "LinkedIn",
    "Twitter",
    "X",
    "Mongolia",
    "Berlin",
}
KEYBOARD_RE = re.compile(r"^(Ctrl|Cmd|Shift|Alt|Meta)\+\w+(\+\w+)?$", re.IGNORECASE)
FILEEXT_RE = re.compile(r"^\.[a-z0-9]{1,5}$", re.IGNORECASE)
EMOJI_RE = re.compile(r"^[\U0001F300-\U0001FAFF\U00002600-\U000027BF✂-➰\U0001F000-\U0001F02F]+$")
NUMBER_RE = re.compile(r"^[\d.,%+\-\s]+$")
ALLCAPS_RE = re.compile(r"^[A-Z0-9 ./\-+&]+$")
PLACEHOLDER_ONLY_RE = re.compile(r"^\{\{[^}]+\}\}$")


def parse_locale(path: Path) -> dict[str, str]:
    """‌⁠‍Parse a .ts locale file. Flat keys: '"key": "value",' on one line."""
    text = path.read_text(encoding="utf-8")
    result: dict[str, str] = {}

    # Match: "key": "value",  — allowing escaped quotes in value.
    # Skip lines starting with // (comments) and lines with template braces.
    line_re = re.compile(r'^\s*"((?:[^"\\]|\\.)+)"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$')

    for raw_line in text.splitlines():
        if raw_line.lstrip().startswith("//"):
            continue
        m = line_re.match(raw_line)
        if not m:
            continue
        key = unescape_ts(m.group(1))
        val = unescape_ts(m.group(2))
        result[key] = val

    return result


def unescape_ts(s: str) -> str:
    """‌⁠‍Undo TS double-quoted string escapes."""
    return s.replace(r"\\", "\\").replace(r"\"", '"').replace(r"\n", "\n").replace(r"\t", "\t")


def is_legit_identical(en_val: str, _key: str) -> bool:
    """Return True if EN==locale is plausibly correct (skip from 'identical' count)."""
    v = en_val.strip()
    if not v:
        return True
    if v in ACRONYMS or v in BRANDS:
        return True
    if KEYBOARD_RE.match(v):
        return True
    if FILEEXT_RE.match(v):
        return True
    if EMOJI_RE.match(v):
        return True
    if NUMBER_RE.match(v):
        return True
    if PLACEHOLDER_ONLY_RE.match(v):
        return True
    # Single char.
    if len(v) <= 1:
        return True
    # Short ALL-CAPS-ish acronyms (≤6 chars, all caps + numbers).
    if len(v) <= 6 and ALLCAPS_RE.match(v) and any(c.isalpha() for c in v):
        return True
    # Pure URLs.
    if v.startswith(("http://", "https://", "www.")):
        return True
    # Tokens like "C30/37", "BSt 500", "DN200" — short, contains digit.
    if len(v) <= 12 and any(c.isdigit() for c in v) and not any(c == " " for c in v[:3]):
        if re.match(r"^[A-Za-z]+\d+", v) or re.match(r"^\d+[A-Za-z]+", v):
            return True
    return False


def head_commit() -> str:
    """Short SHA of HEAD, or a word saying why there isn't one."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def locale_input_digest(paths: list[Path]) -> str:
    """One digest over the bytes this run actually read.

    The basis a reader needs is not the date. Locale files are edited by
    several people at once here, so a report can be minutes old and already
    describe a tree that no longer exists. Recomputing this digest answers
    whether the counts below still apply, which a timestamp cannot.
    """
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(path.name.encode("utf-8"))
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def unanswered_keys(en_keys: set[str], loc_keys: set[str], base_keys: set[str] | None) -> list[str]:
    """Keys whose reader falls past this locale AND its base, into English.

    ``base_keys`` is None for a language that is nobody's variant, and that
    case must stay strict: en.ts answering a key is not coverage of anything,
    it is the fallback this whole audit exists to count. A variant is short of
    a key only when its base is short of it too.
    """
    answered = loc_keys if base_keys is None else loc_keys | base_keys
    return sorted(en_keys - answered)


def base_locale_data(bases: dict[str, str | None]) -> dict[str, dict[str, str]]:
    """Contents of every base language some variant resolves through.

    Only the bases are held in memory. Keeping all 43 parsed files around to
    answer five questions costs a few hundred megabytes for nothing.

    An empty parse is refused rather than used. The reader here is a line regex,
    not a TypeScript parser, so a base file written in some other shape drops
    out of it silently. Silently is the problem: an empty base answers nothing,
    every variant riding on it goes back to being measured against English, and
    the report prints the same tens of thousands of false missing keys this
    whole model exists to stop, with nothing on the page saying why.
    """
    data: dict[str, dict[str, str]] = {}
    for base in sorted({b for b in bases.values() if b}):
        parsed = parse_locale(LOCALES_DIR / f"{base}.ts")
        if not parsed:
            raise SystemExit(
                f"ERROR: {base}.ts parsed to zero keys, and regional variants "
                f"resolve through it. Continuing would measure them against "
                f"English again and report the overlay as tens of thousands of "
                f"keys short. Fix the reader or the file; an empty parse is a "
                f"broken scan, not a clean one."
            )
        data[base] = parsed
    return data


def main() -> None:
    en_path = LOCALES_DIR / f"{MASTER}.ts"
    en = parse_locale(en_path)
    en_keys = set(en.keys())
    if not en_keys:
        raise SystemExit(
            f"ERROR: {en_path.name} parsed to zero keys. Every number below is "
            "measured against it, so an empty read would report every locale as "
            "perfectly complete. Finding nothing and not having looked must not "
            "print the same result."
        )

    stems = sorted(p.stem for p in LOCALES_DIR.glob("*.ts"))
    locales = [s for s in stems if s != MASTER]

    # Which file actually answers each locale. Derived by the same rule the
    # orphan guard enforces, imported from it, never restated.
    bases = {loc: base_of(loc, set(stems)) for loc in locales}
    base_data = base_locale_data(bases)

    per_locale: dict[str, dict] = {}
    missing_counter: Counter[str] = Counter()
    identical_counter: Counter[str] = Counter()

    for loc in locales:
        loc_path = LOCALES_DIR / f"{loc}.ts"
        data = parse_locale(loc_path)
        loc_keys = set(data.keys())

        # A variant is short of a key only when its base is short of it too:
        # anything else it does not declare is answered in the reader's own
        # language by es, pt or en, which is the overlay working as designed.
        base = bases[loc]
        inherited = base_data[base] if base else None
        inherited_keys = set(inherited) if inherited is not None else None

        missing = unanswered_keys(en_keys, loc_keys, inherited_keys)
        for k in missing:
            missing_counter[k] += 1

        # An overlay line that repeats what the chain already says does nothing
        # at runtime, so it is neither this file's translation work nor a
        # defect in it. Counted separately, because a variant that repeats a base
        # word which is ITSELF still English would otherwise be charged with
        # the base's bleed and send a reader to edit the wrong file.
        # Measured against what the chain RESOLVES to, not against the base
        # file alone. es-MX asks es first and en second, so a key es does not
        # answer resolves to en's word, and an es-MX line equal to that carries
        # nothing just as surely. Scoping this to keys en happens to have would
        # skip every plural form Spanish needs and English does not, which is
        # around 3700 lines in each Spanish variant.
        redundant: list[str] = []
        if inherited is not None:
            for k in sorted(loc_keys):
                resolved = inherited.get(k, en.get(k))
                if resolved is not None and data[k] == resolved:
                    redundant.append(k)
        redundant_keys = set(redundant)

        identical: list[str] = []
        for k in (en_keys & loc_keys) - redundant_keys:
            en_v = en[k]
            loc_v = data[k]
            if not loc_v.strip():
                continue  # honest-unknown empty string
            if loc_v == en_v and not is_legit_identical(en_v, k):
                identical.append(k)
                identical_counter[k] += 1

        per_locale[loc] = {
            "path": str(loc_path).replace("\\", "/"),
            "missing": missing,
            "identical": sorted(identical),
            "redundant": redundant,
            "total_keys": len(loc_keys),
            "base": base,
            # Keys this locale does not declare and does not need to, because
            # its base language answers them. Reported so that a variant's
            # small key count reads as an overlay rather than as a hole.
            "via_base": len((inherited_keys or set()) - loc_keys),
        }

    # v3.0.5 critical key set.
    v305_keys = sorted(
        k
        for k in en_keys
        if k.startswith("support.") or k in {"nav.add_module", "nav.request_custom_module", "modules.dev_guide"}
    )

    v305_coverage: dict[str, dict[str, list[str]]] = {}  # key -> {missing|identical}
    for k in v305_keys:
        missing_in = [loc for loc in locales if k in set(per_locale[loc]["missing"])]
        identical_in = [loc for loc in locales if k in set(per_locale[loc]["identical"])]
        if missing_in or identical_in:
            v305_coverage[k] = {"missing": missing_in, "identical": identical_in}

    # Top-20 keys missing in MOST locales.
    top_missing = missing_counter.most_common(50)

    variants = {loc: b for loc, b in bases.items() if b}

    # ---- Write report ----
    lines: list[str] = []
    lines.append("# i18n Coverage Audit Report")
    lines.append("")
    lines.append("## Basis")
    lines.append("")
    lines.append(f"Measured: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC")
    lines.append(f"Repo commit: {head_commit()}")
    lines.append(f"Locale input digest: {locale_input_digest([LOCALES_DIR / f'{s}.ts' for s in stems])}")
    lines.append(
        "These counts describe the locale files as they were at that instant, "
        "not a settled state. Locale files are routinely edited by several "
        "people at once, so a count here can be out of date within minutes. "
        "Re-run this script and compare the digest above before quoting any "
        "number below: a different digest means the tree has moved and these "
        "numbers no longer describe it."
    )
    lines.append("")
    lines.append(f"Master: {MASTER}.ts — {len(en_keys)} keys")
    lines.append(f"Locales audited: {len(locales)}")
    lines.append(
        "Regional variants measured against their base language: "
        + (
            ", ".join(f"{loc} via {b}" for loc, b in sorted(variants.items()))
            or "NONE FOUND — see the variants section below"
        )
    )
    lines.append("")
    lines.append("## Summary table")
    lines.append("")
    lines.append(
        "'missing' means no file in this locale's chain declares the key, so its "
        "reader falls all the way through to English. 'identical' is this file's "
        "own untranslated bleed. 'redundant' is a variant declaring a key its "
        "own chain already resolves to the same word, which is not work for "
        "this file either way: if that word is still English the base is the "
        "one bleeding, and it is the base that has to be fixed. For a variant, "
        "own_keys is the size of the overlay and is supposed to be small, and "
        "redundant close to zero. A variant whose redundant share is most of "
        "its own_keys is not an overlay, it is a copy of its base."
    )
    lines.append("")
    header = (
        f"{'locale':<8} {'base':<6} {'missing':>8} {'identical':>10} "
        f"{'redundant':>10} {'own_keys':>9} {'via_base':>9}  status"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for loc in locales:
        d = per_locale[loc]
        m = len(d["missing"])
        i = len(d["identical"])
        gap = m + i
        if gap < 50:
            status = "GREEN <50"
        elif gap < 200:
            status = "YELLOW <200"
        else:
            status = "RED >=200"
        lines.append(
            f"{loc:<8} {d['base'] or '-':<6} {m:>8} {i:>10} "
            f"{len(d['redundant']):>10} {d['total_keys']:>9} {d['via_base']:>9}  {status}"
        )
    lines.append("")

    # Named explicitly, because a tool that quietly stopped looking at variants
    # would also stop printing the false RED line and would be just as wrong.
    lines.append("## Regional variants (overlay model)")
    lines.append("")
    if not variants:
        lines.append(
            "NO regional variant resolved to a base language. Either none is on "
            "disk, or the base file it needs is gone. Both are worth checking: "
            "with no base to resolve through, every variant would be measured "
            "against English again and read as tens of thousands of keys short."
        )
    for loc, b in sorted(variants.items()):
        d = per_locale[loc]
        total = d["total_keys"]
        share = (100.0 * len(d["redundant"]) / total) if total else 0.0
        verdict = "a copy of its base" if share >= 50 else "an overlay"
        lines.append(
            f"- {loc}: {total} own keys, {len(d['redundant'])} of them "
            f"({share:.1f}%) repeating what {b} already resolves to and "
            f"carrying nothing, {d['via_base']} more inherited from {b}, "
            f"{len(d['missing'])} answered by nobody and so a gap in {b} too. "
            f"On disk this file is {verdict}."
        )
    lines.append("")

    # Per-locale top 20 missing examples.
    lines.append("## Top 20 missing keys per locale")
    lines.append("")
    for loc in locales:
        d = per_locale[loc]
        lines.append(f"### {loc} ({len(d['missing'])} missing, {len(d['identical'])} identical)")
        lines.append(f"path: {d['path']}")
        for k in d["missing"][:20]:
            sample = en[k][:60].replace("\n", " ")
            lines.append(f"  - {k}  :: EN={sample!r}")
        if not d["missing"]:
            lines.append("  (no missing keys)")
        lines.append("")

    # v3.0.5 coverage.
    lines.append("## v3.0.5 critical-key coverage")
    lines.append("")
    if not v305_coverage:
        lines.append("All v3.0.5 keys present and translated in every locale.")
    else:
        for k in v305_keys:
            cov = v305_coverage.get(k)
            if cov is None:
                lines.append(f"- {k}: OK (translated in all 26 locales)")
                continue
            miss = cov["missing"]
            iden = cov["identical"]
            bits = []
            if miss:
                bits.append(f"MISSING in {len(miss)}: {', '.join(miss)}")
            if iden:
                bits.append(f"UNTRANSLATED (==EN) in {len(iden)}: {', '.join(iden)}")
            lines.append(f"- {k}: " + " | ".join(bits))
    lines.append("")

    # Top-20 keys missing in MOST locales.
    lines.append("## Top keys missing in the MOST locales (backfill priority)")
    lines.append("")
    lines.append(
        "A regional variant shares its base language's gaps, so one hole in "
        "es.ts is counted four times in this section, once for es and once for "
        "each of es-CL, es-CO and es-MX, and a hole in pt.ts twice. Read a high "
        "count on a Spanish or Portuguese key with that in mind: writing the key "
        "into the base file closes every one of those lines at once."
    )
    lines.append("")
    if not top_missing:
        lines.append(
            "(No missing keys — every locale has every key. "
            "Backfill priority comes from the identical-value list below.)"
        )
    for k, count in top_missing[:30]:
        sample = en.get(k, "")[:80].replace("\n", " ")
        lines.append(f"- [{count}/{len(locales)} locales] {k}  :: EN={sample!r}")
    lines.append("")

    # Top-30 keys identical-to-EN in the MOST locales (real backfill priority).
    lines.append("## Top keys identical-to-EN across the MOST locales (real backfill priority)")
    lines.append("")
    for k, count in identical_counter.most_common(30):
        sample = en.get(k, "")[:80].replace("\n", " ")
        lines.append(f"- [{count}/{len(locales)} locales identical] {k}  :: EN={sample!r}")
    lines.append("")

    # Identical-values sample for each locale.
    lines.append("## Identical-values samples (top 10 per locale)")
    lines.append("")
    for loc in locales:
        d = per_locale[loc]
        lines.append(f"### {loc} — {len(d['identical'])} identical-to-EN values")
        for k in d["identical"][:10]:
            sample = en[k][:60].replace("\n", " ")
            lines.append(f"  - {k}  :: {sample!r}")
        if not d["identical"]:
            lines.append("  (none)")
        lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written: {REPORT_PATH}")
    print(f"EN master keys: {len(en_keys)}")
    print(f"Locales audited: {len(locales)}")
    print(
        "Regional variants measured against their base: "
        + (", ".join(f"{loc} via {b}" for loc, b in sorted(variants.items())) or "NONE FOUND")
    )
    print()
    print(f"{'locale':<8} {'base':<6} {'missing':>8} {'identical':>10}")
    for loc in locales:
        d = per_locale[loc]
        print(f"{loc:<8} {d['base'] or '-':<6} {len(d['missing']):>8} {len(d['identical']):>10}")


if __name__ == "__main__":
    main()
