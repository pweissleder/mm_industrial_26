// The person this tab acts as: a passwordless lease on one configured profile. A reload resumes
// the tab's own lease (its token is kept in sessionStorage); a new tab starts without a person.
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  api,
  ApiError,
  setSessionToken,
  type UserPresence,
  type UserProfile,
  type UserSession,
} from '../../lib/api';

const KEY = 'arinav:user-session';

interface Stored {
  token: string;
  profileId: string;
}

function load(): Stored | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Stored) : null;
  } catch {
    return null;
  }
}

function save(value: Stored | null) {
  try {
    if (value) sessionStorage.setItem(KEY, JSON.stringify(value));
    else sessionStorage.removeItem(KEY);
  } catch {
    // Storage can be unavailable; the person then lasts until reload.
  }
}

/** Per-person browser storage key: other people's remembered state stays theirs. */
export const personKey = (base: string, personId: string | null) =>
  personId ? `${base}@${personId}` : base;

export interface Person {
  profile: UserProfile | null;
  /** The saved position the person continues from, when they had one. */
  presence: UserPresence | null;
  /** `resuming`: a reload is reclaiming its lease; `ended`: it expired or was taken over. */
  status: 'none' | 'resuming' | 'active' | 'ended';
  error: string | null;
  /** Become `profileId`; the new session (with where they are), or null when refused. */
  choose: (profileId: string) => Promise<UserSession | null>;
  release: () => void;
}

export function usePerson(): Person {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [presence, setPresence] = useState<UserPresence | null>(null);
  const [status, setStatus] = useState<Person['status']>(() => (load() ? 'resuming' : 'none'));
  const [error, setError] = useState<string | null>(null);
  const [leaseSeconds, setLeaseSeconds] = useState<number | null>(null);
  const current = useRef<Stored | null>(null);
  // Bumped by every choose/release, so an older claim's answer cannot become the person.
  const epoch = useRef(0);

  const adopt = useCallback((token: string, who: UserProfile, seconds: number) => {
    current.current = { token, profileId: who.id };
    save(current.current);
    setSessionToken(token);
    setProfile(who);
    setLeaseSeconds(seconds);
    setStatus('active');
    setError(null);
  }, []);

  const end = useCallback((message: string | null) => {
    current.current = null;
    save(null);
    setSessionToken(null);
    setProfile(null);
    setPresence(null);
    setLeaseSeconds(null);
    setStatus(message ? 'ended' : 'none');
    setError(message);
  }, []);

  // A reload: reclaim this tab's own lease with its token.
  useEffect(() => {
    const stored = load();
    if (!stored) return;
    const mine = ++epoch.current;
    setSessionToken(stored.token);
    api
      .claimUser(stored.profileId)
      .then((session) => {
        if (mine !== epoch.current) return;
        adopt(session.token, session.profile, session.lease_seconds);
        setPresence(session.presence);
      })
      .catch((e) => {
        if (mine !== epoch.current) return;
        end(
          e instanceof ApiError && e.status === 409
            ? 'This person is now in use on another device.'
            : null,
        );
      });
  }, [adopt, end]);

  // Keep the lease alive; a lost lease (expired, taken over) ends the person here.
  useEffect(() => {
    if (status !== 'active' || !leaseSeconds) return;
    const timer = window.setInterval(
      () => {
        api.heartbeat().catch((e) => {
          if (!(e instanceof ApiError && e.status === 401)) return;
          // The lease is gone (e.g. the server restarted): take the person back if still free.
          const mine = current.current;
          if (!mine) return;
          api
            .claimUser(mine.profileId)
            .then((session) => {
              if (current.current === mine)
                adopt(session.token, session.profile, session.lease_seconds);
            })
            .catch(() => {
              if (current.current === mine) end('Your session ended. Choose your person again.');
            });
        });
      },
      Math.max(2, leaseSeconds / 3) * 1000,
    );
    return () => window.clearInterval(timer);
  }, [status, leaseSeconds, adopt, end]);

  const choose = useCallback(
    async (profileId: string): Promise<UserSession | null> => {
      const previous = current.current;
      if (previous?.profileId === profileId) return null; // already them
      const mine = ++epoch.current;
      try {
        // Claim first: when the new person is taken, the tab stays who it was. (The old token
        // sent along is ignored for another person.)
        const session = await api.claimUser(profileId);
        if (mine !== epoch.current) {
          void api.releaseUser(session.token);
          return null;
        }
        if (previous) void api.releaseUser(previous.token);
        adopt(session.token, session.profile, session.lease_seconds);
        setPresence(session.presence);
        return session;
      } catch (e) {
        if (mine === epoch.current)
          setError(e instanceof ApiError ? e.message : 'The server is not reachable.');
        return null;
      }
    },
    [adopt],
  );

  const release = useCallback(() => {
    epoch.current++;
    if (current.current) void api.releaseUser(current.current.token);
    end(null);
  }, [end]);

  return { profile, presence, status, error, choose, release };
}
