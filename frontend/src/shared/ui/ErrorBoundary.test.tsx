// @ts-nocheck
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ErrorBoundary } from './ErrorBoundary';

function ThrowingComponent({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) throw new Error('Test render error');
  return <div>Normal content</div>;
}

describe('ErrorBoundary', () => {
  // Suppress console.error for expected errors in tests
  const originalError = console.error;
  beforeEach(() => { console.error = vi.fn(); });
  afterEach(() => { console.error = originalError; });

  it('should render children when no error', () => {
    render(
      <ErrorBoundary>
        <div>Hello World</div>
      </ErrorBoundary>,
    );
    expect(screen.getByText('Hello World')).toBeInTheDocument();
  });

  it('should render error UI when child throws', () => {
    render(
      <ErrorBoundary>
        <ThrowingComponent shouldThrow={true} />
      </ErrorBoundary>,
    );
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText(/unexpected error occurred/)).toBeInTheDocument();
  });

  it('should show error details in expandable section', () => {
    render(
      <ErrorBoundary>
        <ThrowingComponent shouldThrow={true} />
      </ErrorBoundary>,
    );
    expect(screen.getByText('Error details')).toBeInTheDocument();
    expect(screen.getByText('Test render error')).toBeInTheDocument();
  });

  it('should show Try again and Go to Dashboard buttons', () => {
    render(
      <ErrorBoundary>
        <ThrowingComponent shouldThrow={true} />
      </ErrorBoundary>,
    );
    expect(screen.getByText('Try again')).toBeInTheDocument();
    expect(screen.getByText('Go to Dashboard')).toBeInTheDocument();
  });

  it('should have a clickable Try again button', () => {
    render(
      <ErrorBoundary>
        <ThrowingComponent shouldThrow={true} />
      </ErrorBoundary>,
    );
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    const tryAgainButton = screen.getByText('Try again');
    expect(tryAgainButton).toBeInTheDocument();
    // Clicking Try again resets the error state (component will re-render children)
    fireEvent.click(tryAgainButton);
  });

  it('should render custom fallback when provided', () => {
    render(
      <ErrorBoundary fallback={<div>Custom Error UI</div>}>
        <ThrowingComponent shouldThrow={true} />
      </ErrorBoundary>,
    );
    expect(screen.getByText('Custom Error UI')).toBeInTheDocument();
  });

  it('should log error to console', () => {
    render(
      <ErrorBoundary>
        <ThrowingComponent shouldThrow={true} />
      </ErrorBoundary>,
    );
    expect(console.error).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// The nesting AppShell mounts: an outer `scope="app"` boundary wrapped around
// the chrome, and the pathname-keyed page boundary rendered by the chrome
// itself. Before the outer one existed, AppLayout sat above every boundary, so
// a throw in the header reached the root and React unmounted the document —
// measured as 0 characters of text and 0 children under #root, with the
// sidebar gone too. These tests hold the two halves of the guarantee apart: a
// page crash must still stop at the inner boundary with the chrome alive, and
// a chrome crash must produce the recovery card rather than an empty document.
// ---------------------------------------------------------------------------

// Stands in for AppLayout. It renders the chrome and hosts the page boundary,
// so a throw of its own happens before that boundary exists and can only be
// caught above — which is exactly the real topology.
function Chrome({ children, crash }) {
  if (crash) throw new Error('Chrome render error');
  return (
    <div>
      <nav>Sidebar</nav>
      <ErrorBoundary key="/boq">{children}</ErrorBoundary>
    </div>
  );
}

// The measured defect in the shape it actually arrived in: a 200 whose body is
// a well-formed object where the caller's type argument claimed an array.
// `?? []` answers for null and undefined and passes this straight through.
function ChromeFilteringAnObject({ projects }) {
  const names = (projects ?? []).filter((p) => p.name);
  return <div>{names.length}</div>;
}

function AppShellHarness({ crashChrome = false, crashPage = false }) {
  return (
    <ErrorBoundary scope="app">
      <Chrome crash={crashChrome}>
        <ThrowingComponent shouldThrow={crashPage} />
      </Chrome>
    </ErrorBoundary>
  );
}

describe('ErrorBoundary nesting around the app chrome', () => {
  const originalError = console.error;
  const originalLocation = window.location;

  beforeEach(() => { console.error = vi.fn(); });
  afterEach(() => {
    console.error = originalError;
    Object.defineProperty(window, 'location', {
      value: originalLocation,
      writable: true,
      configurable: true,
    });
  });

  function stubLocation() {
    const reload = vi.fn();
    Object.defineProperty(window, 'location', {
      value: { reload, href: 'http://localhost/boq' },
      writable: true,
      configurable: true,
    });
    return reload;
  }

  it('lets the inner page boundary keep the chrome on screen when a page throws', () => {
    render(<AppShellHarness crashPage={true} />);
    // The sidebar surviving is the proof the outer boundary did not take over:
    // React hands the throw to the nearest boundary below it.
    expect(screen.getByText('Sidebar')).toBeInTheDocument();
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('Test render error')).toBeInTheDocument();
    expect(screen.getByTestId('error-boundary-fallback').className).toContain('min-h-[60vh]');
  });

  it('catches a chrome render error instead of blanking the document', () => {
    const { container } = render(<AppShellHarness crashChrome={true} />);
    expect(container).not.toBeEmptyDOMElement();
    expect(container.textContent.length).toBeGreaterThan(0);
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('Chrome render error')).toBeInTheDocument();
    // The chrome is gone — correct for a crash in the chrome — and the card
    // takes the whole viewport in its place.
    expect(screen.queryByText('Sidebar')).not.toBeInTheDocument();
    expect(screen.getByTestId('error-boundary-fallback').className).toContain('min-h-screen');
  });

  it('catches the wrong-type crash the header shipped with', () => {
    const { container } = render(
      <ErrorBoundary scope="app">
        <ChromeFilteringAnObject projects={{ items: [], total: 0 }} />
      </ErrorBoundary>,
    );
    expect(container).not.toBeEmptyDOMElement();
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText(/is not a function/)).toBeInTheDocument();
  });

  it('reloads the document from the app-scope recovery button', () => {
    const reload = stubLocation();
    render(<AppShellHarness crashChrome={true} />);
    fireEvent.click(screen.getByText('Try again'));
    // Nothing outside this boundary survives to navigate with, so clearing the
    // state would rebuild the same failing chrome from the same caches.
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('leaves the page-scope recovery button on plain state reset', () => {
    const reload = stubLocation();
    render(
      <ErrorBoundary>
        <ThrowingComponent shouldThrow={true} />
      </ErrorBoundary>,
    );
    fireEvent.click(screen.getByText('Try again'));
    expect(reload).not.toHaveBeenCalled();
  });
});
