export function memberHue(id: number): number { return (id * 137.508) % 360; }
export function memberColor(id: number, dark: boolean): string {
  return `hsl(${memberHue(id).toFixed(1)} 70% ${dark ? 65 : 55}%)`;
}
