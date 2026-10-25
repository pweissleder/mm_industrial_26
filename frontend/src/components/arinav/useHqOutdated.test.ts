import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { LocationRecord } from '../../lib/api';
import { useHqOutdated } from './useHqOutdated';

const FILE = '{"building":{"id":"B"}}';
// The hash of some earlier version of the file.
const OLDER_SHA = 'a'.repeat(64);

const location = (raw_sha256: string) => ({ raw_sha256 }) as LocationRecord;

async function sha(text: string) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

describe('useHqOutdated', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('is outdated when building.json no longer matches what the HQ model was built from', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(FILE)));
    const { result } = renderHook(() => useHqOutdated('ds', location(OLDER_SHA), 0));
    await waitFor(() => expect(result.current).toBe(true));
  });

  it('is current when the hashes match', async () => {
    const fetch = vi.fn(async () => new Response(FILE));
    vi.stubGlobal('fetch', fetch);
    const current = location(await sha(FILE));
    const { result } = renderHook(() => useHqOutdated('ds', current, 0));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(result.current).toBe(false);
  });

  it('does not check without a recorded hash', () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const { result } = renderHook(() => useHqOutdated('ds', undefined, 0));
    expect(result.current).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });
});
