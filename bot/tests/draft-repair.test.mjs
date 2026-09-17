import assert from 'node:assert/strict';
import test from 'node:test';
import {AICommentary, GeminiRequestError} from '../dist/services/ai-commentary.js';

const event={line:'42',direction:'outbound',operatorRef:'FBRI',vehicleRef:'test',
  timestamp:new Date().toISOString(),eventType:'delay',delayMinutes:8,lastStopName:'Two Mile Hill',
  busDetails:{livery:{name:'Olympia'}}};
const original='The outbound 42 arrived eight minutes late at Two Mile Hill. Olympia paintwork, Olympic patience.';
const corrected='The outbound 42 was eight minutes late at Two Mile Hill. Olympia paintwork, Olympic patience.';
const output=post=>JSON.stringify({post,hook_used:false});
const pass=JSON.stringify({verdict:'PASS',reasons:[]});
const fail=JSON.stringify({verdict:'FAIL',reasons:['Arrival is not established; describe the timing observation.']});
function fixture(responder){
  const ai=Object.create(AICommentary.prototype);
  ai.appState={recentPosts:[]};
  ai.subjectHistory={publishedCount:1,history:[]};
  ai.pendingPublications=new Map();
  ai.aiConfig={model:'test',timeout:75000};
  ai.thinkingLevels={draft:{normal:'LOW',editorial:'MEDIUM'},verifier:'LOW'};
  const calls=[];
  ai.requestGeminiStructured=async (...args)=>{
    calls.push(args);
    return responder(calls.length,...args);
  };
  return {ai,calls,run:()=>ai.callSingleWriterGemini({event},0,null)};
}

test('a rejected draft gets one targeted correction and exact final-brief verification',async()=>{
  const {ai,calls,run}=fixture(n=>[output(original),fail,output(corrected),pass][n-1]);
  const result=await run();
  assert.equal(result.text,corrected);
  assert.equal(calls.length,4);
  assert.match(calls[2][0],/Arrival is not established/);
  assert.match(calls[2][0],/Preserve its subject, voice and humour/);
  assert.ok(calls[2][0].includes(original));
  assert.match(calls[2][0],/"livery":"Olympia"/);
  const verified=JSON.parse(calls[3][0].split('\n').find(l=>l.startsWith('{"brief":')));
  assert.equal(verified.brief,calls[2][0]);
  assert.equal(verified.post,corrected);
  assert.equal(ai.pendingPublications.size,1);
  assert.equal(ai.pendingPublications.get(corrected).subject.kind,'livery');
  assert.equal(ai.subjectHistory.publishedCount,1,'drafts do not spend publication history');
});

test('first-pass success stays at two calls',async()=>{
  const {calls,run}=fixture(n=>n===1?output(corrected):pass);
  assert.ok(await run());
  assert.equal(calls.length,2);
});

test('mechanical and factual failures share the same single correction allowance',async()=>{
  const {ai,calls,run}=fixture(n=>[output('Missing evidence.'),output(original),fail][n-1]);
  assert.equal(await run(),null);
  assert.equal(calls.length,3);
  assert.equal(ai.pendingPublications.size,0);
});

test('an invalid correction never reaches publication or an unnecessary second checker',async()=>{
  const {ai,calls,run}=fixture(n=>[output(original),fail,output('Missing evidence.')][n-1]);
  assert.equal(await run(),null);
  assert.equal(calls.length,3);
  assert.equal(ai.pendingPublications.size,0);
});

test('a temporary provider failure retries only the failed request',async()=>{
  const {calls,run}=fixture(n=>{
    if(n===2)throw new GeminiRequestError('busy',503,false);
    return n===1?output(corrected):pass;
  });
  assert.ok(await run());
  assert.equal(calls.length,3);
  assert.equal(calls[1][0],calls[2][0]);
  assert.ok(calls[2][4]<=75000);
});

test('transient retry allowance is shared by writer and checker',async()=>{
  const {ai,calls,run}=fixture(n=>{
    if(n!==2)throw new GeminiRequestError('busy',503,false);
    return output(corrected);
  });
  assert.equal(await run(),null);
  assert.equal(calls.length,3);
  assert.equal(calls[0][0],calls[1][0]);
  assert.equal(ai.pendingPublications.size,0);
});

test('quota, authentication and uncertain network failures are not retried',async()=>{
  for(const error of [new GeminiRequestError('quota',503,true),new GeminiRequestError('quota',429,true),
    new GeminiRequestError('auth',401,false),new Error('network timeout')]){
    const {calls,run}=fixture(()=>{throw error;});
    assert.equal(await run(),null);
    assert.equal(calls.length,1);
  }
});

test('the story time budget prevents a late retry and caps request timeouts',async t=>{
  const initial=Date.now();
  let elapsed=0;
  t.mock.method(Date,'now',()=>initial+elapsed);
  const {calls,run}=fixture(n=>{
    if(n===1){elapsed=87000;return output(original);}
    throw new GeminiRequestError('busy',503,false);
  });
  assert.equal(await run(),null);
  assert.equal(calls.length,2);
  assert.equal(calls[1][4],3000);
});
