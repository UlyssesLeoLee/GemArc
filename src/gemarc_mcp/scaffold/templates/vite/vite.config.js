import { defineConfig } from 'vite';
import { fileURLToPath, URL } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));

/**
 * The same runtime sources serve both profiles. Only the resolution differs:
 * the static profile uses an import map, this one uses an alias.
 */
export default defineConfig({
  root: here,
  publicDir: false,
  resolve: {
    alias: {
      '@gemarc/runtime': resolve(here, 'runtime'),
    },
  },
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
