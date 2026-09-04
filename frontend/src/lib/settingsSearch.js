const DIACRITICS = /[\u0300-\u036f]/g

export function normalizeSettingsSearch(value) {
  return String(value ?? '')
    .normalize('NFD')
    .replace(DIACRITICS, '')
    .toLocaleLowerCase()
    .trim()
}

export function filterSettingsSections(query, sections) {
  const terms = normalizeSettingsSearch(query).split(/\s+/).filter(Boolean)
  if (!terms.length) return sections

  return sections.filter((section) => {
    const haystack = normalizeSettingsSearch([
      section.label,
      section.description,
      ...(section.keywords || []),
    ].join(' '))
    return terms.every((term) => haystack.includes(term))
  })
}
