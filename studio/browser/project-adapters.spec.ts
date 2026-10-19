/** Installed workspace adapters: retain the material pre-P1 smoke journeys with explicit Run. */
import {test, expect, type Page} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {writeFile, readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {ProjectFixture} from './project-fixture';

const model = `import time
from dataclasses import dataclass
from simulatte import model
from simulatte.server import Server
from simulatte.layout import Point
@dataclass(frozen=True)
class Params:
    label: str
    delay: float = 0.01
@model(params=Params, horizon=4)
def build(env, layout, params):
    machine = Server(env=env, capacity=1, name='machine')
    layout.place(machine, position=Point(1, 2))
    def work():
        for _ in range(4):
            time.sleep(params.delay)
            yield env.timeout(1)
    env.process(work())
`;
async function setup(fixture: ProjectFixture, page: Page) {
  page.setDefaultTimeout(15000);
  await fixture.setup();
  execFileSync(fixture.python,['-I','-c',`
from pathlib import Path
import sys
from simulatte.projects import StudioTarget,install_starter,starter_files
from simulatte.projects.service import ProjectService
root=Path(sys.argv[1]); install_starter(root,starter_files('production'))
service=ProjectService.open(StudioTarget(root,None))
try:
 pool=service.environments; pool.activate(pool.use_existing(Path(sys.executable)).id)
finally: service.close()
`,fixture.root],{stdio:'pipe'});
  await writeFile(join(fixture.root,'models/production.py'),model);
  await page.goto(await fixture.launch());
  await page.getByRole('link',{name:/Production/}).first().click();
  await page.getByRole('button',{name:'Prepare model',exact:true}).click();
  await expect(page.getByRole('button',{name:'Run',exact:true})).toBeVisible();
}
const state = (page: Page) => page.evaluate(async()=> (await (await fetch('/api/state',{
  headers:{'X-Simulatte-Session':sessionStorage.getItem('simulatte-session')!},
})).json()));
const count = async(page: Page) => (await state(page)).project.history.filter((r:{has_result:boolean})=>r.has_result).length;
async function run(page: Page) {
  await page.getByRole('button',{name:'Run',exact:true}).click();
  await expect(page.locator('.ws-page-heading .ws-badge')).toHaveText('succeeded',{timeout:30000});
  await expect(page.locator('.ws-recording .status')).toHaveText('paused',{timeout:30000});
  return page.url().split('#/run/')[1]!;
}
async function returnModel(page: Page) {
  await page.getByRole('link',{name:'Models',exact:true}).click();
  await page.getByRole('link',{name:'Production',exact:true}).click();
}

test('workspace required input, conflicting drafts and physical layout preview/apply/undo/redo/drag/lanes',async({page})=>{
  test.setTimeout(180000);
  const fixture=new ProjectFixture();
  const errors:string[]=[]; page.on('pageerror',error=>errors.push(error.message));
  try {
    await setup(fixture,page);
    expect(await count(page)).toBe(0);
    await page.getByRole('button',{name:'Run',exact:true}).click();
    await expect(page.getByRole('alert')).toContainText('Enter label');
    expect(await count(page)).toBe(0);
    await page.getByRole('button',{name:'Use empty string for label',exact:true}).click();
    await run(page);
    expect(await count(page)).toBe(1);
    await returnModel(page);
    const delay=page.getByLabel('delay',{exact:false});
    await delay.fill('0.02');
    await page.evaluate(async()=> {
      const socket=new WebSocket(location.origin.replace('http:','ws:')+'/ws');
      await new Promise<void>((resolve,reject)=>{
        const timer=setTimeout(()=>reject(Error('Second-tab mutation timeout')),10000);
        let sent=false;
        socket.onopen=()=>socket.send(JSON.stringify({v:1,type:'auth',secret:sessionStorage.getItem('simulatte-session')}));
        socket.onmessage=event=>{
          const message=JSON.parse(event.data);
          if(message.type==='state'&&!sent) {
            sent=true; const m=message.body.project.models[0];
            socket.send(JSON.stringify({v:1,type:'params.set',id:'second-tab',revision:m.revision,
              body:{model_id:m.id,changes:{parameters:{...m.inputs.options.parameters,delay:0.03}}}}));
          }
          if(message.type==='response') {clearTimeout(timer);socket.close();message.body.ok?resolve():reject(Error(JSON.stringify(message.body.error)));}
        };
      });
    });
    await expect(page.getByText('Inputs changed while you were editing. Your draft is retained.')).toBeVisible();
    await expect(delay).toHaveValue('0.02');
    await expect(page.getByRole('button',{name:'Run',exact:true})).toBeDisabled();
    await page.getByRole('button',{name:'Reload current inputs',exact:true}).click();
    await expect(delay).toHaveValue('0.03');
    await page.locator('summary').filter({hasText:/^Layout$/}).click();
    await page.getByRole('button',{name:'Set grid',exact:true}).click();
    await page.getByRole('button',{name:'Set dx',exact:true}).click();
    await page.getByLabel('layout.grid.dx',{exact:true}).fill('2');
    await page.getByRole('button',{name:'Validate layout',exact:true}).click();
    await expect(page.getByText(/Physical preparation passed in a disposable process/)).toBeVisible();
    await page.getByRole('button',{name:'Apply layout',exact:true}).click();
    await expect(page.getByRole('button',{name:'Undo layout',exact:true})).toBeEnabled();
    expect(await count(page)).toBe(1); // Save never starts a simulation.
    await page.getByRole('button',{name:'Undo layout',exact:true}).click();
    await expect(page.getByRole('button',{name:'Set grid',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'Redo layout',exact:true}).click();
    await expect(page.getByLabel('layout.grid.dx',{exact:true})).toHaveValue('2');
    await page.getByLabel('Selected handles').selectOption({label:'machine'});
    await page.getByLabel('Move x',{exact:true}).fill('2');
    await page.getByRole('button',{name:'Move selected',exact:true}).click();
    const x=page.getByLabel('layout.placements.machine.position[0]',{exact:true});
    await expect(x).toHaveValue('3');
    await page.getByRole('button',{name:'Apply layout',exact:true}).click();
    await expect(page.getByRole('button',{name:'Move selected',exact:true})).toBeEnabled();
    await page.getByRole('button',{name:'Fit layout',exact:true}).click();
    const canvas=page.locator('.layout-canvas canvas');
    await canvas.scrollIntoViewIfNeeded();
    const box=(await canvas.boundingBox())!;
    await page.mouse.move(box.x+box.width/2,box.y+box.height/2);
    await page.mouse.down();
    await page.mouse.move(box.x+box.width/2+40,box.y+box.height/2-20,{steps:8});
    await expect(x).toHaveValue('3');
    await page.mouse.up();
    await expect(x).toHaveValue('4');
    await page.getByRole('button',{name:'Set network',exact:true}).click();
    await page.getByLabel('layout.network variant',{exact:true}).selectOption({label:'lanes'});
    const network=page.getByRole('group',{name:'layout.network',exact:true});
    await network.getByRole('button',{name:'Set lanes',exact:true}).click();
    await page.getByLabel('New layout.network.lanes name',{exact:true}).fill('main');
    await network.getByRole('button',{name:'Add named entry',exact:true}).click();
    await page.getByRole('button',{name:'Set vertices',exact:true}).click();
    const vertex=(index:number)=>`layout.network.lanes.main.vertices[${index}]`;
    for(const [i,id] of ['a','b'].entries()) {await page.getByLabel(vertex(i)+'.id',{exact:true}).fill(id);await page.getByLabel(vertex(i)+'.id',{exact:true}).press('Tab');}
    await page.getByLabel(vertex(1)+'.position[0]',{exact:true}).fill('4');
    await page.getByRole('button',{name:'Insert after 1',exact:true}).click();
    await expect(page.getByLabel(vertex(2)+'.id',{exact:true})).toHaveValue('b');
    await page.getByRole('button',{name:'Remove item 2',exact:true}).click();
    await expect(page.getByLabel(vertex(1)+'.id',{exact:true})).toHaveValue('b');
    await page.getByRole('button',{name:'Validate layout',exact:true}).click();
    await expect(page.getByText(/Physical preparation passed in a disposable process/)).toBeVisible();
    await page.getByRole('button',{name:'Apply layout',exact:true}).click();
    await expect(page.getByRole('button',{name:'Undo layout',exact:true})).toBeEnabled();
    expect(await count(page)).toBe(1);
    await run(page);
    expect(await count(page)).toBe(2);
    await returnModel(page);
    await delay.fill('0.02');
    await writeFile(join(fixture.root,'models/production.py'),model.replace('delay: float = 0.01','delay: int = 1'));
    await expect.poll(async()=> (await state(page)).project.models[0].metadata?.models[0].parameters.fields.find((f:{name:string})=>f.name==='delay')?.type,{timeout:30000}).toBe('int');
    await expect(page.getByText('Inputs changed while you were editing. Your draft is retained.')).toBeVisible();
    await expect(delay).toHaveValue('0.02');
    await expect(page.getByRole('button',{name:'Run',exact:true})).toBeDisabled();
    expect(await count(page)).toBe(2);
    expect(errors).toEqual([]);
  } finally {await fixture.close();}
});

test('workspace presentation-only save pins export and comparison shares one clock',async({page,browser})=>{
  test.setTimeout(180000);
  const fixture=new ProjectFixture();
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  try {
    await setup(fixture,page);
    await page.getByRole('button',{name:'Use empty string for label',exact:true}).click();
    const first=await run(page);
    await returnModel(page);
    await page.getByLabel('Seed',{exact:true}).fill('12');
    const second=await run(page);
    await page.getByRole('button',{name:'Export video / PNG',exact:true}).click();
    const dialog=page.getByRole('dialog',{name:'Export recording'});
    await expect(dialog).toBeVisible();
    await page.evaluate(()=>Object.defineProperty(window,'showSaveFilePicker',{value:undefined}));
    const png = async(name:string) => {
      await dialog.getByLabel('Format',{exact:true}).selectOption('png');
      await dialog.getByLabel('End time',{exact:true}).fill('0');
      await dialog.getByLabel('Width',{exact:true}).fill('320');
      await dialog.getByLabel('Height',{exact:true}).fill('180');
      await dialog.getByRole('button',{name:'Start export',exact:true}).click();
      const link=dialog.getByRole('link',{name:/Download simulatte/});
      await expect(link).toBeVisible();
      const waiting=page.waitForEvent('download');await link.click();
      const path=join(fixture.directory,name+'.zip');await(await waiting).saveAs(path);
      const hash=execFileSync(fixture.python,['-I','-c',
        'import sys,zipfile,hashlib; z=zipfile.ZipFile(sys.argv[1]); print(hashlib.sha256(z.read(next(n for n in z.namelist() if n.endswith(".png")))).hexdigest())',path],{encoding:'utf8'}).trim();
      await dialog.getByRole('button',{name:'Remove temporary copy',exact:true}).click();
      return hash;
    };
    const pinnedBefore=await png('before');
    // A second authenticated tab changes only presentation while the export is pinned.
    const outcome=await page.evaluate(async()=>{
      const headers={'X-Simulatte-Session':sessionStorage.getItem('simulatte-session')!};
      const before=await (await fetch('/api/state',{headers})).json();
      const m=before.project.models[0];
      const bundle=await (await fetch(m.view.layout.url,{headers})).json();
      const response=await fetch('/api/layout',{method:'PUT',headers:{...headers,
        'X-Simulatte-Model':m.id,'X-Simulatte-Revision':JSON.stringify(m.revision),'X-Simulatte-Command':crypto.randomUUID(),'Content-Type':'application/json'},
        body:JSON.stringify({schema_version:'1.0',placements:{machine:{kind:bundle.resolved.placements.machine.kind,visual:{label:'presentation-only-label'}}}})});
      return {status:response.status,body:await response.json()};
    });
    expect(outcome.status,JSON.stringify(outcome.body)).toBe(200);expect(outcome.body.ok).toBe(true);
    await expect(dialog).toBeVisible();
    expect(await count(page)).toBe(2);
    expect(await png('pinned-after')).toBe(pinnedBefore);
    await dialog.getByRole('button',{name:'Close',exact:true}).click();
    const waiting=page.waitForEvent('download');
    await page.getByRole('button',{name:'Export HTML',exact:true}).click();
    const htmlPath=join(fixture.root,'presentation.html');await(await waiting).saveAs(htmlPath);
    const html=await readFile(htmlPath,'utf8');
    const embedded=JSON.parse(html.match(/<script id="presentation" type="application\/json">(.*?)<\/script>/s)![1]!);
    expect(embedded.layout.resolved.placements.machine.visual.label).toBe('presentation-only-label');
    await page.getByRole('button',{name:'Export video / PNG',exact:true}).click();
    expect(await png('new-presentation')).not.toBe(pinnedBefore);
    await dialog.getByRole('button',{name:'Close',exact:true}).click();
    const offline=await browser.newContext({offline:true});
    const offlinePage=await offline.newPage();const requests:string[]=[];
    offlinePage.on('request',r=>{if(!r.url().startsWith('file:'))requests.push(r.url());});
    offlinePage.on('pageerror',e=>errors.push(e.message));
    await offlinePage.goto(pathToFileURL(htmlPath).href);
    await expect(offlinePage.locator('output')).toContainText('t = 0');
    expect(requests).toEqual([]);await offline.close();
    await page.getByRole('link',{name:'Experiments',exact:true}).click();
    await page.locator('summary').filter({hasText:/^Compare$/}).click();
    await page.getByLabel('Side A recording',{exact:true}).selectOption(first);
    await page.getByLabel('Side B recording',{exact:true}).selectOption(second);
    await expect(page.locator('.compare-scene canvas')).toHaveCount(2);
    const times=()=>page.evaluate(()=>['A','B'].map(side=>Number(document.querySelector<HTMLOutputElement>(`output[aria-label="Side ${side} time"]`)?.dataset.time)));
    await page.getByRole('button',{name:'Play both',exact:true}).click();
    await expect.poll(async()=> (await times())[0]).toBeGreaterThan(0);
    const [a,b]=await times();expect(Math.abs(a!-b!)).toBeLessThanOrEqual(1/30);
    await page.getByRole('button',{name:'Pause both',exact:true}).click({delay:100});
    await expect.poll(async()=>new Set(await times()).size).toBe(1);
    await page.getByLabel('Compare time').fill('1');
    await expect.poll(times).toEqual([1,1]);
    await page.getByRole('button',{name:'Close compare',exact:true}).click();
    await expect(page.locator('.compare-scene')).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {await fixture.close();}
});
