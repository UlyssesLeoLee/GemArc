/** Fail loudly, and correctly, when the build tool would be skipped.
 *
 *  Symptom this prevents: `npm install` succeeds, `vite` never lands, and the next
 *  command dies with
 *      [vite]: Rolldown failed to resolve import "three" from .../src/main.js
 *  which names the WRONG package. `three` is installed fine -- `vite`, the thing that
 *  resolves imports, was dropped because the environment sets NODE_ENV=production
 *  (npm then defaults to omit=dev).
 *
 *  This runs as `preinstall`, before anything is fetched, and tells the truth.
 */
import fs from 'node:fs';
import path from 'node:path';

const omit=process.env.NPM_CONFIG_OMIT
  || (process.env.NODE_ENV==='production'?'dev':'')
  || '';
const skipDev=omit.split(',').map(s=>s.trim()).includes('dev');

// If devDependencies are being skipped, `vite` cannot be resolved from the registry
// either -- so this is about telling the user WHY before a confusing later failure.
if(skipDev){
  const here=path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1'));
  const installed=path.resolve(here,'..','node_modules','vite');
  if(!fs.existsSync(installed)){
    console.error([
      '',
      '  ✗ This project needs its BUILD tools, but npm is configured to skip devDependencies.',
      '',
      `    NODE_ENV=${process.env.NODE_ENV||'(unset)'}  NPM_CONFIG_OMIT=${process.env.NPM_CONFIG_OMIT||'(unset)'}`,
      '',
      '    A later step would otherwise fail with a misleading message such as',
      '    "Rolldown failed to resolve import \'three\'" -- `three` is installed;',
      '    the missing package is `vite`.',
      '',
      '    Fix it with ONE of:',
      '      npm install --include=dev      # this run only',
      '      set NODE_ENV=development       # PowerShell: $env:NODE_ENV="development"',
      '      delete the include=dev line from .npmrc   # to opt out deliberately',
      '',
    ].join('\n'));
    process.exit(1);
  }
}
