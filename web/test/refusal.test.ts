import { describe, expect, it } from 'vitest';
import { isRefusal, parseRefusal } from '../src/ui/refusal';

describe('refusal cards', () => {
  it('names the holder from the ways an agent or op error phrases it', () => {
    expect(parseRefusal('Refused · held by lead-claude')).toMatchObject({
      title: 'Refused · held by lead-claude', detail: 'Nothing changed. Ask them instead.', holder: 'lead-claude' });
    expect(parseRefusal('not_locked: Lock Lid before set_props. Current holders: bob')).toMatchObject({
      title: 'Refused · held by bob', holder: 'bob' });
    expect(parseRefusal('Already locked: Base by ann (active). Message the holder')).toMatchObject({
      title: 'Refused · held by ann', holder: 'ann', detail: 'Nothing changed. Ask them instead.' });
  });

  it('keeps the explanation under the card title and never loses "nothing changed"', () => {
    const card = parseRefusal('Refused: held by bob. Your change was rolled back before it touched the model.');
    expect(card.title).toBe('Refused · held by bob');
    expect(card.detail).toContain('Your change was rolled back');
    expect(card.detail).toContain('Nothing changed');
    expect(parseRefusal('Refused\nNothing changed. Ask them instead.').detail).toBe('Nothing changed. Ask them instead.');
  });

  it('leaves ordinary chat alone', () => {
    expect(isRefusal('I thickened the walls to 3 mm.')).toBe(false);
    expect(isRefusal('The lid is held by four M3 inserts')).toBe(false);
    expect(isRefusal('Lid is held by bob')).toBe(true);
    expect(parseRefusal('I thickened the walls to 3 mm.').title).toBe('Refused'); // only called for refusals
  });
});
