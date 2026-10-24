/** Escape text for innerHTML. Everything a member, agent or ticket supplies goes through this. */
export const esc = (s: string) =>
  String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
