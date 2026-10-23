import {defineConfig} from 'vite';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

// The prompt catalogue lives at the repository root (docs/agent-prompts/), outside
// the desktop app root. The desktop imports it with `?raw` so the content is bundled
// into the build; the dev server additionally needs filesystem read access there.
const appRoot = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(appRoot, '../..');

export default defineConfig({
  server: {
    fs: {
      allow: [appRoot, repoRoot],
    },
  },
});
