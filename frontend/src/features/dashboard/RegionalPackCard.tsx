// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * RegionalPackCard — which regional pack is switched on, and what it did.
 *
 * A regional pack is the single largest piece of setup this product does on a
 * user's behalf: it loads a market's cost database, sets the currency, wires
 * the tax rules, turns on that market's validation standards and switches the
 * screens into that market's language. All of that used to be invisible after
 * the wizard closed. An estimator could work for weeks on top of SINAPI prices
 * and Brazilian ISS tax without a screen anywhere saying so, and an estimator
 * with NO pack had nothing telling them one existed.
 *
 * So the card answers three questions and nothing else: which pack is on, what
 * it set up, and - when none is on - what one would do and where to get it.
 *
 * The rows deliberately name what an estimator recognises. Not "cwicr_regions"
 * and "validation_rule_sets" but price databases and standards checked, with
 * the number beside them where a number is honest. The pack's own module ids
 * never reach the screen.
 */

import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Check, Globe2, ArrowRight } from 'lucide-react';

import { SUPPORTED_LANGUAGES, normalizePackLocale } from '@/app/i18n';
import { CountryFlag } from '@/shared/ui';
import { usePartnerPack } from '@/shared/hooks/usePartnerPack';
import { packCountryCode, packNameSlug } from '@/shared/lib/regionalPack';

/** Where a reader with no pack goes to pick one. Same destination the
 *  co-brand badge uses (see `@/shared/ui/PartnerLogoBadge`). */
const PACKS_ROUTE = '/modules?tab=partner-packs';

/** One "what it set up" line: a label an estimator uses, and the fact behind it. */
function PackFactRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border-subtle/60 py-1.5 last:border-b-0">
      <span className="min-w-0 truncate text-xs text-content-secondary">{label}</span>
      <span className="shrink-0 text-xs font-semibold tabular-nums text-content-primary">
        {value}
      </span>
    </div>
  );
}

