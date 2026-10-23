interface ImportMetaEnv {
  readonly VITE_CHAT_ORIGIN?: string;
  readonly VITE_LAN_ORIGIN?: string;
}

interface ImportMeta {
  readonly env?: ImportMetaEnv;
}

// Vite `?raw` imports (used by src/agent-prompts.ts) resolve to the file text.
declare module '*?raw' {
  const content: string;
  export default content;
}
