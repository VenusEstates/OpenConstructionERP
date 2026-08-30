// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * ProjectMap — modern vector-tile map for a project's location.
 *
 * Two sizes:
 *   variant="card"     → single static raster tile thumbnail (an <img>),
 *                        no MapLibre / WebGL / live tile streaming. Fits in
 *                        the project list card and loads instantly. The
 *                        grid renders ~12 cards at once, so mounting a live
 *                        GL map per card would spin up 12 WebGL contexts
 *                        streaming vector tiles forever (the network never
 *                        goes idle). The static thumbnail is one cached
 *                        request with zero ongoing work.
 *   variant="detail"   → full interactive MapLibre map — pan, zoom, pin,
 *                        address overlay. Lives on the project detail page.
 *
 * Engine: the detail variant uses MapLibre GL JS (open-source, no Leaflet
 * branding) with OpenFreeMap vector tiles served through our backend (see
 * ./basemap), so it shows real street cartography - named roads, junctions,
 * building footprints. The card variant cannot run a renderer, so it paints
 * a single raster tile as a flat <img>, and the only keyless raster source
 * we have is public-domain shaded relief capped at z6. The card therefore
 * shows a regional TERRAIN patch, not streets. That is a known downgrade,
 * not an oversight: the backend does no rendering, it proxies the relief PNG
 * verbatim, and turning that tile into streets needs a server-side raster
 * renderer we deliberately have not taken on. This paragraph previously
 * claimed the backend drew the card PNG from the vector tile, which is
 * false and would convince a reader the card already had streets.
 * Routing every tile through our own origin keeps maps working even when a
 * browser blocks public tile CDNs.
 *
 * The geocoding pipeline:
 *   1. Accept lat/lng directly (fastest path — stored in project metadata).
 *   2. Otherwise concat (address, city, country), look the string up
 *      through our own backend geocoder, and cache the result in
 *      localStorage under `oe.geocode.<query>` so repeat renders don't
 *      hit the API.
 *
 * Geocoding never leaves the browser for a public geocoder directly. A
 * browser cannot set a User-Agent, so a direct call is unidentifiable and
 * unthrottled and it fans out over every user's IP, which the Nominatim
 * usage policy forbids. The backend geocoder tries Photon first, falls
 * back to Nominatim behind a process-global 1 req/s gate, sends a contact
 * User-Agent, and lets an operator point at their own mirror via
 * OE_GEOCODER_BASE_URL.
 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { MapPin, Loader2 } from 'lucide-react';
import Map, { Marker, Popup, NavigationControl, AttributionControl } from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';
import clsx from 'clsx';

import { geocodeSuggest } from '@/features/geo-hub/api';

import {
  PROXY_TILE_BASE,
  RELIEF_MAX_ZOOM,
  TILE_ATTRIBUTION_HTML,
  VECTOR_BASEMAP_STYLE_URL,
} from './basemap';

// Every map byte comes from our own backend; see ./basemap for the style,
// the credit string and the rationale (browser tile-CDN blocking, and the
// CARTO upstream that started watermarking without changing its status
// code). The card variant stays raster because an <img> cannot render
// vector tiles, not because vector was rejected.

export interface LatLng {
  lat: number;
  lng: number;
}

interface ProjectMapProps {
  /** Direct coordinates — skips geocoding.  Stored in project metadata. */
  lat?: number | null;
  lng?: number | null;
  /** Components of an address to feed Nominatim when lat/lng are absent. */
  address?: string | null;
  city?: string | null;
  country?: string | null;
  /** Display variant.  `card` = static thumbnail, `detail` = interactive. */
  variant?: 'card' | 'detail';
  /** Optional extra classes (height / border overrides). */
  className?: string;
  /** Human-readable label shown in the marker popup and overlay chip. */
  label?: string;
  /** Called once lat/lng are known.  Let the parent persist the result
   *  back to the project so subsequent renders skip geocoding. */
  onResolved?: (coords: LatLng) => void;
}

interface GeocodeCacheEntry {
  lat: number;
  lng: number;
  at: number;
}

const CACHE_PREFIX = 'oe.geocode.';
const CACHE_TTL_MS = 1000 * 60 * 60 * 24 * 30; // 30 days — addresses rarely move

function cacheKey(q: string) {
  return CACHE_PREFIX + q.toLowerCase().trim();
}

