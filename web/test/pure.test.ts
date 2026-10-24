import { describe, expect, it } from 'vitest';
import { decodeTicket } from '../src/ticket';
import { memberColor } from '../src/color';
import { newId } from '../src/ids';

// Produced by the Rust host / Python ticket.encode for this payload:
const payload = { v: 1, uri: 'http://127.0.0.1:3000', db: 'cadswarm', token: 'ab'.repeat(32), kind: 'human', role: 'member', host_addr: '' };

function b32(bytes: Uint8Array): string {
  const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = 0, value = 0, out = '';
  for (const b of bytes) { value = (value << 8) | b; bits += 8; while (bits >= 5) { out += A[(value >>> (bits - 5)) & 31]; bits -= 5; } }
  if (bits > 0) out += A[(value << (5 - bits)) & 31];
  return out;
}

describe('tickets', () => {
  it('decodes base32 JSON case-insensitively', () => {
    const s = b32(new TextEncoder().encode(JSON.stringify(payload)));
    expect(decodeTicket(s)).toEqual(payload);
    expect(decodeTicket(s.toLowerCase())).toEqual(payload);
  });
  it('rejects garbage with a readable error', () => {
    expect(() => decodeTicket('!!!')).toThrow(/ticket/i);
  });
});

describe('colours and ids', () => {
  it('gives each member a stable, distinct colour', () => {
    expect(memberColor(1, false)).toBe(memberColor(1, false));
    expect(memberColor(1, false)).not.toBe(memberColor(2, false));
    expect(memberColor(3, true)).toMatch(/^hsl\(/);
  });
  it('makes ids with the contract prefixes and lengths', () => {
    expect(newId('o_')).toMatch(/^o_[0-9a-f]{12}$/);
    expect(newId('b_')).toMatch(/^b_[0-9a-f]{8}$/);
  });
});
