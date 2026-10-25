// M03 / S01: headings, wrap-around, relative sides and device calibration (pure software checks;
// not a physical-device test).
import { describe, expect, it } from 'vitest';
import { angleDiff, arrowRotation, deviceHeading, deviceLook, headingTo, sideOf, smoothAngle, wrap360 } from './spatial';

describe('angles', () => {
  it('wraps and takes the short way round 359/0', () => {
    expect(wrap360(-10)).toBe(350);
    expect(wrap360(725)).toBe(5);
    expect(angleDiff(350, 10)).toBe(20);
    expect(angleDiff(10, 350)).toBe(-20);
    expect(angleDiff(0, 180)).toBe(180);
  });

  it('headings follow the building frame (CCW from +X)', () => {
    expect(headingTo([0, 0], [1, 0])).toBe(0);
    expect(headingTo([0, 0], [0, 1])).toBe(90);
    expect(headingTo([0, 0], [-1, 0])).toBe(180);
    expect(headingTo([0, 0], [0, -1])).toBe(270);
  });
});

describe('relative sides at the four headings (S01)', () => {
  const target: [number, number] = [0, 5]; // north of the origin
  const cases: [number, string][] = [
    [90, 'ahead'], // facing north
    [0, 'left'], // facing east: north is on the left
    [180, 'right'], // facing west: north is on the right
    [270, 'behind'], // facing south
  ];
  it.each(cases)('facing %s° the target is %s', (facing, side) => {
    expect(sideOf([0, 0], facing, target).side).toBe(side);
  });
  it('is independent of where the map shows things on screen', () => {
    expect(sideOf([10, 10], 0, [10, 4]).side).toBe('right'); // south while facing east
  });
});

describe('device orientation (M03, software only)', () => {
  const cal = { alpha0: 30, heading: 90, screenAngle0: 0 };
  it('follows the device rotation from the calibrated view, incl. 359/0', () => {
    expect(deviceHeading(30, cal)).toBe(90);
    expect(deviceHeading(120, cal)).toBe(180); // turned 90° counter-clockwise
    expect(deviceHeading(300, cal)).toBe(0); // turned 90° clockwise across 0
    expect(deviceHeading(359, { alpha0: 1, heading: 10, screenAngle0: 0 })).toBe(8);
  });
  it('compensates a portrait -> landscape switch', () => {
    expect(deviceHeading(30, cal, 90)).toBe(0);
  });
  it('smooths jitter without jumping across 359/0', () => {
    const s = smoothAngle(355, 5, 0.5);
    expect(s).toBe(0);
  });
  it('arrow rotation is clockwise on screen when the target is to the right', () => {
    expect(arrowRotation(90, 0)).toBe(90); // facing north, target east -> rotate right
    expect(arrowRotation(90, 180)).toBe(-90);
  });
});

describe('deviceLook (3D phone orientation)', () => {
  const near = (a: number, b: number) => expect(Math.abs(angleDiff(a, b))).toBeLessThan(0.5);
  it('upright portrait looks level; tilting looks up and down through the full range', () => {
    const level = deviceLook(0, 90, 0);
    near(level.pitch, 0);
    near(level.roll, 0);
    near(level.azimuth, 90); // north = 90 deg CCW from east
    near(deviceLook(0, 135, 0).pitch, 45); // top edge back: up
    near(deviceLook(0, 45, 0).pitch, -45); // top edge forward: down
    near(deviceLook(0, 0, 0).pitch, -90); // flat, screen up: the floor
    near(deviceLook(0, 180, 0).pitch, 90); // overhead, screen down: the ceiling
  });
  it('keeps the heading when looking at the floor or ceiling (no gimbal flip)', () => {
    for (const beta of [1, 10, 60, 89, 91, 120, 170, 179]) near(deviceLook(30, beta, 0).azimuth, 120);
  });
  it('turning on the spot changes only the azimuth, across 0/360 without a jump', () => {
    const a = deviceLook(359, 100, 0);
    const b = deviceLook(1, 100, 0);
    expect(Math.abs(angleDiff(a.azimuth, b.azimuth))).toBeCloseTo(2, 3);
    near(a.pitch, b.pitch);
  });
  it('rolls with the phone and is independent of portrait/landscape for the view direction', () => {
    // Upright phone whose screen-up is turned 20 deg to the right / left of the device's top.
    expect(deviceLook(0, 90, 0, 20).roll).toBeCloseTo(20, 1);
    expect(deviceLook(0, 90, 0, -20).roll).toBeCloseTo(-20, 1);
    // Landscape (screen turned 90): same forward direction, the screen's up is the device's +x.
    const land = deviceLook(0, 0, -90, 90);
    near(land.pitch, 0);
    near(land.roll, 0);
  });
});
