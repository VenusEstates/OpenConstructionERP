import i18next from 'i18next';
import { describe, expect, it } from 'vitest';

/**
 * `t(key, 'text')` is a defaultValue, and that is why it needs a gate.
 *
 * The orphan guard exists because a key answered by no locale file still
 * renders: the call site's defaultValue stands in, the reader sees English in
 * every language, and nothing reports it. Its own docstring draws the line
 * there - keys called WITHOUT a defaultValue are out of scope, because a
 * missing one of those prints a raw key on screen and fixes itself the day
 * someone opens the page.
 *
 * The line is right. Where it was drawn was not. i18next accepts the default
 * in two shapes, `t(key, { defaultValue })` and a bare string second argument,
 * and the guard only ever read the first. So the second was silent in exactly
 * the way the guard was built to stop, and out of scope by accident rather
 * than by the reasoning its docstring gives. Measured before the fix: 1120
 * keys were called only in the positional shape, 111 of them were missing from
 * at least one locale, and 8 existed in no locale at all - `people.no_login`
 * and `assemblies.library.no_single_total` among them, English in all 42
 * languages since the day they were written.
 *
 * This asserts the runtime behaviour the guard's scope now rests on, so that a
 * future i18next that stopped honouring the positional form would fail here
 * rather than quietly turning a gate into a formality. It also pins the shape
 * the guard must NOT claim: a key with no default at all renders as itself.
 */
describe('a bare string second argument to t()', () => {
  const bundle = { 'known.key': 'Answered by the bundle' };

  async function instance() {
    const i18n = i18next.createInstance();
    await i18n.init({
      lng: 'de',
      // No fallback bundle: this test is about what happens when a lookup
      // finds nothing, and a fallback language would answer for it.
      fallbackLng: false,
      resources: { de: { translation: bundle } },
      interpolation: { escapeValue: false },
    });
    return i18n;
  }

  it('stands in for a key the bundle cannot answer', async () => {
    const i18n = await instance();
    expect(i18n.t('missing.key', 'Positional English')).toBe('Positional English');
  });

  it('behaves identically to the object form the guard already reads', async () => {
    const i18n = await instance();
    const positional = i18n.t('missing.key', 'Same words');
    const object = i18n.t('missing.key', { defaultValue: 'Same words' });
    expect(positional).toBe(object);
    expect(positional).toBe('Same words');
  });

  it('loses to a bundle that can answer, so it is a default and not an override', async () => {
    const i18n = await instance();
    expect(i18n.t('known.key', 'Positional English')).toBe('Answered by the bundle');
  });

  it('still interpolates, so the shape carries real UI strings and not only labels', async () => {
    const i18n = await instance();
    expect(i18n.t('missing.counted', 'Removed {{count}} rows', { count: 3 })).toBe('Removed 3 rows');
  });

  it('leaves a key called with no default rendering as itself, which is the loud case', async () => {
    const i18n = await instance();
    expect(i18n.t('missing.key')).toBe('missing.key');
  });
});
