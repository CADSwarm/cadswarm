export function memberHue(id: number): number { return (id * 137.508) % 360; }
// Comma-separated hsl(): valid CSS, and the only hsl form THREE.Color parses.
export function memberColor(id: number, dark: boolean): string {
  return `hsl(${memberHue(id).toFixed(1)}, 70%, ${dark ? 65 : 55}%)`;
}
