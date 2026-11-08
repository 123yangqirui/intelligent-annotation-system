export function clampBox(
  x: number,
  y: number,
  w: number,
  h: number,
  imageWidth: number,
  imageHeight: number,
): number[] {
  const nx = Math.max(0, Math.min(imageWidth - 1, x));
  const ny = Math.max(0, Math.min(imageHeight - 1, y));
  return [
    nx,
    ny,
    nx + Math.max(1, Math.min(imageWidth - nx, w)),
    ny + Math.max(1, Math.min(imageHeight - ny, h)),
  ];
}
