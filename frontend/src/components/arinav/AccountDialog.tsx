// Choose who you are: avatar tiles of all people (accounts are global, passwordless). On a tab's
// first visit it is the login and can't be dismissed; from the header avatar it switches person.
import { useEffect, useRef, useState } from 'react';
import { api, type BuildingSummary, type UserDirectory, type UserSession } from '../../lib/api';
import { CloseIcon } from './Icons';
import type { Person } from './usePerson';

interface Props {
  open: boolean;
  /** `login`: nobody chosen yet, a choice is required. `switch`: change person or log out. */
  mode: 'login' | 'switch';
  person: Person;
  onClose: () => void;
  /** A person was chosen (their session says where they are). */
  onChosen: (session: UserSession) => void;
}

export default function AccountDialog({ open, mode, person, onClose, onChosen }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [directory, setDirectory] = useState<UserDirectory | null>(null);
  const [buildings, setBuildings] = useState<BuildingSummary[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const me = person.profile?.id ?? null;
  const login = mode === 'login';
  // Without configured people there is nobody to log in as: don't block the app.
  const nobody = !!directory && directory.profiles.length === 0;
  const show = open && !(login && nobody);

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (show && !d.open) d.showModal();
    else if (!show && d.open) d.close();
  }, [show]);

  // Fresh occupancy and locations while it is open.
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const load = () => {
      api
        .users()
        .then((d) => alive && setDirectory(d))
        .catch(() => undefined);
      api
        .buildings()
        .then((b) => alive && setBuildings(b))
        .catch(() => undefined);
    };
    load();
    const timer = window.setInterval(load, 4000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [open]);

  const buildingName = (id: string | null) =>
    id ? (buildings.find((b) => b.id === id)?.name ?? id) : null;

  async function choose(id: string) {
    if (id === me) return onClose();
    setBusy(id);
    const session = await person.choose(id);
    setBusy(null);
    if (session) onChosen(session);
  }

  const profiles = directory?.profiles ?? [];

  return (
    <dialog
      ref={dialog}
      className="dialog account-dialog"
      aria-labelledby="account-title"
      // The login can't be escaped; switching can.
      onCancel={(e) => {
        e.preventDefault();
        if (!login) onClose();
      }}
      onClick={(e) => !login && e.target === dialog.current && onClose()}
    >
      <div className="dialog__body">
        <header className="dialog__head">
          <div>
            <span className="eyebrow">AriNav</span>
            <h2 id="account-title">{login ? 'Who are you?' : 'Switch person'}</h2>
          </div>
          {!login && (
            <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
              <CloseIcon />
            </button>
          )}
        </header>
        <p className="muted">
          Choose your account. No password needed; everyone can see and navigate everywhere.
        </p>
        {directory && profiles.length === 0 && <p className="muted">No people are configured.</p>}
        <div className="accounts" role="list">
          {profiles.map((p) => {
            const mine = p.id === me;
            const taken = p.occupied && !mine;
            const where = buildingName(p.building_id);
            return (
              <button
                key={p.id}
                type="button"
                role="listitem"
                className="accounts__tile"
                aria-pressed={mine}
                disabled={taken || busy !== null}
                onClick={() => void choose(p.id)}
              >
                <span className="accounts__avatar" style={{ background: p.color }} aria-hidden>
                  {p.initials}
                </span>
                <span className="accounts__name">{p.name}</span>
                <span className="accounts__meta">
                  {mine
                    ? 'You'
                    : taken
                      ? 'In use on another device'
                      : busy === p.id
                        ? 'Signing in…'
                        : where
                          ? `Last in ${where}`
                          : 'Available'}
                </span>
              </button>
            );
          })}
        </div>
        {person.error && <p className="people__error">{person.error}</p>}
        {!login && me && (
          <footer className="dialog__foot">
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => {
                person.release();
                onClose();
              }}
            >
              Log out
            </button>
          </footer>
        )}
      </div>
    </dialog>
  );
}
