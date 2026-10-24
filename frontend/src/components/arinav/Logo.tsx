// Ariadne's ball of thread, unwinding into a route that ends in a location dot.
export function LogoMark({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" className="logo__mark">
      <circle cx="13" cy="13" r="10.5" fill="#002478" />
      <g fill="none" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" opacity="0.92">
        <path d="M4.6 9.6c4.6-3.4 11.8-3.6 16.6.4" />
        <path d="M3 14.2c5.6-3.8 13.6-3.4 19.4 1" />
        <path d="M5.4 19.6c4.6-2.6 10.2-2.6 14.8.2" />
        <path d="M10.4 3.2c-3 5.4-2.6 13.4 1.8 19.6" opacity="0.55" />
      </g>
      <path
        d="M20.6 20.2c3 1.6 1.2 5.4 4.2 6.4 1.2.4 2.2 0 2.8-.4"
        fill="none"
        stroke="#fb7928"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <circle cx="28.2" cy="25.6" r="2.4" fill="#fb7928" />
    </svg>
  );
}

export default function Logo() {
  return (
    <span className="logo">
      <LogoMark />
      <span className="logo__word">
        Ari<span className="logo__nav">Nav</span>
      </span>
    </span>
  );
}
