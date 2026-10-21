import test from 'node:test';
import assert from 'node:assert/strict';
import {reservePost,observationPost,EDITORIAL_RESERVE} from '../dist/services/posting-fallback.js';
import {validateCommentaryCandidate} from '../dist/services/editorial-commentary-policy.js';
test('reserve lines do not repeat until the seven-day pool is exhausted; reuse is oldest first',()=>{
 const history=[],now=Date.parse('2026-10-21T08:00:00Z'); // 09:00 BST, ends before midnight
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

import {NIGHT_RESERVE} from '../dist/services/posting-fallback.js';
import {isSmallHours} from '../dist/services/story-subject.js';
import {selectStory} from '../dist/services/story-selection.js';
import {buildStoryPrompt,NIGHT_REGISTER} from '../dist/services/story-brief.js';
import {jargonIssues} from '../dist/services/editorial-commentary-policy.js';

test('the small hours run from midnight to 5am UK time, through the clock change',()=>{
 assert.equal(isSmallHours('2026-10-21T23:30:00Z'),true);   // 00:30 BST
 assert.equal(isSmallHours('2026-10-22T03:59:00Z'),true);   // 04:59 BST
 assert.equal(isSmallHours('2026-10-22T04:00:00Z'),false);  // 05:00 BST
 assert.equal(isSmallHours('2026-12-01T04:30:00Z'),true);   // 04:30 GMT
 assert.equal(isSmallHours('2026-10-21T22:30:00Z'),false);  // 23:30 BST
});

test('overnight reserve posts use the night lines first, then the usual pool',()=>{
 const night=Date.parse('2026-10-21T23:30:00Z'), history=[];
 assert.equal(new Set(NIGHT_RESERVE).size,NIGHT_RESERVE.length);
 for(let i=0;i<NIGHT_RESERVE.length;i++){
  const text=reservePost(night+i*60000,history);
  assert.ok(NIGHT_RESERVE.includes(text)); assert.ok(text.length<=300);
  assert.equal(jargonIssues(text).length,0,text);
  history.push({text,publishedAt:new Date(night+i*60000).toISOString()});
 }
 assert.ok(EDITORIAL_RESERVE.includes(reservePost(night+NIGHT_RESERVE.length*60000,history)));
 assert.ok(EDITORIAL_RESERVE.includes(reservePost(Date.parse('2026-10-21T12:00:00Z'))),'daytime never uses night lines');
});

test('night posts get the night register; daytime posts do not',()=>{
 const base={line:'72',direction:'outbound',eventType:'delay',delayMinutes:6,lastStopName:'Temple Meads',lastStopCode:'s',operatorRef:'FBRI'};
 assert.ok(buildStoryPrompt({...base,timestamp:'2026-10-21T23:40:00Z'},'',null,[]).includes(NIGHT_REGISTER));
 assert.ok(!buildStoryPrompt({...base,timestamp:'2026-10-21T12:40:00Z'},'',null,[]).includes(NIGHT_REGISTER));
});

test('from 4am the early buses at their first stop are preferred; before then nothing changes',()=>{
 const a={vehicleRef:'a',operatorRef:'FBRI',line:'1',eventType:'delay',delayMinutes:15,journeyContext:{timingPointNumber:9,totalStops:30}};
 const b={vehicleRef:'b',operatorRef:'FBRI',line:'2',eventType:'punctual',delayMinutes:0,journeyContext:{timingPointNumber:1,totalStops:30}};
 const at=iso=>Date.parse(iso);
 assert.equal(selectStory([a,b],[],()=>0,at('2026-10-22T03:20:00Z')).vehicleRef,'b');   // 04:20 BST
 assert.equal(selectStory([a],[],()=>0,at('2026-10-22T03:20:00Z')).vehicleRef,'a');     // none at a first stop
 assert.equal(selectStory([a,b],[],()=>0,at('2026-10-22T01:00:00Z')).vehicleRef,'a');   // 02:00 BST: unchanged
});
