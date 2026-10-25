// Who is in this building (read-only): you first as "You", then the others. Choosing someone with
// a place on the map selects them and zooms to them.
import type { UserPresence } from '../../lib/api';

interface Props {
  here: UserPresence[];
  personId: string | null;
  focused: string | null;
  onFocus: (presence: UserPresence) => void;
}

export default function PeopleHere({ here, personId, focused, onFocus }: Props) {
  return (
    <section className="section">
      <span className="section__title" id="people-here">
        People here
      </span>
      {here.length === 0 ? (
        <p className="muted">Nobody is in this building right now.</p>
      ) : (
        <ul className="people-list" aria-labelledby="people-here">
          {here.map((u) => {
            const self = u.profile.id === personId;
            const placed = !!u.position && u.available;
            const where = !u.available
              ? (u.unavailable_reason ?? 'Place no longer exists')
              : (u.position?.label ?? (u.position ? 'On the map' : 'No place set'));
            return (
              <li key={u.profile.id}>
                <button
                  type="button"
                  className="people-list__row"
                  aria-pressed={focused === u.profile.id}
                  disabled={!placed}
                  title={placed ? `Show ${self ? 'yourself' : u.profile.name}` : undefined}
                  onClick={() => onFocus(u)}
                >
                  <span
                    className="people__avatar people__avatar--sm"
                    style={{ background: u.profile.color }}
                    aria-hidden
                  >
                    {u.profile.initials}
                    {(self || u.status === 'online') && <span className="people__online" />}
                  </span>
                  <span className="people-list__text">
                    <strong>{self ? 'You' : u.profile.name}</strong>
                    <span className="muted">
                      {where}
                      {!self && u.status === 'offline' ? ' · offline' : ''}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
