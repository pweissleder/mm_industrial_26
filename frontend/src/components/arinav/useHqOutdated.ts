import { useEffect, useState } from 'react';
import { datasetFileUrl, type LocationRecord } from '../../lib/api';

async function sha256Hex(bytes: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Whether the HQ model was built from an older building.json than the dataset has now, e.g.
 * after a saved layout edit. The pipeline records the SHA-256 of the file it built from; this
 * compares it with the current file. `revision` re-checks after a save.
 */
export function useHqOutdated(
  dataset: string | null,
  location: LocationRecord | undefined,
  revision: number,
): boolean {
  const [outdated, setOutdated] = useState(false);
  const builtFrom = location?.raw_sha256;
  useEffect(() => {
    setOutdated(false);
    // crypto.subtle needs a secure context (https or localhost); without it, no badge.
    if (!dataset || !builtFrom || !globalThis.crypto?.subtle) return;
    let live = true;
    fetch(datasetFileUrl(dataset, 'building.json'))
      .then((r) => (r.ok ? r.arrayBuffer() : Promise.reject(new Error(String(r.status)))))
      .then(sha256Hex)
      .then((current) => live && setOutdated(current !== builtFrom))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [dataset, builtFrom, revision]);
  return outdated;
}
