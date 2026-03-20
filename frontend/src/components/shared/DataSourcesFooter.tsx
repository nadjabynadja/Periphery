// ============================================
// DataSourcesFooter — Third-Party Data Attribution
// Required by ODbL v1.0 and CC BY-SA 3.0 for ICIJ Offshore Leaks data.
// ============================================

export function DataSourcesFooter() {
  return (
    <footer
      className="shrink-0 px-3 py-1 border-t border-surface-border bg-base-800 flex items-center gap-3 flex-wrap"
      style={{ fontSize: '10px', color: 'var(--text-dim)', lineHeight: '1.4' }}
    >
      <span style={{ opacity: 0.6 }}>Data sources:</span>
      <span>
        Includes data from the{' '}
        <a
          href="https://offshoreleaks.icij.org/"
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: 'var(--accent-cyan)', textDecoration: 'underline' }}
        >
          ICIJ Offshore Leaks Database
        </a>
        {' '}(
        <a
          href="https://opendatacommons.org/licenses/odbl/1-0/"
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: 'var(--accent-cyan)', textDecoration: 'underline' }}
        >
          ODbL v1.0
        </a>
        {' / '}
        <a
          href="https://creativecommons.org/licenses/by-sa/3.0/"
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: 'var(--accent-cyan)', textDecoration: 'underline' }}
        >
          CC BY-SA 3.0
        </a>
        ) and the{' '}
        <a
          href="https://ofac.treasury.gov/"
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: 'var(--accent-cyan)', textDecoration: 'underline' }}
        >
          U.S. Treasury OFAC
        </a>
        {' '}sanctions lists (public domain).
      </span>
    </footer>
  )
}
