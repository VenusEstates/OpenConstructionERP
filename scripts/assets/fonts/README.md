# Fonts used by the build scripts

`Inter-Variable-Latin.ttf` is Inter, subset to Basic Latin, Latin-1 Supplement and
Latin Extended-A, with the weight axis left intact so a single file covers every
weight the scripts set. It is here rather than left to the machine that runs the
script because a missing font does not fail, it substitutes, and a banner rendered
in whatever face happened to be installed is a banner nobody can reproduce.

Inter is licensed under the SIL Open Font License 1.1. The full licence text is in
`Inter-OFL.txt` and travels with the file, which is what the licence asks for.

Regenerate the subset from an upstream Inter release with `fontTools`:

    pyftsubset Inter.ttf \
      --unicodes="U+0020-007E,U+00A0-017F,U+2013-2014,U+2018-2019,U+201C-201D,U+2022,U+00B7" \
      --layout-features="*" --name-IDs="*" --name-legacy \
      --output-file=Inter-Variable-Latin.ttf
