export function matchesRequiredBlocker(blockerText: string, ...terms: Array<string | undefined>): boolean {
  const normalizedBlocker = blockerText.trim().toLowerCase();
  if (!normalizedBlocker) return false;

  return terms
    .map(term => term?.trim().toLowerCase())
    .filter((term): term is string => Boolean(term))
    .some(term => normalizedBlocker.includes(term));
}