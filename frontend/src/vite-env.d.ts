/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL for the Periphery API. Empty string = same-origin. */
  readonly VITE_API_BASE_URL?: string
  /** Clerk publishable key. When set, Clerk human-login is enabled. */
  readonly VITE_CLERK_PUBLISHABLE_KEY?: string
  /** Mapbox access token for map display. */
  readonly VITE_MAPBOX_ACCESS_TOKEN?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
