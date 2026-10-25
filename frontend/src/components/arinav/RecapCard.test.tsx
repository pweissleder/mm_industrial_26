import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import RecapCard, { formatMinutes } from './RecapCard';
import type { MemoryRecap } from '../../lib/memory';

const base: MemoryRecap = {
  session_id: 's',
  kind: 'real',
  label: 'Your session',
  synthetic: false,
  created_at: '2026-10-25T12:00:00+00:00',
  event_count: 12,
  places_reached: ['Exit', 'Lab 2.07'],
  places_reached_count: 2,
  arrivals: 3,
  walked_m: 61.137,
  floors_visited: ['Floor 3', 'Ground floor'],
  favourite_destination: 'Exit',
  navigations_started: 4,
  navigations_completed: 3,
  reroutes: 1,
  simulated_minutes: 2.5,
};

const stat = (label: string) => screen.getByText(label).closest('.recap-card__stat') as HTMLElement;

describe('RecapCard', () => {
  it('renders the recorded numbers', () => {
    render(<RecapCard recap={base} />);
    expect(screen.getByRole('heading', { name: 'Your session at a glance' })).toBeInTheDocument();
    expect(within(stat('Places reached')).getByText('2')).toBeInTheDocument();
    expect(within(stat('Places reached')).getByText('Exit, Lab 2.07')).toBeInTheDocument();
    expect(within(stat('Walked')).getByText('61.1 m')).toBeInTheDocument();
    expect(within(stat('Floors')).getByText('2')).toBeInTheDocument();
    expect(within(stat('Time navigating')).getByText('2 min 30 s')).toBeInTheDocument();
    expect(within(stat('Favourite destination')).getByText('Exit')).toBeInTheDocument();
    expect(screen.getByText('3 of 4 navigations completed · 1 reroute')).toBeInTheDocument();
    expect(screen.queryByText(/Demo/)).not.toBeInTheDocument();
  });

  it('shows dashes instead of numbers when nothing was recorded', () => {
    const empty: MemoryRecap = {
      ...base,
      event_count: 0,
      places_reached: [],
      places_reached_count: 0,
      arrivals: 0,
      walked_m: 0,
      floors_visited: [],
      favourite_destination: null,
      navigations_started: 0,
      navigations_completed: 0,
      reroutes: 0,
      simulated_minutes: 0,
    };
    for (const recap of [empty, null]) {
      const { unmount } = render(<RecapCard recap={recap} />);
      for (const label of ['Places reached', 'Walked', 'Floors', 'Time navigating', 'Favourite destination']) {
        expect(within(stat(label)).getByText('—')).toBeInTheDocument();
      }
      expect(screen.queryByText(/\d+(\.\d)? m\b/)).not.toBeInTheDocument();
      unmount();
    }
  });

  it('badges demo and test sessions', () => {
    const { rerender } = render(<RecapCard recap={{ ...base, kind: 'demo', label: 'Demo session (synthetic)', synthetic: true }} />);
    expect(screen.getByText('Demo · synthetic')).toBeInTheDocument();
    expect(screen.getByText('Demo session (synthetic)')).toBeInTheDocument();
    rerender(<RecapCard recap={{ ...base, kind: 'test', label: 'Test session (not user history)', synthetic: true }} />);
    expect(screen.getByText('Test · not your history')).toBeInTheDocument();
  });

  it('wires the session actions', async () => {
    const onNewSession = vi.fn();
    const onDelete = vi.fn();
    const onExport = vi.fn();
    const user = userEvent.setup();
    render(<RecapCard recap={base} onNewSession={onNewSession} onDelete={onDelete} onExport={onExport} />);
    await user.click(screen.getByRole('button', { name: 'New session' }));
    await user.click(screen.getByRole('button', { name: 'Delete history' }));
    await user.click(screen.getByRole('button', { name: 'Export' }));
    expect(onNewSession).toHaveBeenCalledOnce();
    expect(onDelete).toHaveBeenCalledOnce();
    expect(onExport).toHaveBeenCalledOnce();
  });

  it('formats short simulated times in seconds', () => {
    expect(formatMinutes({ ...base, simulated_minutes: 0.75 })).toBe('45 s');
    expect(formatMinutes({ ...base, simulated_minutes: 3 })).toBe('3 min');
  });
});
