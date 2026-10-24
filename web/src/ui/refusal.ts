// A change the team refused because someone else holds the part. The chat and Activity feed
// show these as a card ("Refused · held by lead-claude") rather than an ordinary line, so the
// person can see who to talk to and that nothing changed.

const HOLDER_PATTERNS = [
  /current holders?:\s*([^,.\n]+)/i,
  /held by\s+([^,.\n]+)/i,
  /already locked:\s*[^,\n]*?\bby\s+([^,.\n]+)/i,
  /locked by\s+([^,.\n]+)/i,
];

export interface Refusal {
  title: string;
  detail: string;
  holder: string;
}

/** True when text is a lock refusal rather than an ordinary chat line or status. */
export function isRefusal(text: string): boolean {
  return /refus|not_locked|lock_violation|already locked|current holders/i.test(text)
    // "held by lead-claude" is the phrase; "a lid held by four inserts" is design talk.
    || /held by\s+[\w.-]+\s*(?:\([^)]*\)|$)/i.test(text.trim());
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** "Base by ann (active)" and "ann (active)" both name ann; the status is not part of the name. */
function holderName(raw: string): string {
  return raw.trim().replace(/\s*\([^)]*\)\s*$/, '').replace(/^['"]|['"]$/g, '').trim();
}

/**
 * Drop the lock statement itself ("Refused · held by ann", "Already locked: Base by ann (active)."),
 * since the card's title already says it, and keep whatever explanation follows.
 */
function explanation(text: string, holder: string): string {
  let out = text.replace(/^(?:refused|locked|already locked|not_locked|lock_violation)\b\s*[:·—-]?\s*/i, '');
  if (holder) {
    const h = escapeRe(holder);
    out = out.replace(new RegExp(`^(?:[^\\n]*?\\bby\\s+)?${h}\\b\\s*(?:\\([^)]*\\))?\\s*[.·:,—-]?\\s*`, 'i'), '');
    out = out.replace(new RegExp(`\\b(?:current holders?|holders?)\\s*:\\s*${h}(?:\\s*\\([^)]*\\))?\\s*[.·:,—-]?\\s*`, 'i'), '');
  }
  return out.replace(/^message the holder\b\s*[.·:,—-]?\s*/i, '').replace(/\s{2,}/g, ' ').trim();
}

export function parseRefusal(text: string): Refusal {
  const body = (text || '').trim();
  let holder = '';
  for (const re of HOLDER_PATTERNS) {
    const m = re.exec(body);
    if (m?.[1]) { holder = holderName(m[1]); break; }
  }
  const lines = body.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  let detail = explanation(lines.slice(1).join(' ') || lines[0] || '', holder);
  if (!/nothing changed/i.test(detail)) detail = `${detail ? `${detail} ` : ''}Nothing changed. Ask them instead.`.trim();
  return { title: holder ? `Refused · held by ${holder}` : 'Refused', detail, holder };
}