export function RegionalPackCard() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data, isLoading } = usePartnerPack();

  // A skeleton, not an empty card: the grid cell for this widget shows
  // WidgetSkeleton while we are null, which is why this id is NOT in
  // WIDGET_NULL_FALLBACK. The card always resolves to something afterwards.
  if (isLoading) return null;

  const manifest = data?.active ? data.manifest : undefined;

  // No pack. This is a real, common and DESIGNED state rather than an error:
  // the community wheel deliberately ships no pack for Germany, Canada or
  // Spain, three of the markets with the most case studies, and a fresh
  // install has none applied whatever the market. Rendering nothing here would
  // leave the reader who most needs to know that packs exist as the one reader
  // who is never told.
  if (!manifest) {
    return (
      <div className="h-full rounded-xl border border-border-light bg-surface-primary p-5">
        <div className="mb-3 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-surface-secondary text-content-tertiary">
            <Globe2 size={20} strokeWidth={1.75} />
          </div>
          <h3 className="text-sm font-semibold text-content-primary">
            {t('dashboard.regional_pack_none', { defaultValue: 'No regional pack is active' })}
          </h3>
        </div>
        <p className="mb-4 text-xs leading-relaxed text-content-tertiary">
          {t('dashboard.regional_pack_none_body', {
            defaultValue:
              "A regional pack sets up one market's prices, tax rules and standards for you, so you do not have to enter them by hand.",
          })}
        </p>
        {/* The Packs tab, NOT the onboarding wizard. The wizard is guarded on
            the same `oe_onboarding_completed` flag the dashboard first-run
            redirect writes, and it bounces a completed user back to `/` with
            `replace`, so this button did nothing at all for every reader who
            can see a dashboard - the guard is total, not intermittent. Only
            Settings' "restart onboarding" may route there, because it removes
            the flag first, and this card must not: silently restarting a
            finished user's setup is worse than the dead button was. The Packs
            tab asks the same question the button does, one pack card per
            market with Activate and the apply dialog behind it, and no guard
            sits in front of it. */}
        <button
          type="button"
          onClick={() => navigate(PACKS_ROUTE)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-oe-blue px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-oe-blue/90"
        >
          {t('dashboard.regional_pack_choose', { defaultValue: 'Install a country pack' })}
          <ArrowRight size={13} aria-hidden="true" />
        </button>
      </div>
    );
  }

  const country = packCountryCode(manifest);
  // The pack's own name, translated. Written as an inline template literal
  // inside t() because that is the only shape check_i18n_computed_keys.py can
  // see - see the note in @/shared/lib/regionalPack.
  const name = t(`modules.pp_name_${packNameSlug(manifest.slug)}`, {
    defaultValue: manifest.partner_name,
  });

  // The language the pack speaks, in that language's own words, resolved the
  // same way the app resolves it. "pt-BR" has to read Português (Brasil), not
  // the code, and not Portugal's Português.
  const uiLanguage = normalizePackLocale(manifest.default_locale);
  const languageName =
    SUPPORTED_LANGUAGES.find((l) => l.code === uiLanguage)?.name ?? manifest.default_locale;

  return (
    <div className="h-full rounded-xl border border-border-light bg-surface-primary p-5">
      <div className="mb-1 flex items-center gap-3">
        {country && country !== 'xx' ? (
          <CountryFlag code={country} size={28} className="shrink-0 rounded-sm shadow-sm" />
        ) : (
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-surface-secondary text-content-tertiary">
            <Globe2 size={16} strokeWidth={1.75} />
          </div>
        )}
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-content-primary">
            {t('dashboard.regional_pack_title', { defaultValue: 'Your regional pack' })}
          </h3>
          <p className="truncate text-xs text-content-tertiary">{name}</p>
        </div>
      </div>

      <p className="mb-3 text-xs text-content-tertiary">
        {t('dashboard.regional_pack_subtitle', {
          defaultValue: 'Set up for your market already, so you do not have to be',
        })}
      </p>

      <div className="rounded-lg bg-surface-secondary/50 px-3 py-1">
        {/* Every row is skipped rather than shown empty. A pack that ships no
            tax template is not a pack whose tax rules are "none", it is a pack
            that is silent on tax, and a zero printed against a label reads as
            a promise broken rather than a promise not made. */}
        {manifest.cwicr_regions.length > 0 && (
          <PackFactRow
            label={t('dashboard.regional_pack_prices', { defaultValue: 'Local price databases' })}
            value={manifest.cwicr_regions.length}
          />
        )}
        <PackFactRow
          label={t('common.currency', { defaultValue: 'Currency' })}
          value={manifest.default_currency}
        />
        {manifest.default_tax_template && (
          <PackFactRow
            label={t('dashboard.regional_pack_taxes', { defaultValue: 'Local tax rules' })}
            value={
              <Check
                size={14}
                strokeWidth={2.5}
                className="text-emerald-600"
                aria-label={t('dashboard.regional_pack_taxes', {
                  defaultValue: 'Local tax rules',
                })}
              />
            }
          />
        )}
        {/* validation_rule_SETS, and a tick rather than their count.
            `validation_rule_packs` is the neighbouring field and the wrong one:
            the manifest calls those "documentation", the engine never executes
            them, and naming one switches nothing on. Counting them here would
            have told a Mexican estimator that five rules were checking their
            bills when two rule sets were, which is the invented number this
            card exists to replace. The count of SETS is not printed either,
            because "mexico" and "boq_quality" are two sets holding many rules
            and "2" against the word rules reads as two rules. What is true and
            useful at a glance is that this market's checks are on. */}
        {manifest.validation_rule_sets.length > 0 && (
          <PackFactRow
            label={t('dashboard.validation_rules', { defaultValue: 'Validation rules' })}
            value={
              <Check
                size={14}
                strokeWidth={2.5}
                className="text-emerald-600"
                aria-label={t('dashboard.validation_rules', {
                  defaultValue: 'Validation rules',
                })}
              />
            }
          />
        )}
        <PackFactRow
          label={t('dashboard.regional_pack_language', { defaultValue: 'Language' })}
          value={languageName}
        />
        {manifest.default_modules.length > 0 && (
          <PackFactRow
            label={t('dashboard.regional_pack_modules', { defaultValue: 'Modules switched on' })}
            value={manifest.default_modules.length}
          />
        )}
      </div>

      {/* The way to a different market, in the state that had no way at all.
          A reader with a pack could see which one was on and nothing about how
          to change it or add another, so the card answered "which pack" for
          everyone and "where do I get one" only for the reader who had none.
          It sits after the facts in both states, so the card reads the same way
          down: which pack is on, what it set up, how to change it. Quiet rather
          than a second primary button, because for a reader who is already set
          up this is a way out, not the next step. */}
      <button
        type="button"
        onClick={() => navigate(PACKS_ROUTE)}
        className="mt-3 inline-flex items-center gap-1.5 rounded-md text-xs font-semibold text-oe-blue transition-colors hover:text-oe-blue-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-oe-blue/40"
      >
        {t('dashboard.regional_pack_manage', {
          defaultValue: 'Change or add a country pack',
        })}
        <ArrowRight size={13} aria-hidden="true" />
      </button>
    </div>
  );
}
