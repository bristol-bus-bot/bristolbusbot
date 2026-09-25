import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import { factualStoryIssues, openingIssues } from '../dist/services/story-brief.js';
import { validateCommentaryCandidate } from '../dist/services/editorial-commentary-policy.js';

const baseline = JSON.parse(fs.readFileSync(new URL('./fixtures/audition/baseline.json', import.meta.url)));
test('reject observed baseline movement leaks regardless of timing category', () => {
  for (const id of ['01', '04', '05', '10', '13', '21', '23', '29', '30']) {
    const row = baseline.results.find(r => r.id === `case-${id}`);
    assert.match(factualStoryIssues(row.post, row.evidence.event).join(' '), /movement/, row.id);
  }
});
test('retain factual location/status and do not mistake names for movement', () => {
  const event = baseline.results[0].evidence.event;
  assert.deepEqual(factualStoryIssues('The outbound U1N was on time at Transport Hub, its first scheduled stop.', event), []);
  assert.deepEqual(factualStoryIssues('The inbound 42 was on time at Passed Mill.', {...event,lastStopName:'Passed Mill'}), []);
});
test('clock openings use only the last five confirmed posts and allow a changed opening', () => {
  const post = 'At 20:12, the outbound U1N was on time at Transport Hub.';
  assert.equal(openingIssues(post, [post]).length, 0);
  assert.equal(openingIssues(post, [post, post]).length, 1);
  assert.equal(openingIssues('The outbound U1N was on time at Transport Hub.', [post,post]).length, 0);
  assert.equal(openingIssues(post, ['A.','B.','C.','D.','E.',post,post]).length, 0);
});
test('optional clock does not weaken route, stop, direction or timing validation', () => {
  const event=baseline.results[0].evidence.event;
  const post='The outbound U1N was on time at Transport Hub.';
  assert.deepEqual(validateCommentaryCandidate(post,event,null,false), []);
  assert.match(validateCommentaryCandidate('At 09:00, '+post,event,null,false).join(' '), /clock time/);
  for (const [from,to] of [['outbound','inbound'],['U1N','99'],['Transport Hub','Somewhere Else'],['on time','late']]) {
    assert.ok(validateCommentaryCandidate(post.replace(from,to),event,null,false).length, from);
  }
});