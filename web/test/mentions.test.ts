import { describe, expect, it } from 'vitest';
import {
  completions, filterCompletions, insertMention, mentionPrefix, parseMentions, stripMentions,
} from '../src/ui/mentions';

const HELPERS = ['planner', 'worker-one', 'worker2'];

describe('@ mentions', () => {
  it('finds helpers in the order they appear, case-insensitively', () => {
    const r = parseMentions('@Worker-One build the lid, then @planner check it', HELPERS);
    expect(r.helpers).toEqual(['worker-one', 'planner']);
  });

  it('finds @team and every spelling of @all', () => {
    expect([...parseMentions('@team make a stand', HELPERS).special]).toEqual(['team']);
    for (const word of ['all', 'helpers', 'everyone']) {
      expect([...parseMentions(`@${word} pause`, HELPERS).special]).toEqual(['all']);
    }
  });

  it('keeps names it does not know, and ignores emails', () => {
    const r = parseMentions('mail sam@example.com, @nobody look', HELPERS);
    expect(r.helpers).toEqual([]);
    expect(r.unknown).toEqual(['nobody']);
  });

  it('strips mentions and their punctuation into a task title', () => {
    expect(stripMentions('@team  make   a lid ')).toBe('make a lid');
    expect(stripMentions('@worker2: make the cutout higher')).toBe('make the cutout higher');
    expect(stripMentions('@planner')).toBe('');
  });

  it('detects the partial name only right after @ at a word start', () => {
    expect(mentionPrefix('make it @wo')).toBe('wo');
    expect(mentionPrefix('@')).toBe('');
    expect(mentionPrefix('mail a@b')).toBeNull();
    expect(mentionPrefix('@worker2 make it')).toBeNull();
  });

  it('offers helpers then team and all, filtered by prefix', () => {
    expect(completions(HELPERS)).toEqual(['planner', 'worker-one', 'worker2', 'team', 'all']);
    expect(filterCompletions('wor', completions(HELPERS))).toEqual(['worker-one', 'worker2']);
    expect(filterCompletions('', completions(HELPERS))).toEqual(completions(HELPERS));
  });

  it('inserts a mention in the middle without losing what follows', () => {
    expect(insertMention('make @wo now', 8, 'worker2')).toEqual({ text: 'make @worker2  now', cursor: 14 });
    // the cursor is just after "@wo" (positions: m0 a1 k2 e3 space4 @5 w6 o7)
    expect(insertMention('make @wo now', 8, 'worker2').text).toContain('@worker2');
  });
});
