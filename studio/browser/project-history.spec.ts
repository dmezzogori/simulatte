import {test, expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {ProjectFixture} from './project-fixture';

test('bookmarked old run loads saved annotations, preserves edits and shows recovery notes', async ({page}) => {
  test.setTimeout(90000);
  const fixture = new ProjectFixture();
  try {
    await fixture.setup();
    const id = execFileSync(fixture.python, ['-I', '-c', `
from pathlib import Path
import sys
from simulatte.execution.store import RunStore
from simulatte.execution.schema import ExecutionRequest
from simulatte.execution.project_store import ProjectStore
root=Path(sys.argv[1])
request=ExecutionRequest(model='plant.py:build',source_hash='a'*64,input_hash='b'*64,layout_hash='c'*64,layout_revision=1,parameters={},seed=0,stopping={'type':'horizon','horizon':10,'warmup':0},runtime={})
with RunStore.open(root/'.simulatte') as store:
 keys=[]
 for _ in range(51):
  key=store.assign(request,store.create_revision({}))
  store.revoke(key,'cancelled')
  keys.append(key)
 ProjectStore(store).annotate(keys[0].attempt_id,name='Keep this study',text='Original note',important=True)
 print(keys[0].attempt_id)
`, fixture.root], {encoding:'utf8'}).trim();
    let responses = 0;
    page.on('websocket', socket => socket.on('framereceived', ({payload}) => {
      const message = JSON.parse(String(payload));
      if(message.body?.run?.id === id) responses++;
    }));
    await page.goto(await fixture.launch());
    await page.evaluate(id => {location.hash = '#/run/' + id;}, id);
    await expect(page.getByLabel('Run name', {exact:true})).toHaveValue('Keep this study');
    await expect(page.getByLabel('Notes', {exact:true})).toHaveValue('Original note');
    await expect(page.getByLabel('Important · protect from pruning', {exact:true})).toBeChecked();
    await page.getByLabel('Notes', {exact:true}).fill('Edited note');
    const previous = responses;
    await expect.poll(() => responses).toBeGreaterThan(previous);
    await expect(page.getByLabel('Notes', {exact:true})).toHaveValue('Edited note');
    await page.getByRole('button', {name:'Save notes',exact:true}).click();
    await expect(page.getByRole('button', {name:'Save notes',exact:true})).toBeDisabled();
    await fixture.stop();
    await writeFile(join(fixture.root,'pyproject.toml'), 'invalid toml !!!');
    await page.goto(await fixture.launch());
    await page.evaluate(id => {location.hash = '#/run/' + id;}, id);
    await expect(page.getByLabel('Run name', {exact:true})).toHaveValue('Keep this study');
    await expect(page.getByLabel('Notes', {exact:true})).toHaveValue('Edited note');
    await expect(page.getByLabel('Notes', {exact:true})).toHaveAttribute('readonly', '');
    await expect(page.getByLabel('Important · protect from pruning', {exact:true})).toBeChecked();
    await expect(page.getByRole('button', {name:'Save notes',exact:true})).toHaveCount(0);
  } finally {await fixture.close();}
});

test('saved experiment tables reopen normally and in recovery without preparing a model', async ({page}) => {
  test.setTimeout(120000);
  const fixture = new ProjectFixture();
  try {
    await fixture.setup();
    execFileSync(fixture.python, ['-I', '-c', `
from pathlib import Path
import sys,json
from simulatte.projects import StudioTarget,install_starter,starter_files
from simulatte.projects.service import ProjectService
from simulatte.experiments import load_experiment
root=Path(sys.argv[1]);install_starter(root,starter_files('production'))
service=ProjectService.open(StudioTarget(root,None))
try:
 pool=service.environments;pool.activate(pool.use_existing(Path(sys.executable)).id)
 path=root/'saved.experiment.json'
 path.write_text(json.dumps({'schema':1,'name':'Stored study','model':'models/production.py:build','replications':{'count':1},'configurations':{'grid':{'servers':[2,3]}},'level':'full'}))
 co=service.ensure_coordinator()
 run=co.contexts[service.workspace.models[0].id].start_experiment(load_experiment(path))
 assert co.wait_experiment(run,timeout=30)['status']=='completed'
finally:service.close()
(root/'models/production.py').write_text('raise AssertionError("must not import saved study")')
`, fixture.root], {stdio:'pipe'});
    for(const recovery of [false,true]) {
      if(recovery) await writeFile(join(fixture.root,'pyproject.toml'),'invalid toml !!!');
      await page.goto(await fixture.launch());
      await page.getByRole('link',{name:'Experiments',exact:true}).click();
      await page.locator('summary').filter({hasText:'Experiments (1)'}).click();
      await page.getByRole('button',{name:'Stored study',exact:true}).click();
      await expect(page.locator('.run-details h4')).toContainText('Stored study · completed');
      await expect(page.locator('.run-details')).toContainText('servers=2');
      await page.locator('.run-details summary').filter({hasText:'Replications (2)'}).click();
      await expect(page.getByRole('button',{name:'Open',exact:true})).toHaveCount(2);
      if(recovery) {
        await expect(page.getByRole('button',{name:'Start again',exact:true})).toBeDisabled();
        await expect(page.getByRole('button',{name:'Delete',exact:true})).toBeDisabled();
      }
      await expect(page.getByRole('alert')).toHaveCount(0);
      await fixture.stop();
    }
  } finally {await fixture.close();}
});
