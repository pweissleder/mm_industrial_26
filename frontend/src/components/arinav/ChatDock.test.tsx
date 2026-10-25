import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
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

const assistant = {
  live: true,
  voice: true,
  model: 'm',
  effort: 'low',
  tts_speed: { min: 0.7, max: 1.3, default: 1 },
};

const turnReply = (reply: string) => ({
  session_id: 's1',
  reply,
  status: 'ok',
  phases: [],
  ui: {},
  tools: [],
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ChatDock profiles', () => {
  const live = { health, backendDown: false, draft: null, assistant };

  it('hides the status in user mode and shows it in developer', () => {
    const { unmount } = render(<ChatDock {...live} profile="user" />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    unmount();
    render(<ChatDock {...live} profile="developer" />);
    expect(screen.getByRole('status')).toHaveTextContent('Claude · live · voice');
  });

  it('keeps the simulated location chip developer-only', () => {
    const sel = { name: 'Lab', room: true };
    const { unmount } = render(<ChatDock {...live} profile="user" selection={sel} />);
    expect(screen.queryByRole('button', { name: /Set simulated location/ })).not.toBeInTheDocument();
    unmount();
    render(<ChatDock {...live} profile="developer" />);
    expect(screen.getByRole('button', { name: /Set simulated location/ })).toBeInTheDocument();
  });

  it('shows only the input bar in navigator mode and always speaks', async () => {
    const turn = vi.spyOn(api, 'assistantTurn').mockResolvedValue(turnReply('Turn left.'));
    const user = userEvent.setup();
    render(<ChatDock {...live} profile="navigator" selection={{ name: 'Lab', room: true }} />);

    expect(screen.queryByLabelText('Suggestions')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Detach/ })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('Message'), 'Where to?');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    await screen.findByText('Turn left.');
    expect(turn.mock.calls[0]![0]).toMatchObject({ speak: true, voice: false });
    expect(screen.queryByLabelText('Conversation')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Minimise chat' })).not.toBeInTheDocument();
  });

  it('links rooms in the navigator reply line', async () => {
    vi.spyOn(api, 'assistantTurn').mockResolvedValue(turnReply('Head to Print Lab now.'));
    const onSelect = vi.fn();
    const model = {
      spaces: [{ id: 'sp-1', name: 'Print Lab', ref: '1.01' }],
      assets: [],
    } as unknown as BuildingModel;
    const user = userEvent.setup();
    render(<ChatDock {...live} profile="navigator" model={model} onSelect={onSelect} />);
    await user.type(screen.getByLabelText('Message'), 'Where to?');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    await user.click(await screen.findByRole('button', { name: 'Print Lab' }));
    expect(onSelect).toHaveBeenCalledWith({ kind: 'space', id: 'sp-1' });
  });

  it('hides the speaker toggle and mic when the server has no voice', async () => {
    vi.spyOn(api, 'assistantTurn').mockResolvedValue(turnReply('Ok.'));
    const user = userEvent.setup();
    render(<ChatDock {...live} assistant={{ ...assistant, voice: false }} profile="user" />);
    expect(screen.queryByRole('button', { name: 'Hold to talk' })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('Message'), 'hi');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByText('Ok.');
    expect(screen.queryByRole('button', { name: /replies aloud/ })).not.toBeInTheDocument();
  });

  it('folds the conversation after 15 s without interaction', async () => {
    vi.spyOn(api, 'assistantTurn').mockResolvedValue(turnReply('Ok.'));
    render(<ChatDock {...live} profile="user" />);
    vi.useFakeTimers();
    try {
      fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'hi' } });
      fireEvent.submit(screen.getByLabelText('Message').closest('form')!);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10);
      });
      expect(screen.getByText('Ok.')).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      fireEvent.wheel(screen.getByLabelText('Conversation'));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(screen.getByLabelText('Conversation')).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(6_000);
      });
      expect(screen.queryByLabelText('Conversation')).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows the location as a pill in the input bar and clears it', async () => {
    const onClearSimulated = vi.fn();
    const simulated = { endpoint: { kind: 'space' as const, id: 'sp-1' }, label: 'Print Lab' };
    const user = userEvent.setup();
    const { unmount } = render(
      <ChatDock {...live} profile="user" simulated={simulated} onClearSimulated={onClearSimulated} />,
    );
    expect(screen.getByText('Location: Print Lab')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear location' }));
    expect(onClearSimulated).toHaveBeenCalled();
    unmount();
    render(<ChatDock {...live} profile="navigator" simulated={simulated} />);
    expect(screen.queryByText('Location: Print Lab')).not.toBeInTheDocument();
  });

  it('attaches the selection and stops sending it once detached', async () => {
    const turn = vi.spyOn(api, 'assistantTurn').mockResolvedValue(turnReply('Ok.'));
    const user = userEvent.setup();
    const ui = { building_id: 'b1', inspected: { kind: 'space', id: 'sp-1' } };
    const sel = { name: 'Print Lab', room: true };
    const { rerender } = render(<ChatDock {...live} profile="user" ui={ui} selection={sel} />);

    expect(screen.getByText('Print Lab')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Message'), 'one');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByText('Ok.');
    expect(turn.mock.calls[0]![0].ui.inspected).toEqual({ kind: 'space', id: 'sp-1' });

    await user.click(screen.getByRole('button', { name: 'Detach Print Lab' }));
    expect(screen.queryByText('Print Lab')).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('Message'), 'two');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await vi.waitFor(() => expect(turn).toHaveBeenCalledTimes(2));
    expect(turn.mock.calls[1]![0].ui.inspected).toBeNull();

    // A new selection attaches again.
    rerender(
      <ChatDock
        {...live}
        profile="user"
        ui={{ building_id: 'b1', inspected: { kind: 'space', id: 'sp-2' } }}
        selection={{ name: 'Kitchen', room: true }}
      />,
    );
    expect(screen.getByText('Kitchen')).toBeInTheDocument();
  });

  it('uses the suggestions prop instead of the static list', () => {
    render(
      <ChatDock {...live} profile="user" suggestions={['Where is the kitchen?', 'Who sits here?']} />,
    );
    expect(screen.getByRole('button', { name: 'Where is the kitchen?' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Which rooms are on floor 1?' })).not.toBeInTheDocument();
  });

  it('reads replies aloud once the header toggle is on', async () => {
    const turn = vi.spyOn(api, 'assistantTurn').mockResolvedValue(turnReply('Ok.'));
    const user = userEvent.setup();
    render(<ChatDock {...live} profile="user" />);
    await user.type(screen.getByLabelText('Message'), 'hi');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByText('Ok.');
    expect(turn.mock.calls[0]![0]).toMatchObject({ speak: false });

    const toggle = screen.getByRole('button', { name: 'Read replies aloud' });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    await user.click(toggle);
    expect(screen.getByRole('button', { name: 'Stop reading replies aloud' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await user.type(screen.getByLabelText('Message'), 'again');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await vi.waitFor(() => expect(turn).toHaveBeenCalledTimes(2));
    expect(turn.mock.calls[1]![0]).toMatchObject({ speak: true });
  });
});

describe('ChatDock', () => {
  it('shows the agent status', () => {
    render(<ChatDock health={health} backendDown={false} draft={null} profile="developer" />);

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

    render(<ChatDock health={null} backendDown draft={null} profile="developer" />);
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

describe('ChatDock Call Wrapped', () => {
  const live = { health, backendDown: false, draft: null, assistant, profile: 'user' as const };

  it('opens the recap the backend starts when a conversation is reset', async () => {
    vi.spyOn(api, 'assistantTurn').mockResolvedValue(turnReply('Ok.'));
    const reset = vi
      .spyOn(api, 'assistantReset')
      .mockResolvedValue({ status: 'reset', recap: { id: 'rc1', status: 'pending' } });
    const onOpenRecap = vi.fn();
    const user = userEvent.setup();
    render(<ChatDock {...live} onOpenRecap={onOpenRecap} />);
    await user.type(screen.getByLabelText('Message'), 'Hi');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByText('Ok.');
    await user.click(screen.getByRole('button', { name: /New conversation/ }));
    expect(reset).toHaveBeenCalledWith('s1');
    await vi.waitFor(() => expect(onOpenRecap).toHaveBeenCalledWith('rc1'));
  });

  it('offers a recap started by a turn instead of interrupting', async () => {
    vi.spyOn(api, 'assistantTurn').mockResolvedValue({
      ...turnReply('Hello again.'),
      recap: { id: 'rc2', status: 'pending' },
    });
    const onOpenRecap = vi.fn();
    const user = userEvent.setup();
    render(<ChatDock {...live} onOpenRecap={onOpenRecap} />);
    await user.type(screen.getByLabelText('Message'), 'Hi');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByText('Your Call Wrapped is ready.');
    expect(onOpenRecap).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Watch your Call Wrapped' }));
    expect(onOpenRecap).toHaveBeenCalledWith('rc2');
  });
});
