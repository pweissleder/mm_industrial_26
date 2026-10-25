// Routes to a person follow them. The route keeps its target as the person ({kind: 'user', id});
// when they move far enough, change floor, or their place changes otherwise, the route is
// recomputed from where you are now: while walking via guidance (it pauses for a moment and goes
// on), else the shown route is replaced. At most one recomputation runs, at most one per
// FOLLOW_MIN_MS; answers for an older state are dropped.
//
// Offline people don't move: the route keeps going to their last known place. If the person
// leaves the building or clears their place, following stops with a message and the route stays.
import { useEffect, useRef, useState } from 'react';
import {
  api,
  ApiError,
  type RouteRequest,
  type RouteResponse,
  type UserPresence,
} from '../../lib/api';
import { routeTargetOf, type Guidance } from './useGuidance';

/** Reroute once the person is this far from where the route ends (m). */
export const FOLLOW_MOVE_M = 1;
/** At most one recomputation per this many ms (per follower). */
const FOLLOW_MIN_MS = 1000;

export interface Following {
  /** The person the route goes to (null: the route is not to a person). */
  name: string | null;
  status: 'following' | 'updating' | 'offline' | 'stopped' | null;
  /** Why following stopped. */
  message: string | null;
}

const NOT_FOLLOWING: Following = { name: null, status: null, message: null };

/** Does a route ending at `end` still reach a person now at `at`? (Same floor, within
    FOLLOW_MOVE_M.) A missing end always needs a new route. */
export function personMoved(
  end: { floor_id: string; position: [number, number] } | null | undefined,
  at: { floor_id: string; position: [number, number] },
): boolean {
  return (
    !end ||
    end.floor_id !== at.floor_id ||
    Math.hypot(end.position[0] - at.position[0], end.position[1] - at.position[1]) >= FOLLOW_MOVE_M
  );
}

function startOf(route: RouteResponse): RouteRequest['start'] | null {
  const s = route.start;
  if (!s) return null;
  if (s.kind === 'point' || !s.id)
    return { kind: 'point', space_id: s.space_id, position: s.position };
  return { kind: s.kind, id: s.id } as RouteRequest['start'];
}

export function useFollowPerson(args: {
  buildingId: string | null;
  /** The route shown in the app (the planned one, before and after walking). */
  route: RouteResponse | null;
  guidance: Guidance;
  here: UserPresence[];
  showRoute: (route: RouteResponse) => void;
}): Following {
  const { buildingId, route, guidance, here, showRoute } = args;
  // While walking, guidance's own copy is the route that matters (it is rerouted along the way).
  const followed = guidance.navigating ? guidance.snapshot.route : route;
  const person = followed?.target_person ?? null;
  const live = person ? here.find((u) => u.profile.id === person.id) : undefined;
  const [state, setRaw] = useState<Following>(NOT_FOLLOWING);
  // Runs on every guidance snapshot: only a real change may re-render.
  const setState = (next: Following) =>
    setRaw((prev) =>
      prev.name === next.name && prev.status === next.status && prev.message === next.message
        ? prev
        : next,
    );
  const busy = useRef(false);
  const lastAt = useRef(0);
  const token = useRef(0);
  const stopped = useRef<string | null>(null); // the person following stopped for
  const [tick, setTick] = useState(0); // re-check after the cooldown

  useEffect(() => {
    if (!followed || !person || !buildingId) {
      setState(NOT_FOLLOWING);
      return;
    }
    if (stopped.current === person.id) return;
    if (guidance.snapshot.status === 'arrived') {
      setState({ name: person.name, status: null, message: null });
      return;
    }
    // Not in the building any more (left, or cleared their place): stop, keep the route.
    if (!live || !live.position || !live.available) {
      stopped.current = person.id;
      setState({
        name: person.name,
        status: 'stopped',
        message: `${person.name} is no longer here; the route goes to where they were.`,
      });
      return;
    }
    const at = live.position;
    const end = followed.target;
    const moved = personMoved(end, at);
    const status = live.status === 'offline' ? 'offline' : 'following';
    if (!moved) {
      if (!busy.current) setState({ name: person.name, status, message: null });
      return;
    }
    if (busy.current) return;
    const wait = lastAt.current + FOLLOW_MIN_MS - performance.now();
    if (wait > 0) {
      const timer = window.setTimeout(() => setTick((n) => n + 1), wait);
      return () => window.clearTimeout(timer);
    }
    lastAt.current = performance.now();
    setState({ name: person.name, status: 'updating', message: null });
    const floorChange = end?.floor_id !== at.floor_id;
    const message = `${person.name} ${floorChange ? 'changed floor' : 'moved'} · updating your route…`;
    // Walking: guidance recomputes from the walker and carries on; it reports the new route.
    // (While it is already recomputing, the next check after the cooldown tries again.)
    if (guidance.navigating) {
      guidance.retarget(message);
      return;
    }
    // Only planned: recompute from the route's start.
    const start = startOf(followed);
    const target = routeTargetOf(followed);
    if (!start || !target) return;
    const c = followed.constraints;
    const request: RouteRequest = {
      mode: followed.mode,
      start,
      target,
      step_free: !!c?.step_free,
      ...(followed.mode === 'precision' && {
        profile: c?.profile,
        width_m: c?.width_m,
        safety_m: c?.safety_m,
        allow_schematic_vertical: true,
      }),
    };
    const mine = ++token.current;
    busy.current = true;
    api
      .navRoute(buildingId, request)
      .then((next) => {
        if (mine === token.current && next.status === 'ok') showRoute(next);
      })
      .catch((e: unknown) => {
        if (mine !== token.current) return;
        stopped.current = person.id;
        setState({
          name: person.name,
          status: 'stopped',
          message: e instanceof ApiError ? e.message : `${person.name} can't be reached right now.`,
        });
      })
      .finally(() => {
        busy.current = false;
        setTick((n) => n + 1);
      });
  }, [followed, person, live, buildingId, guidance, showRoute, tick]);

  // A route to someone else (or a new route to them) starts following afresh.
  const routeTo = route?.target_person?.id ?? null;
  useEffect(() => {
    stopped.current = null;
    token.current += 1;
  }, [routeTo, route]);

  return state;
}
