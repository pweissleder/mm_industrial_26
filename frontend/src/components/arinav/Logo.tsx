export function LogoMark({ size = 30 }: { size?: number }) {
  return (
    <img
      src="/android-chrome-192x192.png"
      width={size}
      height={size}
      alt=""
      aria-hidden="true"
      className="logo__mark"
    />
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
