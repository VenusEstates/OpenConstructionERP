// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * MarketPackNotice — which regional pack covers this market, and a way in.
 *
 * A case written for Germany is about GAEB, VOB/B and XRechnung; one written
 * for China is about GB/T 50500. The case page already named the pack that
 * supplies those standards, but only as a sentence, and the sentence could not
 * say the one thing the reader needed next: whether that pack is switched on
 * here. Measured on a stock install, eighteen packs sit on disk and none is
 * applied, so every reader of a German case was told a German pack exists and
 * left with no way to reach it. A name with no door is worse than silence
 * because it reads as a requirement the reader has already failed.
 *
 * Three states, and they are separated by `active_slug`, not by presence in the
 * pack list - see `resolveMarketPacks` for why the list alone cannot tell the
 * first two apart:
 *
 *   applied     the pack serving this market is the workspace's pack. Say so
 *               and link to it; there is nothing to do.
 *   available   the pack is on disk and switched off. Name it and offer the
 *               setup flow for that exact pack.
 *   absent      no pack serves this market. Render nothing. Ten shipped cases
 *               carry ES and no Spanish pack exists, and a chip that shrugged
 *               on all ten would teach the reader to stop reading the row.
 *
 * The action is a link into the Modules pack list rather than an apply button
 * here. Applying rewrites the workspace's currency, tax template, validation
 * rule packs and which modules are enabled, and the flow that does it already
 * shows a dry-run preview and streams named progress steps. A second, smaller
 * copy of that on a case page would either skip the preview - which is the
 * whole confirm step - or duplicate it. It is also admin-only server-side, and
 * the pack list is where a non-admin reader can see what they would be asking
 * for.
 */

import { Boxes, Check } from 'lucide-react';
import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { useInstalledPacks } from '@/shared/hooks/usePartnerPack';
import { packNameSlug, resolveMarketPacks } from '@/shared/lib/regionalPack';
import { PackEmblem } from '@/shared/ui/PackEmblem';

interface MarketPackNoticeProps {
  /** ISO 3166-1 alpha-2 for the case's market. Cases spell it upper case. */
  region: string | null | undefined;
  className?: string;
}

export function MarketPackNotice({ region, className = '' }: MarketPackNoticeProps) {
  const { t } = useTranslation();
  const { data } = useInstalledPacks();

  const { packs, applied } = useMemo(
    () => resolveMarketPacks(data?.installed ?? [], data?.active_slug, region),
    [data, region],
  );

  if (packs.length === 0) return null;

  // The pack to point at is the applied one when there is one, and otherwise
  // the first that serves the market. Several can - us-california, us-costdata
  // and us-texas all declare US - and the remainder are reachable one click
  // further in, on the page this links to.
  const lead = applied ?? packs[0]!;
  const leadName = t(`modules.pp_name_${packNameSlug(lead.slug)}`, {
    defaultValue: lead.partner_name,
  });

  // Both states link to the same place, and the destination knows the
  // difference: the card opens its setup dialog only for a pack that is not
  // already applied. So the applied chip is not a dead label - a reader who
  // wants to see what their pack configures, or swap it, gets there the same
  // way - and what changes between the states is the wording and the colour.
  const href = `/modules?tab=packs&pack=${encodeURIComponent(lead.slug)}`;

  if (applied) {
    return (
      <Link
        to={href}
        data-testid="market-pack-notice"
        data-pack-state="applied"
        data-pack-slug={lead.slug}
        className={`inline-flex items-center gap-1.5 rounded-md bg-semantic-success/10 px-2 py-0.5 text-2xs font-medium text-content-secondary ring-1 ring-inset ring-semantic-success/25 transition-colors hover:bg-semantic-success/20 hover:text-content-primary ${className}`}
        title={t('cases.regional_pack_setup_hint', {
          defaultValue:
            'This case follows the standards of its market. Opens the pack that carries them, where you can switch it on.',
        })}
      >
        <Check size={11} strokeWidth={2.5} aria-hidden="true" />
        {t('cases.regional_pack_in_use', {
          defaultValue: 'Regional pack in use: {{name}}',
          name: leadName,
        })}
      </Link>
    );
  }

  return (
    <Link
      to={href}
      data-testid="market-pack-notice"
      data-pack-state="available"
      data-pack-slug={lead.slug}
      // bg-oe-blue/10 rather than bg-oe-blue-subtle/60: the subtle token
      // carries no alpha support, so Tailwind emits nothing at all for an
      // alpha-modified form of it and the chip would render with no fill.
      className={`group inline-flex items-center gap-1.5 rounded-md bg-oe-blue/10 px-2 py-0.5 text-2xs font-medium text-content-secondary ring-1 ring-inset ring-oe-blue/20 transition-colors hover:bg-oe-blue/20 hover:text-content-primary ${className}`}
      title={t('cases.regional_pack_setup_hint', {
        defaultValue:
          'This case follows the standards of its market. Opens the pack that carries them, where you can switch it on.',
      })}
    >
      <PackEmblem pack={lead} size={14} />
      {/* One pack named, not "and 2 more". Three packs declare US and the
          count would have to agree with the reader's language, which makes
          this a counted key - and i18next does not fall back to the same
          language's other plural form when a form is missing, it walks the
          fallback chain and prints English. The pack list this links to shows
          the rest, so the count buys a translation hazard and no information. */}
      <span>
        {t('cases.regional_pack_needed', { defaultValue: 'Needs {{name}}', name: leadName })}
      </span>
      <span aria-hidden="true" className="text-oe-blue-text">
        ·
      </span>
      <span className="text-oe-blue-text underline-offset-2 group-hover:underline">
        {t('cases.regional_pack_set_up', { defaultValue: 'Set up' })}
      </span>
      <Boxes size={11} strokeWidth={2} aria-hidden="true" className="text-oe-blue-text" />
    </Link>
  );
}
