/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Bearer token, when the backend has DASHBOARD_TOKEN set. */
  readonly VITE_DASHBOARD_TOKEN?: string
  /** Backend origin for the dev proxy. */
  readonly VITE_BACKEND?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
