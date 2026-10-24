import { render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { api, type LocationRecord, type RunRecord } from '../../lib/api';
import type { BuildingModel } from '../../lib/building';
import { PipelineChip, PipelineDialog } from './PipelineStatus';
import { usePipeline, type Pipeline } from './usePipeline';

beforeAll(() => {
  // jsdom may lack modal dialogs; opening one only needs to set `open` here.
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.open = true;
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.open = false;
  };
});

function makeRun(patch: Partial<RunRecord> = {}): RunRecord {
  return {
    run_id: 'r1',
    dataset: 'Test3',
    location_id: 'osm-1',
    status: 'running',
    current_stage: 'floorplan',
    offline: true,
    created_at: '2026-10-24T10:00:00Z',
    updated_at: '2026-10-24T10:00:00Z',
    stages: { raw_validation: { status: 'passed', attempts: 1, summary: null } },
    pending_review: null,
    decisions: [],
    error: null,
    ...patch,
  };
}

const location: LocationRecord = {
  id: 'osm-1',
  dataset: 'Test3',
  name: 'Hall',
  updated_at: '2026-10-24T10:05:00Z',
  pillars: { assets_3d: { status: 'done', note: null } },
  metadata: { summary: 'A small hall.', key_facts: [], spaces: {} },
  artifacts: { model: '3d/model.glb' },
};

const building = { building: { id: 'osm-1' } } as BuildingModel;

const review = {
  review_id: 'r1:floorplan:2',
  run_id: 'r1',
  stage: 'floorplan',
  title: 'Floorplan needs a decision',
  message: 'Two rooms overlap.',
  issues: ['S001 overlaps S002'],
  artifacts: ['3d/previews/plan_floor_0.png'],
  options: [
    { id: 'accept_with_warnings' as const, label: 'Accept with warnings', description: '' },
    { id: 'retry' as const, label: 'Retry stage', description: '' },
    { id: 'abort' as const, label: 'Abort run', description: '' },
  ],
  allow_note: true,
};

describe('usePipeline', () => {
  it('starts a run for a dataset that was never processed', async () => {
    vi.spyOn(api, 'runs').mockResolvedValue([]);
    const start = vi.spyOn(api, 'startRun').mockResolvedValue(makeRun());

    const { result } = renderHook(() => usePipeline('Test3', 'osm-1', null));

    await waitFor(() => expect(result.current.state.phase).toBe('running'));
    expect(start).toHaveBeenCalledWith('Test3', false);
  });

  it('loads the high-quality result of a finished run', async () => {
    vi.spyOn(api, 'runs').mockResolvedValue([makeRun({ status: 'completed', current_stage: null })]);
    vi.spyOn(api, 'location').mockResolvedValue(location);
    vi.spyOn(api, 'locationBuilding').mockResolvedValue(building);
    const start = vi.spyOn(api, 'startRun');

    const { result } = renderHook(() => usePipeline('Test3', 'osm-1', null));

    await waitFor(() => expect(result.current.state.phase).toBe('ready'));
    const state = result.current.state;
    expect(state.phase === 'ready' && state.hq.modelUrl).toBe(
      '/api/locations/osm-1/model.glb?v=2026-10-24T10%3A05%3A00Z',
    );
    expect(start).not.toHaveBeenCalled();
  });

  it('starts a run when asked, e.g. after an upload, even if one finished before', async () => {
    const runs = vi.spyOn(api, 'runs');
    const start = vi.spyOn(api, 'startRun').mockResolvedValue(makeRun());

    renderHook(() => usePipeline('Test3', 'osm-1', { dataset: 'Test3', nonce: 1 }));

    await waitFor(() => expect(start).toHaveBeenCalledWith('Test3', false));
    expect(runs).not.toHaveBeenCalled();
  });

  it('does not restart a failed run on its own', async () => {
    vi.spyOn(api, 'runs').mockResolvedValue([makeRun({ status: 'failed', error: 'boom' })]);
    vi.spyOn(api, 'location').mockResolvedValue({ ...location, artifacts: {} });
    const start = vi.spyOn(api, 'startRun');

    const { result } = renderHook(() => usePipeline('Test3', 'osm-1', null));

    await waitFor(() => expect(result.current.state).toMatchObject({ phase: 'failed', message: 'boom' }));
    expect(start).not.toHaveBeenCalled();
  });
});

describe('PipelineDialog', () => {
  it('shows a review request and sends the decision with the note', async () => {
    const run = makeRun({ status: 'needs_review', pending_review: review });
    const pipeline: Pipeline = {
      state: { phase: 'review', run },
      start: vi.fn(),
      resume: vi.fn().mockResolvedValue(undefined),
    };
    const user = userEvent.setup();

    render(<PipelineChip state={pipeline.state} onOpen={() => {}} />);
    expect(screen.getByRole('button', { name: /Review needed/ })).toBeInTheDocument();

    render(<PipelineDialog open pipeline={pipeline} onClose={() => {}} />);
    expect(screen.getByText('Two rooms overlap.')).toBeInTheDocument();
    expect(screen.getByText('S001 overlaps S002')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'plan_floor_0.png' })).toHaveAttribute(
      'src',
      '/api/locations/osm-1/files/3d/previews/plan_floor_0.png',
    );

    await user.type(screen.getByLabelText(/Note for the pipeline/), 'Overlap is intended.');
    await user.click(screen.getByRole('button', { name: 'Accept with warnings' }));
    expect(pipeline.resume).toHaveBeenCalledWith('accept_with_warnings', 'Overlap is intended.');
  });

  it('offers a retry when the build failed', async () => {
    const pipeline: Pipeline = {
      state: { phase: 'failed', run: makeRun({ status: 'failed' }), message: 'Floorplan crashed.' },
      start: vi.fn().mockResolvedValue(undefined),
      resume: vi.fn(),
    };
    const user = userEvent.setup();

    render(<PipelineDialog open pipeline={pipeline} onClose={() => {}} />);
    expect(screen.getByText('Floorplan crashed.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(pipeline.start).toHaveBeenCalledWith(true);
  });
});
