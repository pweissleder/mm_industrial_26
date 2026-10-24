import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import ChatDemo from './ChatDemo';
import { api } from '../lib/api';

const health = {
  status: 'ok',
  environment: 'test',
  llm_enabled: false,
  managed_agent_enabled: false,
  tracing_enabled: false,
};

describe('ChatDemo', () => {
  it('shows backend status from the health endpoint', async () => {
    vi.spyOn(api, 'health').mockResolvedValue(health);

    render(<ChatDemo />);

    expect(await screen.findByText('Backend ok · offline mode')).toBeInTheDocument();
  });

  it('sends a message and renders the reply', async () => {
    vi.spyOn(api, 'health').mockResolvedValue(health);
    const chat = vi
      .spyOn(api, 'chat')
      .mockResolvedValue({ reply: 'Room 2.04, second floor.', thread_id: 't1' });
    const user = userEvent.setup();

    render(<ChatDemo />);
    await user.type(screen.getByLabelText('Message'), 'Where is the 3D printer?');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByText('Room 2.04, second floor.')).toBeInTheDocument();
    expect(screen.getByText('Where is the 3D printer?')).toBeInTheDocument();
    expect(chat).toHaveBeenCalledWith('Where is the 3D printer?', undefined);
  });

  it('shows an error when the backend is unreachable', async () => {
    vi.spyOn(api, 'health').mockRejectedValue(new Error('down'));
    vi.spyOn(api, 'chat').mockRejectedValue(new Error('down'));
    const user = userEvent.setup();

    render(<ChatDemo />);
    expect(await screen.findByText('Backend unreachable')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Message'), 'hello');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('not answering');
  });
});
