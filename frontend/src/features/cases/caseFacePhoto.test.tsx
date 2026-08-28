// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// Gate for the load-time half of the country-portrait feature.
//
// caseFaces.test.ts proves which FILE the code asks for, and that the file is
// on disk when it asks. It cannot prove what happens when the file turns out
// not to be there anyway - a deploy that shipped the manifest without the
// webp, a webp pulled afterwards - because that is decided at load time. The
// faces below are therefore built by hand rather than dealt: the point is the
// request failing, not the request being chosen.
//
// jsdom never fetches an <img>, so nothing here fires `error` on its own and a
// test that merely rendered would pass while proving nothing at all. The error
// event is dispatched explicitly, and what is asserted afterwards is that an
// <img> is STILL on the page wearing the pooled portrait. Asserting the
// country <img> is gone would be satisfied by a tile that renders nothing,
// which is the outcome this component exists to prevent.

import { describe, expect, it } from 'vitest';
import { fireEvent, render } from '@testing-library/react';
import { CaseFacePhoto } from './CaseFacePhoto';
import { PEOPLE_ASSETS_BASE, type CaseFace } from './caseFaces';

const GERMAN: CaseFace = {
  src: `${PEOPLE_ASSETS_BASE}/prf-de-estimator.webp`,
  pooled: `${PEOPLE_ASSETS_BASE}/prf-estimator.webp`,
};
const UNIVERSAL: CaseFace = {
  src: `${PEOPLE_ASSETS_BASE}/prf-estimator.webp`,
  pooled: `${PEOPLE_ASSETS_BASE}/prf-estimator.webp`,
};

/** The one <img> the component renders.
 *
 *  Queried by tag rather than by role. `alt=""` puts the element in the
 *  `presentation` role, not `img`, which is correct for a decorative
 *  photograph and would make a role query silently assert the wrong thing -
 *  the point of every check below is that a PICTURE is on the page, and that
 *  has to be asked of the element itself. */
function photo(): HTMLImageElement {
  const images = document.body.querySelectorAll('img');
  expect(images).toHaveLength(1);
  return images[0] as HTMLImageElement;
}

describe('CaseFacePhoto', () => {
  it('shows the country portrait for a case whose market has one', () => {
    render(<CaseFacePhoto face={GERMAN} />);
    expect(photo().getAttribute('src')).toBe(GERMAN.src);
  });

  it('falls back to the pooled portrait when the country file is not there', () => {
    render(<CaseFacePhoto face={GERMAN} />);
    fireEvent.error(photo());
    const img = photo();
    expect(img.getAttribute('src')).toBe(GERMAN.pooled);
    // Named separately from the src: the requirement is not only "the right
    // file" but "a picture at all". An empty tile and a broken-image glyph
    // both fail here.
    expect(img).toBeInTheDocument();
    expect(img.getAttribute('alt')).toBe('');
  });

  it('stops at the pooled portrait instead of looping between the two', () => {
    render(<CaseFacePhoto face={GERMAN} />);
    fireEvent.error(photo());
    fireEvent.error(photo());
    fireEvent.error(photo());
    expect(photo().getAttribute('src')).toBe(GERMAN.pooled);
  });

  it('leaves a universal case on the picture it always wore', () => {
    render(<CaseFacePhoto face={UNIVERSAL} />);
    expect(photo().getAttribute('src')).toBe(UNIVERSAL.pooled);
    fireEvent.error(photo());
    expect(photo().getAttribute('src')).toBe(UNIVERSAL.pooled);
  });

  it('tries again when the same tile is handed a different case', () => {
    // The hub filters in place and the case page navigates between cases, so
    // this component is re-rendered with a new face rather than remounted. A
    // `broken` flag left over from the previous case would pin every later
    // card to its pooled portrait for the rest of the session, and nothing on
    // screen would say why.
    const { rerender } = render(<CaseFacePhoto face={GERMAN} />);
    fireEvent.error(photo());
    expect(photo().getAttribute('src')).toBe(GERMAN.pooled);

    const chinese: CaseFace = {
      src: `${PEOPLE_ASSETS_BASE}/prf-cn-estimator.webp`,
      pooled: `${PEOPLE_ASSETS_BASE}/prf-estimator.webp`,
    };
    rerender(<CaseFacePhoto face={chinese} />);
    expect(photo().getAttribute('src')).toBe(chinese.src);
  });

  it('passes the intrinsic size through rather than pinning one', () => {
    // Three callers reserve 340x480; the dashboard deliberately gives none and
    // would move if a size appeared under it.
    const { rerender } = render(<CaseFacePhoto face={GERMAN} width={340} height={480} />);
    expect(photo().getAttribute('width')).toBe('340');
    rerender(<CaseFacePhoto face={GERMAN} />);
    expect(photo().hasAttribute('width')).toBe(false);
  });
});
