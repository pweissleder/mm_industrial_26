import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import ChatDock from './ChatDock';
import { api } from '../../lib/api';

const health = {
  status: 'ok',
  environment: 'test',
  llm_enabled: false,
  managed_agent_enabled: false,
  tracing_enabled: false,
};

describe('ChatDock', () => {
  it('shows the agent status', () => {
    render(<ChatDock health={health} backendDown={false} draft={null} />);

    expect(screen.getByRole('status')).toHaveTextContent('Offline mode');
  });

  it('sends a message and renders the reply', async () => {
    const chat = vi
      .spyOn(api, 'chat')
      .mockResolvedValue({ reply: 'Room 2.04, second floor.', thread_id: 't1' });
    const user = userEvent.setup();

    render(<ChatDock health={health} backendDown={false} draft={null} />);
    await user.type(screen.getByLabelText('Message'), 'Where is the 3D printer?');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByText('Room 2.04, second floor.')).toBeInTheDocument();
    expect(screen.getByText('Where is the 3D printer?')).toBeInTheDocument();
    expect(chat).toHaveBeenCalledWith('Where is the 3D printer?', undefined);

    await user.type(screen.getByLabelText('Message'), 'And the lift?');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    expect(chat).toHaveBeenLastCalledWith('And the lift?', 't1');
  });

  it('sends a suggestion with one tap', async () => {
    const chat = vi.spyOn(api, 'chat').mockResolvedValue({ reply: 'Here.', thread_id: 't1' });
    const user = userEvent.setup();

    render(<ChatDock health={health} backendDown={false} draft={null} />);
    await user.click(screen.getByRole('button', { name: 'Which rooms are on floor 1?' }));

    expect(chat).toHaveBeenCalledWith('Which rooms are on floor 1?', undefined);
    expect(await screen.findByText('Here.')).toBeInTheDocument();
  });

  it('marks replies rejected by the guardrail', async () => {
    vi.spyOn(api, 'chat').mockResolvedValue({
      reply: 'I can only help with this building.',
      thread_id: 't1',
      guardrail: { category: 'off_topic', reason: 'Asks for a poem.', source: 'classifier' },
    });
    const user = userEvent.setup();

    render(<ChatDock health={health} backendDown={false} draft={null} />);
    await user.type(screen.getByLabelText('Message'), 'Write me a poem');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    const flag = await screen.findByText('Rejected by guidelines · off-topic');
    expect(flag).toHaveAttribute('title', 'Asks for a poem.');
    expect(flag.closest('li')).toHaveClass('chat__msg--blocked');
  });

  it('prefills the input from a draft', () => {
    render(
      <ChatDock health={health} backendDown={false} draft={{ text: 'How do I get to H-A1?', nonce: 1 }} />,
    );

    expect(screen.getByLabelText('Message')).toHaveValue('How do I get to H-A1?');
  });

  it('shows an error when the backend is unreachable', async () => {
    vi.spyOn(api, 'chat').mockRejectedValue(new Error('down'));
    const user = userEvent.setup();

    render(<ChatDock health={null} backendDown draft={null} />);
    expect(screen.getByRole('status')).toHaveTextContent('Backend unreachable');
    await user.type(screen.getByLabelText('Message'), 'hello');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('not answering');
  });
});
