export interface BoardOption { id: string; label: string }

/** `boards/boards.json` is `{ "<id>": { "label": ..., ... } }` (plan 00). Anything else gives an empty list. */
export function parseBoards(json: unknown): BoardOption[] {
  if (!json || typeof json !== 'object' || Array.isArray(json)) return [];
  return Object.entries(json as Record<string, unknown>)
    .filter(([id, b]) => id && b && typeof b === 'object')
    .map(([id, b]) => {
      const label = (b as { label?: unknown }).label;
      return { id, label: typeof label === 'string' && label.trim() ? label : id };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
}

/** The board list is copied into the build when it exists; missing or broken means "type it". */
export async function loadBoards(fetchFn: typeof fetch = fetch): Promise<BoardOption[]> {
  try {
    const r = await fetchFn('./boards.json', { cache: 'no-cache' });
    return r.ok ? parseBoards(await r.json()) : [];
  } catch { return []; }
}
