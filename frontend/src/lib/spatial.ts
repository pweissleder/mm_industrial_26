// One documented place for frames and directions (night task §5 "three separate quantities").
//
// Building frame (data, routing, costs): metres, X right, Y "north" up the plan, floors by
// elevation_m. Headings are degrees counter-clockwise from +X (0 = +X, 90 = +Y), like the backend
// waypoints' heading_deg.
// Render frame (three.js): x = X, y = elevation (+ visual floor explode offset), z = -Y.
// A yaw about the render y axis by +h turns +x towards -z, i.e. towards building +Y, so the
// render yaw of a building heading h is simply h (in radians).
// Device frame (DeviceOrientationEvent.alpha): degrees about the screen normal, increasing
// counter-clockwise seen from above; not a building heading until calibrated to a known view.
//
// Rendering, floor explode and camera moves never change building coordinates or costs.
import type { Point } from './building';

export const EYE_HEIGHT_M = 1.65;

/** Normalise to [0, 360). */
export const wrap360 = (deg: number) => ((deg % 360) + 360) % 360;

/** Signed smallest difference to - from, in (-180, 180]. */
export function angleDiff(from: number, to: number): number {
  const d = wrap360(to - from);
  return d > 180 ? d - 360 : d;
}

/** Heading (deg, CCW from +X) of the vector a -> b in the building frame. */
export const headingTo = (a: Point, b: Point) =>
  wrap360((Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI);

/** Unit vector of a building heading. */
export const headingVector = (deg: number): Point => {
  const r = (deg * Math.PI) / 180;
  return [Math.cos(r), Math.sin(r)];
};

export type Side = 'ahead' | 'right' | 'behind' | 'left';

/**
 * Where `target` lies relative to someone at `from` looking along `facingDeg`: four 90° sectors
 * centred on ahead/right/behind/left. Pure geometry; "right" is clockwise of the facing direction.
 */
export function sideOf(from: Point, facingDeg: number, target: Point): { side: Side; relativeDeg: number } {
  const rel = angleDiff(facingDeg, headingTo(from, target)); // + = counter-clockwise = left
  const a = Math.abs(rel);
  const side: Side = a <= 45 ? 'ahead' : a >= 135 ? 'behind' : rel > 0 ? 'left' : 'right';
  return { side, relativeDeg: rel };
}

/** Rotation (deg, clockwise on screen) of an "ahead" arrow that points from the view to a target. */
export const arrowRotation = (facingDeg: number, targetHeadingDeg: number) =>
  -angleDiff(facingDeg, targetHeadingDeg);

/**
 * Device orientation -> building view heading, after calibration: at calibration time the user
 * faces `calibratedHeading` (e.g. the current route direction) while the device reports alpha0.
 * Later headings follow the device's change of alpha (counter-clockwise like building headings).
 * `screenAngle` (screen.orientation.angle) compensates portrait/landscape.
 */
export function deviceHeading(
  alpha: number,
  calibration: { alpha0: number; heading: number; screenAngle0: number },
  screenAngle = calibration.screenAngle0,
): number {
  const delta = angleDiff(calibration.alpha0, alpha) - angleDiff(calibration.screenAngle0, screenAngle);
  return wrap360(calibration.heading + delta);
}

/** Exponential smoothing of an angle that handles the 359 -> 0 wrap. */
export function smoothAngle(previous: number | null, next: number, factor = 0.25): number {
  if (previous === null || !Number.isFinite(previous)) return wrap360(next);
  return wrap360(previous + angleDiff(previous, next) * factor);
}

/** Render-frame position of a building point on a floor at a visual elevation. */
export const toRender = (p: Point, elevation: number): [number, number, number] => [p[0], elevation, -p[1]];
