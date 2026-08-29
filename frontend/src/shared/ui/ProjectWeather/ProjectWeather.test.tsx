// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// The forecast widget is decoration, so a refused network must leave no
// trace of it on the page.
//
// WHY THIS EXISTS. The dashboard renders one summary widget per site, each
// asking api.open-meteo.com on its own, and the shared public endpoint
// answers 429 once enough of them ask at once. The `full` variant used to
// keep its card on a refusal: a bordered box headed "15-day forecast" and
// footed "refreshed hourly" with nothing between the two, naming data it
// did not have. It now renders nothing.
//
// WHAT THIS ASSERTS.
//   1. A forecast that arrives still renders, so "renders nothing" below is
//      not passing vacuously.
//   2. HTTP 429 renders nothing in the `full` variant.
//   3. A rejected fetch renders nothing in the `summary` variant.
//   4. A refusal is remembered, so a second mount for the same coordinates
//      does not ask the host that just said no.
//   5. A refusal is scoped to the coordinates it was refused for - pointing
//      the same widget at another project must not leave it blank.
//
// Assertion 5 is the only one that points the widget at a second location, so
// it is the only place a remembered refusal could be seen leaking from one
// project into the next. It pins the settled state and nothing finer: the
// widget is briefly blank while the second location loads either way, and the
// reset inside the effect is what keeps that window short rather than what
// this assertion measures.
//
// Run: npx vitest run src/shared/ui/ProjectWeather/ProjectWeather.test.tsx

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, cleanup } from '@testing-library/react';

import { ProjectWeather } from './ProjectWeather';

/** Three days is enough to render the grid; the component maps what it gets. */
function forecastBody() {
  return {
    daily: {
      time: ['2026-08-29', '2026-08-30', '2026-08-31'],
      weather_code: [0, 61, 3],
      temperature_2m_min: [14, 15, 13],
      temperature_2m_max: [24, 22, 21],
      precipitation_sum: [0, 4.2, 0],
    },
  };
}

/** The three fields of a Response this component reads, and no cast: the
 *  stub stands in for `fetch` through `vi.stubGlobal`, which is untyped. */
function answering(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  localStorage.clear();
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('ProjectWeather', () => {
  it('renders the forecast when the request succeeds', async () => {
    fetchMock.mockResolvedValue(answering(forecastBody()));

    render(<ProjectWeather lat={25.0107} lng={55.0633} />);

    expect(await screen.findByText('15-day forecast')).toBeInTheDocument();
    expect(screen.getByText(/Open-Meteo/)).toBeInTheDocument();
  });

  it('renders nothing at all when the service answers 429', async () => {
    fetchMock.mockResolvedValue(answering({}, 429));

    const { container } = render(<ProjectWeather lat={48.1372} lng={11.5756} />);

    await waitFor(() => expect(container).toBeEmptyDOMElement());
    expect(screen.queryByText('15-day forecast')).not.toBeInTheDocument();
    expect(screen.queryByText(/Open-Meteo/)).not.toBeInTheDocument();
  });

  it('renders nothing at all when the request cannot leave the site', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

    const { container } = render(
      <ProjectWeather lat={52.52} lng={13.405} variant="summary" />,
    );

    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('does not ask again inside the back-off window', async () => {
    fetchMock.mockResolvedValue(answering({}, 429));

    const first = render(<ProjectWeather lat={40.7128} lng={-74.006} />);
    await waitFor(() => expect(first.container).toBeEmptyDOMElement());
    expect(fetchMock).toHaveBeenCalledTimes(1);

    cleanup();
    const second = render(<ProjectWeather lat={40.7128} lng={-74.006} />);
    await waitFor(() => expect(second.container).toBeEmptyDOMElement());
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('keeps the refusal to the coordinates it was refused for', async () => {
    fetchMock
      .mockResolvedValueOnce(answering({}, 429))
      .mockResolvedValueOnce(answering(forecastBody()));

    const { container, rerender } = render(
      <ProjectWeather lat={-33.8688} lng={151.2093} />,
    );
    await waitFor(() => expect(container).toBeEmptyDOMElement());

    rerender(<ProjectWeather lat={1.3521} lng={103.8198} />);

    expect(await screen.findByText('15-day forecast')).toBeInTheDocument();
  });
});
