import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import ChatDock, { linkTargets, linkify } from './ChatDock';
import type { BuildingModel } from '../../lib/building';
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
      .mockResolvedValue({ reply: 'Room 2.04, second floor.', thread_id: 't1', building_changed: false });
    const user = userEvent.setup();

    render(<ChatDock health={health} backendDown={false} draft={null} />);
    await user.type(screen.getByLabelText('Message'), 'Where is the 3D printer?');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByText('Room 2.04, second floor.')).toBeInTheDocument();
    expect(screen.getByText('Where is the 3D printer?')).toBeInTheDocument();
    expect(chat).toHaveBeenCalledWith('Where is the 3D printer?', undefined, null);

    await user.type(screen.getByLabelText('Message'), 'And the lift?');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    expect(chat).toHaveBeenLastCalledWith('And the lift?', 't1', null);
  });

  it('asks for a reload when the agent changed the building', async () => {
    vi.spyOn(api, 'chat').mockResolvedValue({ reply: 'Lift closed.', thread_id: 't1', building_changed: true });
    const changed = vi.fn();
    const user = userEvent.setup();

    render(<ChatDock health={health} backendDown={false} draft={null} buildingId="b1" onBuildingChanged={changed} />);
    await user.type(screen.getByLabelText('Message'), 'Close the lift');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByText('Lift closed.')).toBeInTheDocument();
    expect(changed).toHaveBeenCalledOnce();
  });

  it('sends a suggestion with one tap', async () => {
    const chat = vi.spyOn(api, 'chat').mockResolvedValue({ reply: 'Here.', thread_id: 't1', building_changed: false });
    const user = userEvent.setup();

    render(<ChatDock health={health} backendDown={false} draft={null} />);
    await user.click(screen.getByRole('button', { name: 'Which rooms are on floor 1?' }));

    expect(chat).toHaveBeenCalledWith('Which rooms are on floor 1?', undefined, null);
    expect(await screen.findByText('Here.')).toBeInTheDocument();
  });

  it('marks replies rejected by the guardrail', async () => {
    vi.spyOn(api, 'chat').mockResolvedValue({
      reply: 'I can only help with this building.',
      thread_id: 't1',
      building_changed: false,
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

  it('sends the selected building so the agent gets its navgraph tools', async () => {
    const chat = vi.spyOn(api, 'chat').mockResolvedValue({ reply: 'Here.', thread_id: 't1', building_changed: false });
    const user = userEvent.setup();

    render(<ChatDock health={health} backendDown={false} draft={null} buildingId="b1" />);
    await user.click(screen.getByRole('button', { name: 'Which rooms are on floor 1?' }));

    expect(chat).toHaveBeenCalledWith('Which rooms are on floor 1?', undefined, 'b1');
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

  it('starts over after an error with a fresh thread', async () => {
    const chat = vi
      .spyOn(api, 'chat')
      .mockResolvedValueOnce({ reply: 'First.', thread_id: 't1', building_changed: false })
      .mockRejectedValueOnce(new Error('down'))
      .mockResolvedValue({ reply: 'Fresh.', thread_id: 't2', building_changed: false });
    const user = userEvent.setup();

    render(<ChatDock health={health} backendDown={false} draft={null} />);
    await user.type(screen.getByLabelText('Message'), 'one');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByText('First.');
    await user.type(screen.getByLabelText('Message'), 'two');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByRole('alert');

    await user.click(screen.getByRole('button', { name: /Start over/ }));
    expect(screen.queryByText('First.')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    await user.type(screen.getByLabelText('Message'), 'three');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    expect(chat).toHaveBeenLastCalledWith('three', undefined, null);
  });

  it('offers a new conversation when the building changes', async () => {
    vi.spyOn(api, 'chat').mockResolvedValue({ reply: 'Here.', thread_id: 't1', building_changed: false });
    const user = userEvent.setup();

    const { rerender } = render(<ChatDock health={health} backendDown={false} draft={null} buildingId="b1" />);
    await user.click(screen.getByRole('button', { name: 'Which rooms are on floor 1?' }));
    await screen.findByText('Here.');
    rerender(<ChatDock health={health} backendDown={false} draft={null} buildingId="b2" />);

    await user.click(screen.getByRole('button', { name: 'Start new conversation' }));
    expect(screen.queryByText('Here.')).not.toBeInTheDocument();
  });

  it('links rooms and unique assets in replies, not shared names', () => {
    const model = {
      spaces: [{ id: 'sp-1', name: 'Print Lab', ref: '2.04' }],
      assets: [
        { id: 'a-1', name: '3D Printer' },
        { id: 'a-2', name: 'Desk' },
        { id: 'a-3', name: 'Desk' },
      ],
    } as unknown as BuildingModel;
    const parts = linkify('The 3D printer is in Print Lab (2.04), by a Desk.', linkTargets(model));
    expect(parts.filter((p) => typeof p !== 'string')).toEqual([
      ['3D printer', { kind: 'asset', id: 'a-1' }],
      ['Print Lab', { kind: 'space', id: 'sp-1' }],
      ['2.04', { kind: 'space', id: 'sp-1' }],
    ]);
    expect(parts.join('')).toContain('by a Desk.');
  });
});
