import { describe, expect, it } from 'vitest';
import { FOLLOW_MOVE_M, personMoved } from './useFollowPerson';

const end = { floor_id: 'F0', position: [10, 5] as [number, number] };

describe('personMoved', () => {
  it('keeps the route while the person stays within the threshold', () => {
    expect(personMoved(end, { floor_id: 'F0', position: [10.5, 5.3] })).toBe(false);
  });

  it('reroutes once they moved far enough', () => {
    expect(personMoved(end, { floor_id: 'F0', position: [10 + FOLLOW_MOVE_M, 5] })).toBe(true);
  });

  it('reroutes on another floor, even at the same XY', () => {
    expect(personMoved(end, { floor_id: 'F1', position: [10, 5] })).toBe(true);
  });

  it('reroutes when the route has no end', () => {
    expect(personMoved(null, { floor_id: 'F0', position: [10, 5] })).toBe(true);
  });
});
