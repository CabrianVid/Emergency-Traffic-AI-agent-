const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
// Webpack reserves "!" for loader syntax. A Windows app-container temp path
// may contain this character; keep its temporary bundles inside this project.
const env = {...process.env};
if (os.tmpdir().includes('!')) {
  const temp = path.join(root, '.qa', 'tmp');
  fs.mkdirSync(temp, {recursive:true});
  Object.assign(env, {TMP:temp,TEMP:temp,TMPDIR:temp});
}
const cli = path.join(root, 'node_modules/@remotion/cli/remotion-cli.js');
const child = spawnSync(process.execPath,[cli,...process.argv.slice(2)], {cwd:root,env,stdio:'inherit'});
if (child.error) throw child.error;
process.exitCode=child.status ?? 1;
