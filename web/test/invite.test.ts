import { describe, expect, it } from 'vitest';
import { reachableUri, readInvite } from '../src/ticket';

// Made by cadswarm/invite.py make_invite and cadswarm/ticket.py encode: the codes FreeCAD and the host hand out.
const CODE = 'cadswarm-2:eyJ2IjoxLCJ1cmkiOiJodHRwOi8vMTI3LjAuMC4xOjMwMDAiLCJkYiI6ImNhZHN3YXJtLXRlc3QtNmM5MCIsInRva2VuIjoiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYmFiYWJhYiIsImtpbmQiOiJodW1hbiIsInJvbGUiOiJtZW1iZXIiLCJob3N0X2FkZHIiOiIiLCJwIjoiUGhvbmUgc3RhbmQifQ';
const B32 = 'PMRHMIR2GEWCE5LSNERDUITIOR2HAORPF4YTENZOGAXDALRRHIZTAMBQEIWCEZDCEI5CEY3BMRZXOYLSNUWXIZLTOQWTMYZZGARCYITUN5VWK3RCHIRGCYTBMJQWEYLCMFRGCYTBMJQWEYLCMFRGCYTBMJQWEYLCMFRGCYTBMJQWEYLCMFRGCYTBMJQWEYLCMFRGCYTBMJQWEYLCMFRGCYTBMIRCYITLNFXGIIR2EJUHK3LBNYRCYITSN5WGKIR2EJWWK3LCMVZCELBCNBXXG5C7MFSGI4RCHIRCE7I';
const TOKEN = 'ab'.repeat(32);

describe('pasting an invite code', () => {
  it("reads FreeCAD's cadswarm-2 code, with the project name", () => {
    expect(readInvite(CODE)).toMatchObject({ uri: 'http://127.0.0.1:3000', db: 'cadswarm-test-6c90', token: TOKEN,
      kind: 'human', role: 'member', project: 'Phone stand' });
  });

  it('forgives what chat apps and copying do to it', () => {
    const wrapped = `Here you go: “${CODE.slice(0, 60)}\n${CODE.slice(60)}” thanks!`;
    expect(readInvite(wrapped).token).toBe(TOKEN);
    expect(readInvite('​' + CODE + '​').db).toBe('cadswarm-test-6c90');
  });

  it("reads the host's bare ticket and phone links too", () => {
    expect(readInvite(B32)).toMatchObject({ db: 'cadswarm-test-6c90', token: TOKEN, project: '' });
    expect(readInvite(`https://example.org/cadswarm/#t=${B32}`).token).toBe(TOKEN);
  });

  it('explains what is wrong in plain words', () => {
    expect(() => readInvite('')).toThrow(/Paste the invite code/);
    expect(() => readInvite('cadswarm-1:abc')).toThrow(/older CADSwarm/);
    expect(() => readInvite('hello there')).toThrow(/doesn't look like a CADSwarm invite/);
    expect(() => readInvite(CODE.slice(0, 40))).toThrow(/incomplete/);
  });
});

describe('reaching a local team from another device', () => {
  it('uses the address that served the page instead of 127.0.0.1', () => {
    expect(reachableUri('http://127.0.0.1:3000', '10.0.9.22')).toBe('http://10.0.9.22:3000');
    expect(reachableUri('http://localhost:3000', '192.168.1.5')).toBe('http://192.168.1.5:3000');
  });
  it('leaves real addresses and same-computer use alone', () => {
    expect(reachableUri('https://maincloud.spacetimedb.com', '10.0.9.22')).toBe('https://maincloud.spacetimedb.com');
    expect(reachableUri('http://127.0.0.1:3000', 'localhost')).toBe('http://127.0.0.1:3000');
    expect(reachableUri('http://127.0.0.1:3000', '')).toBe('http://127.0.0.1:3000');
  });
});
