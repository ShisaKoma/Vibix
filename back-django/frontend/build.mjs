import { build } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';
import { dirname, join, parse } from 'node:path';

const result = await build({
  entryPoints: ['src/editor.js'],
  outfile: '../static/js/editor.js',
  bundle: true,
  format: 'iife',
  target: ['es2020'],
  minify: true,
  legalComments: 'linked',
  metafile: true,
  banner: {js: '/*! Third-party licenses: editor.licenses.txt */'},
});

const licenses = new Map();
for (const input of Object.keys(result.metafile.inputs)) {
  if (!input.includes('node_modules')) continue;
  let directory = dirname(input);
  while (directory !== parse(directory).root && directory !== '.') {
    try {
      const pkg = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'));
      const name = `${pkg.name}@${pkg.version}`;
      if (!licenses.has(name)) {
        for (const filename of ['LICENSE.md', 'LICENSE', 'LICENSE.txt', 'license']) {
          try {
            licenses.set(name, await readFile(join(directory, filename), 'utf8'));
            break;
          } catch (error) { if (error.code !== 'ENOENT') throw error; }
        }
        if (!licenses.has(name)) throw new Error(`Missing license for ${name}`);
      }
      break;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      directory = dirname(directory);
    }
  }
}
await writeFile('../static/js/editor.licenses.txt', [...licenses.entries()]
  .sort(([a], [b]) => a.localeCompare(b))
  .map(([name, license]) => `${name}\n${'='.repeat(72)}\n${license}`).join('\n\n'));
process.stdout.write(`Editor compiled; ${licenses.size} dependency licenses included.\n`);
await build({entryPoints: ['src/planning.js'], outfile: '../static/js/planning.js', bundle: true, format: 'iife', target: ['es2020'], minify: true});
process.stdout.write('Planning drag-and-drop compiled.\n');
