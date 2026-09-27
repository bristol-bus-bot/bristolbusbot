import test from 'node:test';
import assert from 'node:assert/strict';
import {reservePost,observationPost,EDITORIAL_RESERVE} from '../dist/services/posting-fallback.js';
import {validateCommentaryCandidate} from '../dist/services/editorial-commentary-policy.js';
test('reserve lines do not repeat until the seven-day pool is exhausted; reuse is oldest first',()=>{
 const history=[],now=Date.now();
 assert.ok(EDITORIAL_RESERVE.length>=40);assert.equal(new Set(EDITORIAL_RESERVE).size,EDITORIAL_RESERVE.length);
 for(let i=0;i<EDITORIAL_RESERVE.length;i++){
  const text=reservePost(now+i*1200000,history);assert.ok(text.length<=300);assert.ok(!history.some(r=>r.text===text));
  history.push({text,publishedAt:new Date(now+i*1200000).toISOString()});
 }
 assert.equal(reservePost(now+history.length*1200000,history),history[0].text);
 assert.equal(reservePost(now+8*86400000,history),EDITORIAL_RESERVE[0]);
});
test('all eight observation shapes preserve route, direction, spoken stop and timing',()=>{
 const event={line:'42',direction:'outbound',timestamp:new Date().toISOString(),eventType:'delay',delayMinutes:8,lastStopName:'The Haymarket - B10'};
 const posts=[];
 for(let i=0;i<8;i++){
  const text=observationPost(event,posts.slice().reverse());assert.ok(!posts.includes(text));
  assert.deepEqual(validateCommentaryCandidate(text,event,null,false),[]);assert.doesNotMatch(text,/B10/);posts.push(text);
 }
});
