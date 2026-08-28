"""‌⁠‍Show the 13 'other' (ASCII but differs) entries."""

import re
from pathlib import Path

en = Path("frontend/src/app/locales/en.ts").read_text(encoding="utf-8")
mn = Path("frontend/src/app/locales/mn.ts").read_text(encoding="utf-8")
pat = re.compile(r'"((?:[^"\\]|\\.)+)"\s*:\s*"((?:[^"\\]|\\.)*)"')
en_pairs = {m.group(1): m.group(2) for m in pat.finditer(en)}
mn_pairs = {m.group(1): m.group(2) for m in pat.finditer(mn)}


def has_cyrillic(s):
    return any("Ѐ" <= c <= "ӿ" for c in s)


for k in en_pairs:
    if k not in mn_pairs:
        continue
    e, m = en_pairs[k], mn_pairs[k]
    if not e:
        continue
    if has_cyrillic(m):
        continue
    if m == e:
        continue
    print(f"  {k}:")
    print(f"    EN: {e!r}")
    print(f"    MN: {m!r}")
