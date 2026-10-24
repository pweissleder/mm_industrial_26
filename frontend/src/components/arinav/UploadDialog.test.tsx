import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from '../../lib/api';
import UploadDialog from './UploadDialog';

beforeAll(() => {
  // jsdom may lack modal dialogs; opening one only needs to set `open` here.
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.open = true;
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.open = false;
  };
});

const file = new File(['{}'], 'notes.txt', { type: 'text/plain' });

function setup() {
  const onUploaded = vi.fn();
  render(
    <UploadDialog open datasets={['hall-3']} onClose={() => {}} onUploaded={onUploaded} />,
  );
  return { onUploaded, user: userEvent.setup() };
}

describe('UploadDialog', () => {
  it('flags a name that is already in use, ignoring case', async () => {
    const check = vi.spyOn(api, 'datasetAvailability');
    const { user } = setup();

    await user.type(screen.getByLabelText(/name the location/i), 'Hall-3');

    expect(screen.getByText(/already exists/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create location' })).toBeDisabled();
    expect(check).not.toHaveBeenCalled();
  });

  it('creates a location once the backend confirms the name', async () => {
    vi.spyOn(api, 'datasetAvailability').mockResolvedValue({
      dataset: 'plant-2',
      available: true,
      reason: null,
    });
    const upload = vi
      .spyOn(api, 'upload')
      .mockResolvedValue({ dataset: 'plant-2', files: [], ingestible: false });
    const { user, onUploaded } = setup();

    await user.type(screen.getByLabelText(/name the location/i), 'plant-2');
    await user.upload(document.querySelector<HTMLInputElement>('input[type=file]')!, file);
    expect(await screen.findByText('Name is available.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Create location' }));

    expect(upload).toHaveBeenCalledWith('plant-2', [file]);
    await waitFor(() => expect(onUploaded).toHaveBeenCalled());
  });

  it('shows a conflict from the backend on the name', async () => {
    vi.spyOn(api, 'datasetAvailability').mockResolvedValue({
      dataset: 'plant-2',
      available: true,
      reason: null,
    });
    vi.spyOn(api, 'upload').mockRejectedValue(
      new ApiError(409, "A location named 'plant-2' already exists"),
    );
    const { user } = setup();

    await user.type(screen.getByLabelText(/name the location/i), 'plant-2');
    await user.upload(document.querySelector<HTMLInputElement>('input[type=file]')!, file);
    await screen.findByText('Name is available.');
    await user.click(screen.getByRole('button', { name: 'Create location' }));

    expect(await screen.findByText(/already exists/)).toBeInTheDocument();
  });
});
