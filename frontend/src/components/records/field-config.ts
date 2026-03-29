// ============================================
// Field Display Configuration per source_type
// Generic field renderer config — NOT hardcoded layouts
// ============================================

export type FieldType = 'text' | 'date' | 'address' | 'phone' | 'url' | 'boolean' | 'array' | 'number' | 'badge' | 'json'

export interface FieldConfig {
  key: string
  label: string
  icon?: string
  type?: FieldType
  group?: string
}

export interface SourceTypeConfig {
  label: string
  icon: string
  primaryFields: string[]  // keys to show first, in order
  fieldOverrides: Record<string, Partial<FieldConfig>>
  groups: { key: string; label: string; icon: string }[]
}

/** Known field type detection heuristics */
export function detectFieldType(key: string, value: unknown): FieldType {
  if (value === null || value === undefined) return 'text'
  if (typeof value === 'boolean') return 'boolean'
  if (typeof value === 'number') return 'number'
  if (Array.isArray(value)) return 'array'
  if (typeof value === 'object') return 'json'
  const str = String(value)
  // URL
  if (/^https?:\/\//.test(str)) return 'url'
  // Phone
  if (/^\+?\d[\d\s\-()]{7,}$/.test(str)) return 'phone'
  // Date-like ISO strings
  if (/^\d{4}-\d{2}-\d{2}/.test(str)) return 'date'
  // Date-like MM/DD/YYYY
  if (/^\d{1,2}\/\d{1,2}\/\d{4}$/.test(str)) return 'date'
  // Address heuristic
  if (key.toLowerCase().includes('address') || key.toLowerCase().includes('addr')) return 'address'
  return 'text'
}

/** Humanize a snake_case or camelCase key */
export function humanizeKey(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/\b\w/g, c => c.toUpperCase())
}

// ---- Source type configs ----

const ncVoterConfig: SourceTypeConfig = {
  label: 'NC Voter Record',
  icon: '🗳️',
  primaryFields: [
    'first_name', 'middle_name', 'last_name', 'name_suffix',
    'party_cd', 'county_desc', 'voter_reg_num',
    'res_street_address', 'res_city_desc', 'state_cd', 'zip_code',
    'registr_dt', 'voter_status_desc', 'race_code', 'ethnic_code', 'gender_code',
    'birth_year', 'age_at_year_end',
    'confidential_ind',
  ],
  fieldOverrides: {
    first_name: { label: 'First Name', group: 'identity' },
    middle_name: { label: 'Middle Name', group: 'identity' },
    last_name: { label: 'Last Name', group: 'identity' },
    name_suffix: { label: 'Suffix', group: 'identity' },
    party_cd: { label: 'Party', icon: '🏛️', group: 'registration' },
    county_desc: { label: 'County', group: 'location' },
    voter_reg_num: { label: 'Registration #', group: 'registration' },
    res_street_address: { label: 'Street Address', type: 'address', group: 'location' },
    res_city_desc: { label: 'City', group: 'location' },
    state_cd: { label: 'State', group: 'location' },
    zip_code: { label: 'ZIP Code', group: 'location' },
    registr_dt: { label: 'Registration Date', type: 'date', group: 'registration' },
    voter_status_desc: { label: 'Voter Status', group: 'registration' },
    race_code: { label: 'Race', group: 'demographics' },
    ethnic_code: { label: 'Ethnicity', group: 'demographics' },
    gender_code: { label: 'Gender', group: 'demographics' },
    birth_year: { label: 'Birth Year', group: 'demographics' },
    age_at_year_end: { label: 'Age', group: 'demographics' },
    confidential_ind: { label: 'Confidential', type: 'boolean', icon: '🔒', group: 'registration' },
    voting_history: { label: 'Voting History', type: 'array', group: 'history' },
    ncid: { label: 'NCID', group: 'registration' },
    precinct_desc: { label: 'Precinct', group: 'districts' },
    municipality_desc: { label: 'Municipality', group: 'districts' },
    ward_desc: { label: 'Ward', group: 'districts' },
    cong_dist_desc: { label: 'Congressional District', group: 'districts' },
    nc_senate_desc: { label: 'NC Senate District', group: 'districts' },
    nc_house_desc: { label: 'NC House District', group: 'districts' },
    school_dist_desc: { label: 'School District', group: 'districts' },
  },
  groups: [
    { key: 'identity', label: 'Identity', icon: '👤' },
    { key: 'registration', label: 'Registration', icon: '📋' },
    { key: 'location', label: 'Location', icon: '📍' },
    { key: 'demographics', label: 'Demographics', icon: '📊' },
    { key: 'districts', label: 'Districts', icon: '🗺️' },
    { key: 'history', label: 'Voting History', icon: '📅' },
  ],
}

