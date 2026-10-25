// The voice on/off button of the chat header and the navigation card: one setting, one look.
import { SpeakerIcon, SpeakerOffIcon } from './Icons';

export default function VoiceToggle({
  on,
  onToggle,
  className = '',
}: {
  on: boolean;
  onToggle: () => void;
  className?: string;
}) {
  return (
    <button
      className={`icon-btn voice-toggle ${className}`.trim()}
      aria-pressed={on}
      onClick={onToggle}
      aria-label={on ? 'Stop reading replies aloud' : 'Read replies aloud'}
      title={on ? 'Reading replies aloud (click to mute)' : 'Replies muted (click to read them aloud)'}
    >
      {on ? <SpeakerIcon width={20} height={20} /> : <SpeakerOffIcon width={20} height={20} />}
    </button>
  );
}
