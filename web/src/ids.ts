const LEN: Record<string, number> = { o_: 12, t_: 8, m_: 12, b_: 8, r_: 8, h_: 8 };
export function newId(prefix: keyof typeof LEN | string): string {
  const n = LEN[prefix] ?? 12;
  const bytes = crypto.getRandomValues(new Uint8Array(Math.ceil(n / 2)));
  return prefix + Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('').slice(0, n);
}
