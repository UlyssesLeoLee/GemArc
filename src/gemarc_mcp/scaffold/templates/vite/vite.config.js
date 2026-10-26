import { defineConfig } from 'vite';
import { fileURLToPath, URL } from 'node:url';
import { dirname } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));

/**
 * The same runtime sources serve both profiles. The application always sits at
 * the project root, so it reaches the runtime by a relative path; this profile
 * only differs in how the optional third-party libraries are resolved.
 */
export default defineConfig({
  root: here,
  publicDir: false,
  server: {
    port: 5173,
    open: false,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2022',
  },
});
