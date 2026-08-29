// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// Locating a region of someone else's source file, safely.
//
// Some rules can only be checked by reading source as text. "This component
// does not paste the credit string literally, it references the shared
// constant" is a claim about how a file is written, and importing a value can
// never answer it. Those tests are legitimate and they are not going away.
//
// What they need is a floor under the region they examine, because the failure
// mode of a text scan is not an error, it is a confident wrong answer. Two
// shapes produce it:
//
//   * A runaway slice. `source.slice(source.indexOf(A), source.indexOf(B))`
//     with B missing returns -1, and `slice(start, -1)` runs to the end of the
//     file. The assertion then examines whichever declaration happens to sit
//     last in the file and reports on it under the name of the one it meant.
//     Seen live: everyMapSurfaceCreditsOpenStreetMap read a neighbouring
//     constant this way. One half of it failed loudly and said nothing true;
//     the sibling half overran identically and stayed GREEN, purely because
//     nothing was declared after it, and adding one export below that line
//     would have been enough to make it lie while still passing.
//
//   * An empty population. A walk that collects no files, or a read that comes
//     back empty, makes `expect(offenders).toEqual([])` true for free. Every
//     test here that walks the disk states a floor on its population in its own
//     file for that reason.
//
// WHY THIS IS A STANDING HAZARD HERE AND NOT AN OCCASIONAL BUG
//
// This repository has no `.gitattributes` rule for `.ts`, and `core.autocrlf`
// is true on a Windows checkout, so the index is LF and the working tree is
// whatever last wrote each file. Measured 2026-08-28 over frontend/src and
// frontend/tests: 1915 files CRLF on disk, 1029 LF, 20 with both. Every one of
// them is LF in the index. The LF ones are LF only because some session
// rewrote them after checkout.
//
// So which side of that line a file falls on is an artifact of which agent last
// touched it, not a property of the repository. A text locating test here can
// go from correct to blind with no edit to the test and no change to the
// target's content, and back again, because an unrelated session happened to
// rewrite the target. An anchor of `';\n'` matches an LF copy and misses the
// CRLF copy of the same bytes. That is why a local green is evidence about this
// disk at this hour and nothing else, and it is the reason this helper exists.
// It is not a warning about line endings. It is the reason a text scan in this
// tree has to fail loudly when it cannot find what it is looking for, instead
// of quietly measuring a neighbour.

export interface SliceBetweenOptions {
  /**
   * Shortest source this call will accept, in characters.
   *
   * Stated by the caller rather than defaulted, so that handing the helper an
   * empty or truncated read fails here, naming the file, instead of three
   * assertions later as a vacuous pass. Set it well under the real size: it is
   * a collapse detector, not a size assertion.
   */
  minSourceLength: number;
  /** What the source is, so a failure names the file rather than a haystack. */
  label?: string;
}

/**
 * The text between two anchors, or a thrown error explaining which anchor moved.
 *
 * Unlike a hand written `slice(indexOf(a), indexOf(b))` this refuses every way
 * the region can silently become the wrong region:
 *
 *   * a source shorter than `minSourceLength`, which is an empty or failed read
 *   * either anchor missing, rather than slicing to the end of the file
 *   * an end anchor that occurs only BEFORE the start anchor, which a bare
 *     `indexOf(b)` searching from zero would return and which yields an empty
 *     slice that reads as "the block is empty" rather than "the anchors crossed"
 *   * a second occurrence of the start anchor inside the result, which means the
 *     slice swallowed a neighbouring declaration
 *
 * Anchors may not contain a newline. An anchor spanning a line break matches one
 * checkout of a file and misses the other, for the reason in the header above,
 * and it does so without erroring. Anchor on a single line and let the returned
 * block be what spans lines.
 */
export function sliceBetween(
  source: string,
  startAnchor: string,
  endAnchor: string,
  options: SliceBetweenOptions,
): string {
  const what = options.label ? `${options.label}: ` : '';

  for (const [role, anchor] of [
    ['start', startAnchor],
    ['end', endAnchor],
  ] as const) {
    if (anchor.length === 0) {
      throw new Error(`${what}the ${role} anchor is empty, so it matches at position 0 and means nothing`);
    }
    if (/[\r\n]/.test(anchor)) {
      throw new Error(
        `${what}the ${role} anchor ${JSON.stringify(anchor)} spans a line break. ` +
          'It would match an LF checkout of the target and miss a CRLF one, silently, ' +
          'and this tree holds both. Anchor on a single line.',
      );
    }
  }

  if (source.length < options.minSourceLength) {
    throw new Error(
      `${what}read ${source.length} characters, expected at least ${options.minSourceLength}. ` +
        'This is an empty or truncated read, not a small file, and every assertion ' +
        'downstream would have passed on it.',
    );
  }

  const start = source.indexOf(startAnchor);
  if (start === -1) {
    throw new Error(`${what}the start anchor ${JSON.stringify(startAnchor)} is not in the source`);
  }

  // Searched from the end of the start anchor, never from zero: an end anchor
  // that also appears earlier in the file would otherwise be found in front of
  // the start and hand back an empty block.
  const end = source.indexOf(endAnchor, start + startAnchor.length);
  if (end === -1) {
    throw new Error(
      `${what}the end anchor ${JSON.stringify(endAnchor)} is not in the source after the start anchor. ` +
        'Slicing to the end of the file here is what makes a test report on a neighbour.',
    );
  }

  const block = source.slice(start, end);

  const second = block.indexOf(startAnchor, startAnchor.length);
  if (second !== -1) {
    throw new Error(
      `${what}the block between the anchors contains a second ${JSON.stringify(startAnchor)} at ` +
        `offset ${second}, so it holds more than the one declaration it names.`,
    );
  }

  return block;
}
