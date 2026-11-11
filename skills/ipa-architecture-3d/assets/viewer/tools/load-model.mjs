// Resolve the architecture model that the *viewer* will actually render.
//   - generated site : ./architecture.json  (build.py writes it to the output root)
//   - skill template : assets/examples/illustrative-architecture.json
// Falling back means a copied viewer keeps working, but tests in a generated site
// must exercise the generated model -- otherwise they validate the sample, not the
// artifact, and a broken site still shows green.
import fs from 'node:fs';
import path from 'node:path';

const here=path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1'));
const candidates=[
  path.resolve(here,'..','architecture.json'),
  path.resolve(here,'..','..','examples','illustrative-architecture.json'),
];
const found=candidates.find(p=>fs.existsSync(p));
if(!found)throw new Error(`architecture model not found; looked in:\n  ${candidates.join('\n  ')}`);
export const modelPath=found;
export const model=JSON.parse(fs.readFileSync(found,'utf8'));
