// Start profiles and switchable modules (night task §3). One app, no copies:
//   core       — checked search/routes, text/voice assistant, manual location; new extras off
//   immersive  — core + route preview/guidance, POV, game controls, phone orientation
//   showcase   — immersive + opt-in demo memory/recap (prepared HQ buildings are data, not code)
// The camera-localisation experiment is not part of any profile.
// Choose with ?profile=core|immersive|showcase (remembered), single overrides with
// ?features=pov,-memory, or PUBLIC_ARINAV_PROFILE at build/dev start. Default: immersive.

export type Profile = 'core' | 'immersive' | 'showcase';
export type FeatureKey = 'guidance' | 'pov' | 'game' | 'orientation' | 'memory';
export type Features = Record<FeatureKey, boolean>;

export const PROFILES: Record<Profile, Features> = {
  core: { guidance: false, pov: false, game: false, orientation: false, memory: false },
  immersive: { guidance: true, pov: true, game: true, orientation: true, memory: false },
  showcase: { guidance: true, pov: true, game: true, orientation: true, memory: true },
};

const KEY = 'arinav:profile';
const isProfile = (v: unknown): v is Profile => v === 'core' || v === 'immersive' || v === 'showcase';

export function resolveFeatures(
  search: string,
  stored: string | null,
  envProfile: string | undefined,
): { profile: Profile; features: Features } {
  const params = new URLSearchParams(search);
  const asked = params.get('profile');
  const profile: Profile = isProfile(asked)
    ? asked
    : isProfile(stored)
      ? stored
      : isProfile(envProfile)
        ? envProfile
        : 'immersive';
  const features = { ...PROFILES[profile] };
  for (const raw of (params.get('features') ?? '').split(',')) {
    const name = raw.trim().replace(/^[-+]/, '') as FeatureKey;
    if (name in features) features[name] = !raw.trim().startsWith('-');
  }
  // POV, game controls and orientation build on guidance (the walker); keep them consistent.
  if (!features.guidance) Object.assign(features, { pov: false, game: false, orientation: false });
  return { profile, features };
}

export function currentFeatures(): { profile: Profile; features: Features } {
  if (typeof window === 'undefined') return { profile: 'immersive', features: PROFILES.immersive };
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(KEY);
  } catch {
    // Private mode: profiles still work via the URL.
  }
  const resolved = resolveFeatures(window.location.search, stored, import.meta.env.PUBLIC_ARINAV_PROFILE);
  const asked = new URLSearchParams(window.location.search).get('profile');
  if (isProfile(asked)) {
    try {
      localStorage.setItem(KEY, asked);
    } catch {
      // Not remembered; fine.
    }
  }
  return resolved;
}
