import assert from 'node:assert/strict';
import test from 'node:test';
import { spokenDestination } from '../dist/services/journey-context.js';
import { AICommentary } from '../dist/services/ai-commentary.js';
import { buildStoryPrompt } from '../dist/services/story-brief.js';
const event={line:'42',direction:'outbound',timestamp:'2026-09-25T19:12:00Z',eventType:'delay',delayMinutes:8,lastStopName:'Two Mile Hill',
 collectorTripId:'matched',collectorStopSequence:5,journeyContext:{tripId:'matched',timingPointNumber:6,totalStops:12,origin:'Old Market',destination:'Temple Meads Stn - B'}};
const ai=Object.create(AICommentary.prototype); ai.appState={recentPosts:[]};
const issues=(post,e=event)=>ai.prepareWriterCandidate({post,hookUsed:false},{event:e},null).issues;
test('matched destination is an alternative marker without weakening opposite-direction rejection',()=>{
 assert.equal(spokenDestination(event),'Temple Meads Station');
 assert.match(buildStoryPrompt(event,event.timestamp,null,[]),/"towards":"Temple Meads Station"/);
 assert.deepEqual(issues('The 42 towards Temple Meads Station was eight minutes late at Two Mile Hill.'),[]);
 assert.deepEqual(issues('The outbound 42 was eight minutes late at Two Mile Hill.'),[]);
 assert.ok(issues('The inbound 42 towards Temple Meads Station was eight minutes late at Two Mile Hill.').length);
 assert.ok(issues('The outbound 42 towards Old Market was eight minutes late at Two Mile Hill.').length);
 assert.ok(issues('The outbound 42 towards Somewhere Else was eight minutes late at Two Mile Hill.').length);
 assert.ok(issues('The 42 was eight minutes late at Two Mile Hill.').length);
});
test('loops, generic termini, final stops and uncertain matches retain the direction word',()=>{
 for(const e of [{...event,lowConfidence:true},{...event,collectorTripId:'other'}, {...event,collectorTripId:undefined},
 {...event,journeyContext:{...event.journeyContext,origin:'Temple Meads Stn - B'}},
 {...event,journeyContext:{...event.journeyContext,destination:'Bus Station'}},
 {...event,journeyContext:{...event.journeyContext,timingPointNumber:12}}]){
 assert.equal(spokenDestination(e),null);
 assert.ok(issues('The 42 towards Temple Meads Station was eight minutes late at Two Mile Hill.',e).length);
 assert.deepEqual(issues('The outbound 42 was eight minutes late at Two Mile Hill.',e),[]);
 }
 const short={...event,journeyContext:{...event.journeyContext,destination:'Kingswood High Street'}};
 assert.equal(spokenDestination(short),'Kingswood High Street');
 assert.ok(issues('The outbound 42 towards Temple Meads Station was eight minutes late at Two Mile Hill.',short).length);
});