import test from 'node:test';
import assert from 'node:assert/strict';
import { dropUrl, blockPosition } from '../src/planning.js';
const origin = 'http://127.0.0.1:8010';
const handle = {href: '/sites/2/planning/3/deplacer/', allDay: 'false', version: '7'};
const target = {kind: 'timed', date: '2026-10-07', hour: '16', timezone: 'Europe/Paris'};
test('drop opens a confirmation with the original version and explicit target timezone', () => {
  const url = new URL(dropUrl(handle, target, origin));
  assert.equal(url.pathname, '/sites/2/planning/3/deplacer/');
  assert.equal(url.searchParams.get('date'), '2026-10-07');
  assert.equal(url.searchParams.get('hour'), '16');
  assert.equal(url.searchParams.get('version'), '7');
  assert.equal(url.searchParams.get('timezone'), 'Europe/Paris');
});
test('hourly blocks preserve exact minute offsets and proportional duration', () => {
  const short = blockPosition({offset: 615, duration: 30, lane: 0, lanes: 1}, 1440);
  const long = blockPosition({offset: 615, duration: 90, lane: 0, lanes: 1}, 1440);
  assert.equal(short.top, '615px');
  assert.equal(short.height, '30px');
  assert.equal(long.height, '90px');
});
test('overlapping blocks get separate equal-width lanes', () => {
  const first = blockPosition({offset: 600, duration: 120, lane: 0, lanes: 2}, 1440);
  const second = blockPosition({offset: 630, duration: 60, lane: 1, lanes: 2}, 1440);
  assert.equal(first.width, second.width);
  assert.notEqual(first.left, second.left);
});
test('short markers and last-minute events stay inside the day', () => {
  assert.equal(blockPosition({offset: 600, duration: 1, lane: 0, lanes: 1}, 1440).height, '6px');
  assert.equal(blockPosition({offset: 1439, duration: 1, lane: 0, lanes: 1}, 1440).height, '1px');
  assert.equal(blockPosition({offset: NaN, duration: 60, lane: 0, lanes: 1}, 1440), null);
});
test('external origins cannot be used as a move destination', () => assert.equal(dropUrl({...handle, href:'https://example.test/move/'}, target, origin), null));
test('drop never silently converts a timed event to an all-day event', () => assert.equal(dropUrl(handle, {...target,kind:'all-day'}, origin), null));
test('all-day moves only choose a day and never a time slot', () => {
  const allDay = {...handle, allDay:'true'};
  assert.equal(dropUrl(allDay, target, origin), null);
  const url = new URL(dropUrl(allDay, {...target,kind:'day'}, origin));
  assert.equal(url.searchParams.has('hour'), false);
});