const gdeltDocConfig: SourceTypeConfig = {
  label: 'GDELT Article',
  icon: '🌐',
  primaryFields: [
    'title', 'url', 'domain', 'language', 'source_country',
    'seendate', 'socialimage', 'query_category',
  ],
  fieldOverrides: {
    title: { label: 'Title', group: 'article' },
    url: { label: 'URL', type: 'url', group: 'article' },
    domain: { label: 'Domain', group: 'source' },
    language: { label: 'Language', group: 'source' },
    source_country: { label: 'Country', icon: '🌍', group: 'source' },
    seendate: { label: 'Date Seen', type: 'date', group: 'article' },
    socialimage: { label: 'Image URL', type: 'url', group: 'article' },
    query_category: { label: 'Category', icon: '🏷️', group: 'classification' },
  },
  groups: [
    { key: 'article', label: 'Article', icon: '📰' },
    { key: 'source', label: 'Source', icon: '🔗' },
    { key: 'classification', label: 'Classification', icon: '🏷️' },
  ],
}

const rssArticleConfig: SourceTypeConfig = {
  label: 'RSS Article',
  icon: '📰',
  primaryFields: [
    'title', 'link', 'source_feed', 'published', 'summary', 'author',
  ],
  fieldOverrides: {
    title: { label: 'Title', group: 'article' },
    link: { label: 'Link', type: 'url', group: 'article' },
    source_feed: { label: 'Source Feed', group: 'source' },
    published: { label: 'Published', type: 'date', group: 'article' },
    summary: { label: 'Summary', group: 'content' },
    author: { label: 'Author', group: 'source' },
    content: { label: 'Content', group: 'content' },
  },
  groups: [
    { key: 'article', label: 'Article', icon: '📰' },
    { key: 'source', label: 'Source', icon: '🔗' },
    { key: 'content', label: 'Content', icon: '📄' },
  ],
}

const icijEntityConfig: SourceTypeConfig = {
  label: 'ICIJ Entity',
  icon: '🏢',
  primaryFields: [
    'name', 'jurisdiction', 'entity_type', 'incorporation_date',
    'inactivation_date', 'struck_off_date', 'status',
    'service_provider', 'source_id',
  ],
  fieldOverrides: {
    name: { label: 'Entity Name', group: 'identity' },
    jurisdiction: { label: 'Jurisdiction', icon: '⚖️', group: 'identity' },
    entity_type: { label: 'Type', group: 'identity' },
    incorporation_date: { label: 'Incorporation Date', type: 'date', group: 'dates' },
    inactivation_date: { label: 'Inactivation Date', type: 'date', group: 'dates' },
    struck_off_date: { label: 'Struck Off Date', type: 'date', group: 'dates' },
    status: { label: 'Status', group: 'identity' },
    service_provider: { label: 'Service Provider', group: 'details' },
    source_id: { label: 'Source ID', group: 'details' },
  },
  groups: [
    { key: 'identity', label: 'Identity', icon: '🏢' },
    { key: 'dates', label: 'Key Dates', icon: '📅' },
    { key: 'details', label: 'Details', icon: '📋' },
  ],
}

const ofacSanctionConfig: SourceTypeConfig = {
  label: 'OFAC Sanction',
  icon: '⛔',
  primaryFields: [
    'name', 'sdn_type', 'program', 'title', 'remarks',
    'addresses', 'alt_names', 'ids',
  ],
  fieldOverrides: {
    name: { label: 'Name', group: 'identity' },
    sdn_type: { label: 'SDN Type', group: 'identity' },
    program: { label: 'Program', icon: '🏛️', group: 'sanction' },
    title: { label: 'Title', group: 'identity' },
    remarks: { label: 'Remarks', group: 'details' },
    addresses: { label: 'Addresses', type: 'array', group: 'location' },
    alt_names: { label: 'Alternate Names', type: 'array', group: 'identity' },
    ids: { label: 'Identifications', type: 'array', group: 'details' },
  },
  groups: [
    { key: 'identity', label: 'Identity', icon: '👤' },
    { key: 'sanction', label: 'Sanction Info', icon: '⛔' },
    { key: 'location', label: 'Addresses', icon: '📍' },
    { key: 'details', label: 'Details', icon: '📋' },
  ],
}

export const FIELD_DISPLAY_CONFIG: Record<string, SourceTypeConfig> = {
  nc_voter: ncVoterConfig,
  gdelt_doc: gdeltDocConfig,
  rss_article: rssArticleConfig,
  rss: rssArticleConfig,
  icij_entity: icijEntityConfig,
  icij: icijEntityConfig,
  ofac_sanction: ofacSanctionConfig,
  ofac: ofacSanctionConfig,
}

/** Fields to always hide from display */
export const HIDDEN_FIELDS = new Set([
  'embedding', 'vector', 'raw_content', 'processing_trace',
  '_id', '__v', 'created_at', 'updated_at',
])
