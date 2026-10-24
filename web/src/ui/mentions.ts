// @mentions in the web chat: the same rules as cadswarm/mentions.py, so a phone and FreeCAD send the
// same thing. Stdlib-free and DOM-free: unit-tested.
const MENTION = /(?<![\w@])@([A-Za-z0-9_-]+)/g;
const SPECIAL_ALL = new Set(['all', 'helpers', 'everyone']);

export interface ParsedMentions {
  helpers: string[];
  special: Set<'team' | 'all'>;
  unknown: string[];
}

/** Helpers named with @, {"team", "all"} asked for, and other @names, in the order they appear. */
export function parseMentions(text: string, helperNames: string[]): ParsedMentions {
  const byLower = new Map(helperNames.map(n => [n.toLowerCase(), n]));
  const helpers: string[] = [];
  const special = new Set<'team' | 'all'>();
  const unknown: string[] = [];
  for (const m of text.matchAll(MENTION)) {
    const word = m[1].toLowerCase();
    const name = byLower.get(word);
    if (name) {
      if (!helpers.includes(name)) helpers.push(name);
    } else if (word === 'team') {
      special.add('team');
    } else if (SPECIAL_ALL.has(word)) {
      special.add('all');
    } else {
      unknown.push(m[1]);
    }
  }
  return { helpers, special, unknown };
}

/** The request without its @names: what goes in the task's title. */
export function stripMentions(text: string): string {
  return text.replace(MENTION, '').replace(/\s{2,}/g, ' ').trim().replace(/^[,:\s]+|[,:\s]+$/g, '');
}

/** The partial name after an @ right before the cursor ("" right after the @), or null. */
export function mentionPrefix(beforeCursor: string): string | null {
  const m = /(?:^|\s)@([A-Za-z0-9_-]*)$/.exec(beforeCursor);
  return m ? m[1] : null;
}

/** What the @ list offers: every helper, then the two team-wide targets. */
export function completions(helperNames: string[]): string[] {
  return [...helperNames, 'team', 'all'];
}

/** Filter completion choices by what was typed after @. */
export function filterCompletions(prefix: string, choices: string[]): string[] {
  const p = prefix.toLowerCase();
  return choices.filter(c => c.toLowerCase().startsWith(p));
}

/** Replace the partial @name before the cursor with @name, keeping everything after it. */
export function insertMention(text: string, cursor: number, name: string): { text: string; cursor: number } {
  const before = text.slice(0, cursor).replace(/@[A-Za-z0-9_-]*$/, `@${name} `);
  return { text: before + text.slice(cursor), cursor: before.length };
}
