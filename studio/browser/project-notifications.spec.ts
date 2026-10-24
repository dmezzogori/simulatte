import {test, expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {ProjectFixture} from './project-fixture';

const prepare = (fixture: ProjectFixture, body: string) => execFileSync(fixture.python, ['-I','-c',`
from pathlib import Path
import sys
from simulatte.projects import StudioTarget, install_starter, starter_files
from simulatte.projects.service import ProjectService
root=Path(sys.argv[1]); install_starter(root,starter_files('production'))
service=ProjectService.open(StudioTarget(root,None))
try:
 pool=service.environments; pool.activate(pool.use_existing(Path(sys.executable)).id)
 ${body}
finally: service.close()
`, fixture.root], {encoding:'utf8'}).trim();

test('workspace reports replay preflight failure and reconciles it after reconnect', async ({page}) => {
  test.setTimeout(90000);
  const fixture=new ProjectFixture();
  try {
    await fixture.setup();
    const attempt=prepare(fixture, `co=service.ensure_coordinator(); model=service.workspace.models[0].id
 prepared=co.prepare_model(model,timeout=20)
 request=co.make_request(prepared,level='kpi')
 admission=co.admit_run(model,request,prepared.revision)
 result=co.wait(admission.key,timeout=20); assert result.key==admission.key
 with co.store._transaction() as db: db.execute("DELETE FROM snapshots WHERE kind='preparation'")
 print(admission.key.attempt_id)`);
    await page.addInitScript(() => {
      const NativeWebSocket=window.WebSocket;
      let socket: WebSocket;
      window.WebSocket=class extends NativeWebSocket {
        constructor(url: string|URL, protocols?: string|string[]) {super(url,protocols); socket=this;}
      };
      Object.assign(window,{reviewDisconnect:()=>socket.close()});
    });
    const states: {status:string,reason:string}[][]=[];
    let connections=0;
    page.on('websocket', socket=>{connections++; socket.on('framereceived', ({payload})=>{
      const message=JSON.parse(String(payload));
      if(message.type==='state' && message.body.replays) states.push(message.body.replays);
    });});
    await page.goto(await fixture.launch());
    await page.getByRole('link',{name:'All experiments',exact:true}).click();
    await page.locator('summary').filter({hasText:/^Compare$/}).click();
    await page.getByLabel('Side A recording',{exact:true}).selectOption(attempt);
    await page.getByRole('button',{name:'Replay at full level',exact:true}).click();
    const outcome=page.getByRole('region',{name:'Compare side A'}).locator('p[role=status]');
    await expect(outcome).toContainText(/Replay (failed|validation_failed)/);
    await expect(outcome).not.toContainText('running');
    await expect.poll(()=>states.at(-1)?.[0]?.status).toBe('failed');
    const count=states.length;
    const sockets=connections;
    await page.evaluate(()=> (window as unknown as {reviewDisconnect():void}).reviewDisconnect());
    await expect.poll(()=>connections).toBeGreaterThan(sockets);
    await expect.poll(()=>states.length).toBeGreaterThan(count);
    await expect(outcome).toContainText('Replay failed');
    await expect(outcome).toContainText(/preparation|snapshot/i);
  } finally {await fixture.close();}
});

test('workspace live recording receives cancellation and leaves buffering', async ({page})=>{
  test.setTimeout(90000);
  const fixture=new ProjectFixture();
  try {
    await fixture.setup(); prepare(fixture, 'pass');
    await writeFile(join(fixture.root,'models/production.py'), `
import time
from simulatte import model
from dataclasses import dataclass
from simulatte.events import ObserverEvent,event_type
@event_type('review.tick')
class Tick(ObserverEvent):
    value: int
@dataclass(frozen=True)
class Params:
    pass
@model(params=Params,horizon=100000)
def build(env,layout,params):
    def work():
        for i in range(100000):
            env.emit(Tick(value=i))
            time.sleep(0.005)
            yield env.timeout(1)
    env.process(work())
`);
    await page.goto(await fixture.launch());
    await page.getByRole('link',{name:'Models',exact:true}).click();
    await page.getByRole('link',{name:'Production',exact:true}).click();
    await page.getByRole('button',{name:'Prepare model',exact:true}).click();
    await page.getByRole('button',{name:'Run',exact:true}).click();
    await expect(page.locator('.ws-recording .status')).toHaveText(/paused|buffering/,{timeout:20000});
    await page.getByRole('button',{name:'Stop run',exact:true}).click();
    await expect(page.locator('.ws-page-heading .ws-badge')).toHaveText('cancelled');
    await expect(page.locator('.ws-recording .status')).toHaveText('invalidated');
  } finally {await fixture.close();}
});


test('workspace comparison recovers verified replay from reconnect without its terminal event', async ({page}) => {
  test.setTimeout(90000);
  const fixture = new ProjectFixture();
  try {
    await fixture.setup();
    const attempt = prepare(fixture, `co=service.ensure_coordinator(); model=service.workspace.models[0].id
 prepared=co.prepare_model(model,timeout=20)
 request=co.make_request(prepared,level='kpi')
 admission=co.admit_run(model,request,prepared.revision)
 co.wait(admission.key,timeout=20)
 print(admission.key.attempt_id)`);
    await page.addInitScript(() => {
      const NativeWebSocket = window.WebSocket;
      Object.assign(window, {missedReplayTerminal: 0});
      window.WebSocket = class extends NativeWebSocket {
        constructor(url: string|URL, protocols?: string|string[]) {
          super(url, protocols);
          this.addEventListener('message', event => {
            const message = JSON.parse(String(event.data));
            if(message.type === 'replay_state' && message.body.status === 'succeeded') {
              event.stopImmediatePropagation();
              (window as unknown as {missedReplayTerminal:number}).missedReplayTerminal++;
              this.close(); // The next authenticated snapshot is the only terminal evidence the UI gets.
            }
          });
        }
      };
    });
    let connections = 0;
    page.on('websocket', () => {connections++;});
    await page.goto(await fixture.launch());
    await page.getByRole('link', {name:'All experiments',exact:true}).click();
    await page.locator('summary').filter({hasText:/^Compare$/}).click();
    await page.getByLabel('Side A recording', {exact:true}).selectOption(attempt);
    await page.getByRole('button', {name:'Replay at full level',exact:true}).click();
    await expect.poll(() => page.evaluate(() => (window as unknown as {missedReplayTerminal:number}).missedReplayTerminal)).toBe(1);
    await expect.poll(() => connections).toBeGreaterThan(1);
    await expect(page.locator('.compare-side[aria-label="Compare side A"] .replay-verified')).toHaveText('verified replay');
    await expect(page.locator('.compare-scene[data-side="A"] canvas')).toHaveCount(1);
    await expect(page.getByText('Replay running at full level…')).toHaveCount(0);
  } finally {await fixture.close();}
});
