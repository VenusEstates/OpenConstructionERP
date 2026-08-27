// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// Cases - the specialist's photograph on a case tile, and the one place the
// country-variant fallback happens.
//
// `caseFaceFor` mints `prf-<country>-<stem>.webp` for every case that names a
// market, and cannot know whether that file has been bought yet: the country
// art is dropped into public/assets/people over time and there is no
// build-time list of it (see the header of caseFaces.ts for why there must not
// be one). So the check is the request itself. The browser asks for the
// country portrait, and if it 404s this component swaps in the pooled one on
// the error event. The reader never sees an empty tile or a broken-image
// glyph; the worst case is the picture they saw before any country art
// existed.
//
// The four surfaces that show a face - the Cases hub card, the case page hero
// and its catalogue band, and the dashboard gallery - all come through here.
// The dashboard forces it: its <img> is rendered inside a `.map()` in the
// parent's body, where a per-tile `useState` is not something you can write.
// One component is the answer for the other three too, since a fallback that
// three of four surfaces implement is a fallback one surface is missing.

import { useState, type CSSProperties } from 'react';
import type { CaseFace } from './caseFaces';

interface CaseFacePhotoProps {
  /** The requested portrait and its pooled fallback, from `dealCaseFaces`. */
  face: CaseFace;
  /** Classes for the <img> itself. Every caller frames it differently - a hex
   *  crop, a masked band, a masked column - so the shape stays with them. */
  className?: string;
  /** Inline style for the <img>, for the clip-path the hex tiles carry. */
  style?: CSSProperties;
  /** Intrinsic size, passed through rather than hardcoded: the three 340x480
   *  callers give it and the dashboard deliberately does not, and pinning a
   *  size on that one would move its layout. */
  width?: number;
  height?: number;
}

/**
 * The photograph of the specialist a case is written for.
 *
 * Decorative everywhere it is used (`alt=""`): the role and the market are
 * stated in words on the same tile, so nothing is said only in a picture.
 *
 * Falls back from the country portrait to the pooled one when the former does
 * not load. When the two are equal - a universal case, or a bespoke `pbk-*`
 * photo - the fallback is a no-op and a genuinely missing file leaves the
 * browser's own placeholder, which is the behaviour these tiles always had.
 */
export function CaseFacePhoto({ face, className, style, width, height }: CaseFacePhotoProps) {
  const [broken, setBroken] = useState(false);
  // A card can be handed a different case without unmounting - the hub filters
  // in place and the case page navigates between cases - so a `broken` flag
  // left over from the previous src would pin the tile to the pooled portrait
  // for the rest of the session. Same reset CompanyArt does on its `id`.
  const [lastSrc, setLastSrc] = useState(face.src);
  if (face.src !== lastSrc) {
    setLastSrc(face.src);
    setBroken(false);
  }

  return (
    <img
      src={broken ? face.pooled : face.src}
      alt=""
      loading="lazy"
      decoding="async"
      width={width}
      height={height}
      draggable={false}
      // Firing again on the pooled portrait is harmless: the state is already
      // true, React bails out, and there is no second source to loop between.
      onError={() => setBroken(true)}
      className={className}
      style={style}
    />
  );
}
