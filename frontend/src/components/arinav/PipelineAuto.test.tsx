import { render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { api, type RunRecord } from '../../lib/api';
import { PipelineChip, PipelineDialog } from './PipelineStatus';
import { usePipeline, type Pipeline } from './usePipeline';

beforeAll(() => {
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.open = true;
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.open = false;
  };
});

const run = (patch: Partial<RunRecord> = {}): RunRecord => ({
  run_id: 'r1',
  dataset: 'New1',
  location_id: 'new-1',
  status: 'running',
  current_stage: 'floorplan',
  offline: false,
  created_at: '2026-10-24T10:00:00Z',
  updated_at: '2026-10-24T10:00:00Z',
  stages: {},
  pending_review: null,
  decisions: [],
  error: null,
  ...patch,
});

describe('usePipeline with auto-build off', () => {
  it('leaves a never-processed building in the preview without starting a run', async () => {
    vi.spyOn(api, 'runs').mockResolvedValue([]);
    const start = vi.spyOn(api, 'startRun').mockResolvedValue(run());

    const { result } = renderHook(() => usePipeline('New1', 'new-1', null, { auto: false }));

    await waitFor(() => expect(result.current.state.phase).toBe('not_built'));
    expect(start).not.toHaveBeenCalled();
  });

  it('does not start a run after an upload either', async () => {
    vi.spyOn(api, 'runs').mockResolvedValue([]);
    const start = vi.spyOn(api, 'startRun').mockResolvedValue(run());

    const { result } = renderHook(() =>
      usePipeline('New1', 'new-1', { dataset: 'New1', nonce: 1 }, { auto: false }),
    );

    await waitFor(() => expect(result.current.state.phase).toBe('not_built'));
    expect(start).not.toHaveBeenCalled();
  });

  it('builds only when asked, and switching auto-build on does not start one for the open building', async () => {
    vi.spyOn(api, 'runs').mockResolvedValue([]);
    const start = vi.spyOn(api, 'startRun').mockResolvedValue(run());

    const { result, rerender } = renderHook(({ auto }) => usePipeline('New1', 'new-1', null, { auto }), {
      initialProps: { auto: false },
    });
    await waitFor(() => expect(result.current.state.phase).toBe('not_built'));
    rerender({ auto: true });
    expect(start).not.toHaveBeenCalled();

    await result.current.start(false);
    await waitFor(() => expect(result.current.state.phase).toBe('running'));
    expect(start).toHaveBeenCalledWith('New1', false);
  });
});

describe('not built yet', () => {
  it('shows an LQ chip and a dialog that starts the build on request', async () => {
    const pipeline: Pipeline = {
      state: { phase: 'not_built' },
      start: vi.fn().mockResolvedValue(undefined),
      resume: vi.fn(),
    };
    const user = userEvent.setup();

    render(<PipelineChip state={pipeline.state} onOpen={() => {}} />);
    expect(screen.getByRole('button', { name: /LQ only/ })).toBeInTheDocument();

    render(<PipelineDialog open pipeline={pipeline} onClose={() => {}} />);
    await user.click(screen.getByRole('button', { name: 'Build HQ model' }));
    expect(pipeline.start).toHaveBeenCalledWith(false);
  });
});
