import { animate } from 'motion/react';
import { useEffect, useState } from 'react';

const QUERY = '(prefers-reduced-motion: reduce)';

/** Live `prefers-reduced-motion` (motion's own hook reads it once per page load). */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== 'undefined' && !!window.matchMedia?.(QUERY).matches,
  );
  useEffect(() => {
    const media = window.matchMedia?.(QUERY);
    if (!media) return;
    const update = () => setReduced(media.matches);
    update();
    media.addEventListener?.('change', update);
    return () => media.removeEventListener?.('change', update);
  }, []);
  return reduced;
}

interface Props {
  value: number;
  decimals?: number;
  /** Seconds; the count starts after `delay`. */
  duration?: number;
  delay?: number;
}

const format = (n: number, decimals: number) =>
  n.toLocaleString('en', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });

/** A number that counts up from 0 when it mounts; shows the final value at once with reduced motion. */
export default function CountUp({ value, decimals = 0, duration = 1.4, delay = 0.2 }: Props) {
  const reduced = usePrefersReducedMotion();
  const [shown, setShown] = useState(reduced ? value : 0);

  useEffect(() => {
    if (reduced) {
      setShown(value);
      return;
    }
    const controls = animate(0, value, {
      duration,
      delay,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: setShown,
    });
    return () => controls.stop();
  }, [value, duration, delay, reduced]);

  return (
    <span className="wrapped__count" aria-label={format(value, decimals)}>
      <span aria-hidden="true">{format(shown, decimals)}</span>
    </span>
  );
}
