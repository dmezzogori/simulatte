import {execFileSync} from 'node:child_process';
import {readFileSync,readdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
const groups=JSON.parse(execFileSync('pnpm',['licenses','list','--prod','--json'],{encoding:'utf8'}));
let text='# Studio dependency notices\n\nRuntime workspace dependencies. Generated with `node scripts/notices.mjs`.\n';
for(const items of Object.values(groups))for(const item of items){
 if(item.name.startsWith('@simulatte/'))continue;
 const path=item.paths[0],names=readdirSync(path).filter(name=>/^(licen[cs]e|copying|notice)(\.|$)/i.test(name));
 text+=`\n## ${item.name} ${item.versions.join(', ')}\n\nLicense: ${item.license}. ${item.homepage??''}\n`;
 for(const name of names)text+='\n```text\n'+readFileSync(join(path,name),'utf8').trim()+'\n```\n';
}
writeFileSync('THIRD_PARTY_NOTICES.md',text.split(/\r?\n/).map(line=>line.trimEnd()).join('\n'));
