import { cp, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
await mkdir(`${root}dist/src/data`, { recursive: true });
for (const file of ['index.html', 'styles.css']) await cp(`${root}${file}`, `${root}dist/${file}`);
await cp(`${root}src/data/contract.json`, `${root}dist/src/data/contract.json`);
console.log('Built frontend/dist (no runtime package dependencies).');