function isFiniteNumber(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

// ── Static raster thumbnail (card variant) ──────────────────────────────
//
// The list grid renders ~12 cards at once. Mounting a live MapLibre GL
// instance per card spins up 12 WebGL contexts that stream tiles forever
// (the reason the page never reaches network-idle). For the card variant we
// instead paint a single static raster tile centred on the resolved
// coordinate: one cached <img> request, zero WebGL, zero ongoing network.
// The interactive MapLibre map only mounts on the detail page. Tiles come
// from our same-origin proxy (see ./basemap), so the card renders even when
// a browser blocks public tile CDNs.
// The relief basemap stops at z6. Requesting z11 would return a blank
// tile, so the thumbnail asks for the deepest zoom that actually exists
// and shows a regional relief patch containing the site.
const STATIC_TILE_ZOOM = RELIEF_MAX_ZOOM;

/** Web-Mercator lon → fractional tile X at the given zoom. */
function lngToTileX(lng: number, z: number): number {
  return ((lng + 180) / 360) * 2 ** z;
}

/** Web-Mercator lat → fractional tile Y at the given zoom. */
function latToTileY(lat: number, z: number): number {
  const rad = (lat * Math.PI) / 180;
  return ((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * 2 ** z;
}

/** URL for the raster tile that contains the given coordinate. */
function staticTileUrl(coords: LatLng): string {
  const z = STATIC_TILE_ZOOM;
  const max = 2 ** z;
  const x = Math.min(max - 1, Math.max(0, Math.floor(lngToTileX(coords.lng, z))));
  const y = Math.min(max - 1, Math.max(0, Math.floor(latToTileY(coords.lat, z))));
  return `${PROXY_TILE_BASE}/${z}/${x}/${y}.png`;
}

function readCache(q: string): LatLng | null {
  try {
    const raw = localStorage.getItem(cacheKey(q));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as GeocodeCacheEntry;
    if (Date.now() - parsed.at > CACHE_TTL_MS) return null;
    if (!isFiniteNumber(parsed.lat) || !isFiniteNumber(parsed.lng)) {
      localStorage.removeItem(cacheKey(q));
      return null;
    }
    return { lat: parsed.lat, lng: parsed.lng };
  } catch {
    return null;
  }
}

function writeCache(q: string, coords: LatLng) {
  try {
    const entry: GeocodeCacheEntry = { ...coords, at: Date.now() };
    localStorage.setItem(cacheKey(q), JSON.stringify(entry));
  } catch {
    /* quota full, ignore */
  }
}

async function geocode(query: string, signal?: AbortSignal): Promise<LatLng | null> {
  const cached = readCache(query);
  if (cached) return cached;

  // Goes to GET /api/v1/geo-hub/geocode/suggest, never to a public
  // geocoder from the browser. ``geocodeSuggest`` throws ApiError on a
  // non-2xx and rejects on abort; the catch below turns both into the
  // same null a miss already produced, so the caller's loading and error
  // states are unchanged.
  try {
    const res = await geocodeSuggest(query, { limit: 1, signal });
    const first = res.suggestions[0];
    if (!first) return null;
    const lat = Number(first.lat);
    const lng = Number(first.lon);
    if (!isFiniteNumber(lat) || !isFiniteNumber(lng)) return null;
    const coords: LatLng = { lat, lng };
    writeCache(query, coords);
    return coords;
  } catch {
    return null;
  }
}

// ``buildGeocodeQuery`` lives in ``./geocode`` so consumers that only
// need to build an address string don't pull in the full maplibre +
// react-map-gl chunk (and its 220 KB CSS) via this module.
export { buildGeocodeQuery } from './geocode';
import { buildGeocodeQuery } from './geocode';

export function ProjectMap({
  lat,
  lng,
  address,
  city,
  country,
  variant = 'detail',
  className,
  label,
  onResolved,
}: ProjectMapProps) {
  const { t } = useTranslation();
  const hasExplicitCoords = isFiniteNumber(lat) && isFiniteNumber(lng);

  const [resolved, setResolved] = useState<LatLng | null>(
    hasExplicitCoords ? { lat: lat as number, lng: lng as number } : null,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [popupOpen, setPopupOpen] = useState(false);

  const query = useMemo(
    () => (hasExplicitCoords ? null : buildGeocodeQuery(address, city, country)),
    [hasExplicitCoords, address, city, country],
  );

  useEffect(() => {
    if (hasExplicitCoords) {
      setResolved({ lat: lat as number, lng: lng as number });
      return;
    }
    if (!query) {
      setResolved(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    geocode(query, controller.signal)
      .then((coords) => {
        if (controller.signal.aborted) return;
        if (coords) {
          setResolved(coords);
          onResolved?.(coords);
        } else {
          setError(true);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
    // onResolved intentionally omitted — parents often pass an inline
    // callback; re-running the fetch on every render would hammer the geocoder.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasExplicitCoords, lat, lng, query]);

  const isCard = variant === 'card';
  // detail variant defaults to ``h-full`` so the parent grid (e.g. the
  // project-detail Map+Weather panel) can stretch the map to match the
  // height of its sibling. A custom ``className`` override still wins
  // because tailwind's JIT utilities cascade after the default class.
  const heightClass = isCard ? 'h-28' : 'h-full';

  const shell = (content: React.ReactNode) => (
    <div
      className={clsx(
        'relative overflow-hidden rounded-xl border border-border-light bg-gradient-to-br from-slate-100 via-slate-50 to-blue-50/30 dark:from-slate-900 dark:via-slate-900/60 dark:to-slate-800',
        heightClass,
        className,
      )}
    >
      {content}
    </div>
  );

  if (!resolved && !loading && !query) {
    return shell(
      <div className="absolute inset-0 flex items-center justify-center text-content-quaternary">
        <MapPin size={isCard ? 20 : 28} strokeWidth={1.5} />
      </div>,
    );
  }

  if (loading) {
    return shell(
      <div className="absolute inset-0 flex items-center justify-center gap-2 text-content-tertiary">
        <Loader2 size={14} className="animate-spin" />
        <span className="text-[11px] font-medium">
          {t('projects.map_locating', { defaultValue: 'Locating…' })}
        </span>
      </div>,
    );
  }

  if (error || !resolved) {
    return shell(
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-content-quaternary">
        <MapPin size={isCard ? 18 : 24} strokeWidth={1.5} />
        <span className="text-[10px] font-medium">
          {query || t('projects.map_no_location', { defaultValue: 'No location set' })}
        </span>
      </div>,
    );
  }

  // Card variant: static raster thumbnail — no MapLibre, no WebGL, no
  // perpetual tile streaming. The marker is positioned at the resolved
  // point's fractional offset within the displayed tile so it lands on
  // the actual location rather than always dead-centre.
  if (isCard) {
    const z = STATIC_TILE_ZOOM;
    const fracX = lngToTileX(resolved.lng, z) % 1;
    const fracY = latToTileY(resolved.lat, z) % 1;
    return (
      <div
        className={clsx(
          'relative overflow-hidden rounded-xl border border-border-light bg-slate-100 dark:bg-slate-800',
          heightClass,
          className,
        )}
      >
        <img
          src={staticTileUrl(resolved)}
          alt={label || query || t('projects.map_thumbnail_alt', { defaultValue: 'Project location map' })}
          loading="lazy"
          decoding="async"
          draggable={false}
          className="absolute inset-0 h-full w-full select-none object-cover"
          onError={() => setError(true)}
        />
        {/* Marker pinned at the coordinate's fractional offset in the tile. */}
        <div
          className="pointer-events-none absolute z-[1] flex h-6 w-6 -translate-x-1/2 -translate-y-full items-center justify-center"
          style={{ left: `${fracX * 100}%`, top: `${fracY * 100}%` }}
          aria-hidden="true"
        >
          <span className="flex h-5 w-5 items-center justify-center rounded-full bg-oe-blue text-white shadow-md shadow-oe-blue/40 ring-2 ring-white">
            <MapPin size={11} fill="currentColor" strokeWidth={0} />
          </span>
        </div>
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/40 via-transparent to-transparent" />
        {(label || query) && (
          <div className="pointer-events-none absolute inset-x-2 bottom-2 flex items-center gap-1 rounded-md bg-surface-elevated/90 backdrop-blur-sm px-2 py-1 shadow-sm">
            <MapPin size={11} className="shrink-0 text-oe-blue" />
            <span className="truncate text-[11px] font-medium text-content-primary">
              {label || query}
            </span>
          </div>
        )}
      </div>
    );
  }

  const zoom = 13;

  return (
    <div
      className={clsx(
        'relative overflow-hidden rounded-xl border border-border-light',
        heightClass,
        className,
      )}
    >
      <Map
        initialViewState={{
          longitude: resolved.lng,
          latitude: resolved.lat,
          zoom,
        }}
        mapStyle={VECTOR_BASEMAP_STYLE_URL}
        style={{ width: '100%', height: '100%' }}
        dragRotate={false}
        attributionControl={false}
      >
        <NavigationControl position="top-right" showCompass={false} />
        <AttributionControl
          compact
          customAttribution={TILE_ATTRIBUTION_HTML}
        />

        <Marker
          longitude={resolved.lng}
          latitude={resolved.lat}
          anchor="bottom"
          onClick={(e) => {
            e.originalEvent.stopPropagation();
            if (label) setPopupOpen(true);
          }}
        >
          <div
            className="relative flex h-8 w-8 items-center justify-center"
            aria-label={label || 'Project location'}
          >
            <span className="absolute inset-0 rounded-full bg-oe-blue/25 animate-ping" />
            <span className="relative flex h-6 w-6 items-center justify-center rounded-full bg-oe-blue text-white shadow-lg shadow-oe-blue/40 ring-2 ring-white">
              <MapPin size={14} fill="currentColor" strokeWidth={0} />
            </span>
          </div>
        </Marker>

        {popupOpen && label && (
          <Popup
            longitude={resolved.lng}
            latitude={resolved.lat}
            anchor="bottom"
            onClose={() => setPopupOpen(false)}
            closeButton
            closeOnClick={false}
            offset={28}
          >
            <div className="text-xs font-medium text-content-primary">{label}</div>
          </Popup>
        )}
      </Map>
    </div>
  );
}
