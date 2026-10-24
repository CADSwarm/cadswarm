export interface Ticket { v: number; uri: string; db: string; token: string; kind: string; role: string; host_addr: string }

const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';

export function base32Decode(s: string): Uint8Array {
  const clean = s.replace(/\s+/g, '').replace(/=+$/, '').toUpperCase();
  const out: number[] = [];
  let bits = 0, value = 0;
  for (const ch of clean) {
    const i = ALPHABET.indexOf(ch);
    if (i < 0) throw new Error('This ticket is not valid (bad character).');
    value = (value << 5) | i;
    bits += 5;
    if (bits >= 8) { out.push((value >>> (bits - 8)) & 255); bits -= 8; }
  }
  return new Uint8Array(out);
}

export function decodeTicket(s: string): Ticket {
  try {
    const t = JSON.parse(new TextDecoder().decode(base32Decode(s)));
    if (!t.uri || !t.db || !t.token) throw new Error('missing fields');
    return { host_addr: '', ...t };
  } catch (e) {
    throw new Error(`This ticket could not be read: ${(e as Error).message}`);
  }
}
