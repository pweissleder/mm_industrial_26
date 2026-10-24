interface ImportMetaEnv {
  /** Base URL of the backend. Leave empty to use the dev-server proxy (same origin). */
  readonly PUBLIC_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
