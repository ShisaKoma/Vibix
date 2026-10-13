import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const source = await readFile(new URL('../src/app/pages/contact/contact-client.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText;
const {ContactClient, ContactError, validChallenge} = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const config = {siteId:2, apiBase:'/api/v1/public'};
const challenge = {challenge:'signed-fictional-challenge', csrf_token:'a'.repeat(64), expires_in:1800, minimum_delay:2,max_message_length:10000,retention_days:90};
const payload = {name:'Personne fictive', first_name:'Exemple',email:'person@example.test',phone:'',organization:'',title:'Demande de test',message:'Message fictif reçu par le back-office.'};
const json = (body, status=200, headers={}) => new Response(JSON.stringify(body), {status,headers:{'Content-Type':'application/json',...headers}});
function setup(handler) {
  let now = 100000;
  const calls = [];
  const client = new ContactClient(config, async (url, options) => {
    calls.push({url,options});
    return url.endsWith('/challenge/') ? json(challenge) : handler(url,options,calls);
  }, () => now);
  return {client,calls,advance:milliseconds => { now+=milliseconds; }};
}
test('challenge validates expiry, csrf, limits and scalar structure', () => {
  assert.equal(validChallenge(challenge), true);
  for (const value of [null,[],{...challenge,expires_in:undefined},{...challenge,minimum_delay:0},{...challenge,csrf_token:'bad'},{...challenge,max_message_length:50001},{...challenge,retention_days:0}]) assert.equal(validChallenge(value),false);
});
test('browser submits on the same origin with csrf and no bearer credential', async () => {
  const {client,calls,advance} = setup(() => json({status:'accepted'},202));
  await client.prepare(); advance(2000); await client.submit(payload);
  assert.equal(calls[0].url,'/api/v1/public/sites/2/contacts/challenge/');
  const sent=calls[1];
  assert.equal(sent.url,'/api/v1/public/sites/2/contacts/');
  assert.equal(sent.options.credentials,'same-origin');
  assert.equal(sent.options.headers['X-CSRFToken'],challenge.csrf_token);
  assert.equal(sent.options.headers.Authorization,undefined);
  assert.deepEqual(JSON.parse(sent.options.body), {...payload,challenge:challenge.challenge,website:''});
});
test('early and expired first submissions never reach the server', async () => {
  const {client,calls,advance} = setup(() => json({status:'accepted'},202));
  await client.prepare();
  await assert.rejects(client.submit(payload), /délai/);
  advance(1800001);
  await assert.rejects(client.submit(payload), error => error instanceof ContactError && error.refresh);
  assert.equal(calls.length,1);
});
test('lost response retries the identical payload and challenge even if the editor changes', async () => {
  let attempt=0;
  const {client,calls,advance} = setup(() => { if (!attempt++) throw new TypeError('network'); return json({status:'accepted'},202); });
  await client.prepare(); advance(2000);
  await assert.rejects(client.submit(payload), error => error.uncertain);
  await assert.rejects(client.prepare(), error => error.uncertain);
  await client.submit({...payload,message:'Un autre contenu ne doit pas être envoyé'});
  assert.equal(calls[1].options.body,calls[2].options.body);
});
test('malformed success and server failure never show a false confirmation', async () => {
  for (const response of [json({status:'other'},202),new Response('maintenance',{status:202}),json({error:'failure'},503)]) {
    const {client,advance} = setup(() => response);
    await client.prepare(); advance(2000);
    await assert.rejects(client.submit(payload), error => error.uncertain);
  }
});
test('definite validation failure allows correction while malformed errors stay bounded', async () => {
  let attempt=0;
  const {client,calls,advance} = setup(() => attempt++ ? json({status:'accepted'},202) : json({error:'validation_failed',fields:{email:[{message:'Courriel invalide'}],message:'bad',private_field:[{message:'hidden'}]}},400));
  await client.prepare(); advance(2000);
  await assert.rejects(client.submit(payload), error => {
    assert.deepEqual(error.fields,{email:[{message:'Courriel invalide'}]});
    return !error.uncertain;
  });
  await client.submit({...payload,email:'corrected@example.test'});
  assert.equal(JSON.parse(calls[2].options.body).email,'corrected@example.test');
});
test('quota delay prevents additional requests and is bounded without Retry-After', async () => {
  const {client,calls,advance} = setup(() => json({error:'quota_exceeded'},429,{'Retry-After':'60'}));
  await client.prepare(); advance(2000);
  await assert.rejects(client.submit(payload), /demandes/);
  await assert.rejects(client.submit(payload), /délai/);
  await assert.rejects(client.prepare(), /délai/);
  assert.equal(calls.length,2);
  advance(60000);
  await assert.rejects(client.submit(payload), /demandes/);
  const fallback=setup(() => new Response('rate limit',{status:429}));
  await fallback.client.prepare();fallback.advance(2000);
  await assert.rejects(fallback.client.submit(payload));
  fallback.advance(4999);await assert.rejects(fallback.client.submit(payload), /délai/);
});
test('expired protection after an uncertain send keeps the original attempt until explicit reset', async () => {
  let attempt=0;
  const {client,advance} = setup(() => { if (!attempt++) throw new TypeError('network'); return json({error:'invalid_challenge'},400); });
  await client.prepare();advance(2000);
  await assert.rejects(client.submit(payload), error => error.uncertain);
  advance(1800000);
  await assert.rejects(client.submit(payload), error => error.uncertain && error.refresh);
  await assert.rejects(client.prepare(), error => error.uncertain);
  client.reset(); await client.prepare();
});
test('conflicts, csrf and disabled sites require a fresh form and unsafe bases are refused', async () => {
  for (const status of [403,404,409]) {
    const {client,advance} = setup(() => new Response('failure',{status}));
    await client.prepare();advance(2000);
    await assert.rejects(client.submit(payload), error => error.refresh && !error.uncertain);
  }
  for (const changes of [{siteId:1},{apiBase:'https://other.example.test'},{siteId:'2'}]) assert.throws(() => new ContactClient({...config,...changes}));
});

test('typed form carries only configured answers and keeps its exact retry', async () => {
  const definition = {code:'devis',label:'Demande de devis',version:'2026-10-10T08:00:00+00:00',questions:[{key:'question_01',label:'Lieu',required:true}]};
  const typed = {...challenge,form:definition,forms:[{code:'devis',label:'Demande de devis'}]};
  assert.equal(validChallenge(typed),true);
  let now=100000, attempts=0;
  const calls=[];
  const client=new ContactClient(config,async (url,options) => {
    calls.push({url,options});
    if (url.includes('/challenge/')) return json(typed);
    if (!attempts++) throw new TypeError('network');
    return json({status:'accepted'},202);
  },()=>now);
  await client.prepare(undefined,'devis'); now+=2000;
  await assert.rejects(client.submit({...payload,question_01:'Studio',question_02:'Non configurée'}), error=>error.uncertain);
  await client.submit({...payload,question_01:'Autre lieu'});
  assert.equal(calls[0].url,'/api/v1/public/sites/2/contacts/challenge/?type=devis');
  assert.equal(calls[1].options.body,calls[2].options.body);
  const sent=JSON.parse(calls[1].options.body);
  assert.equal(sent.form_type,'devis'); assert.equal(sent.form_version,definition.version);
  assert.equal(sent.question_01,'Studio'); assert.equal(sent.question_02,undefined);
});
test('malformed or duplicate question definitions are refused', () => {
  const question={key:'question_01',label:'Lieu',required:true};
  const definition={code:'devis',label:'Devis',version:'revision',questions:[question]};
  for (const questions of [[question,question],[{...question,key:'question_11'}],[{...question,required:'yes'}]]) {
    assert.equal(validChallenge({...challenge,form:{...definition,questions},forms:[{code:'devis',label:'Devis'}]}),false);
  }
});
test('a changed schema requests a fresh form and exposes question errors', async () => {
  const {client,advance}=setup(()=>json({error:'validation_failed',fields:{form_version:[{message:'Formulaire modifié'}],question_01:[{message:'Réponse requise'}]}},400));
  await client.prepare();advance(2000);
  await assert.rejects(client.submit(payload),error=>error.refresh && !error.uncertain && error.fields.question_01[0].message==='Réponse requise');
});
