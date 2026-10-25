import { describe, expect, it } from 'vitest';
import { PROFILES, resolveFeatures } from './features';

describe('profiles and module switches (A02)', () => {
  it('defaults to immersive; URL beats stored beats env', () => {
    expect(resolveFeatures('', null, undefined).profile).toBe('immersive');
    expect(resolveFeatures('', 'core', 'showcase').profile).toBe('core');
    expect(resolveFeatures('?profile=showcase', 'core', undefined).profile).toBe('showcase');
    expect(resolveFeatures('', null, 'core').profile).toBe('core');
  });

  it('core switches every new module off', () => {
    expect(resolveFeatures('?profile=core', null, undefined).features).toEqual(PROFILES.core);
    expect(Object.values(PROFILES.core).some(Boolean)).toBe(false);
  });

  it('single overrides on top of a profile, consistent dependencies', () => {
    expect(resolveFeatures('?profile=immersive&features=-pov,memory', null, undefined).features).toMatchObject({
      pov: false,
      memory: true,
      guidance: true,
    });
    expect(resolveFeatures('?features=-guidance', null, undefined).features).toMatchObject({
      guidance: false,
      pov: false,
      game: false,
      orientation: false,
    });
  });
});
