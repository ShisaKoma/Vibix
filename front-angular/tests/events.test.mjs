import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';
const source = await readFile(new URL('../src/app/pages/events/event-contract.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { validEvent } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const event = { id: 1, title: 'Concert fictif', kind: 'concert', description: 'Description', location: 'Salle', link: 'https://example.test/concert', starts_at: '2026-10-03T18:00:00+00:00', ends_at: '2026-10-03T20:00:00+00:00', timezone: 'Europe/Paris', all_day: false, resources: [{id: 1, name: 'Scène'}] };
test('public event accepts explicit UTC instants and a valid event timezone', () => assert.equal(validEvent(event), true));
test('unsafe public links never reach an event card', () => {
  for (const link of ['javascript:alert(1)', 'data:text/html,test', 'file:///tmp/test', '//example.test']) assert.equal(validEvent({...event, link}), false);
});
test('ambiguous timestamps, broken timezones and reversed periods are rejected', () => {
  for (const changes of [{starts_at: '2026-10-03T18:00:00'}, {ends_at: event.starts_at}, {ends_at: 'bad'}, {timezone: 'Unknown/Zone'}, {timezone: null}]) assert.equal(validEvent({...event, ...changes}), false);
});
test('invalid identity and malformed resources are rejected', () => {
  for (const changes of [{id: -1}, {kind: 'private'}, {resources: [{id: 0, name: 'Bad'}]}, {resources: null}]) assert.equal(validEvent({...event, ...changes}), false);
});
