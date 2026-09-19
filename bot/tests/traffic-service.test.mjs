import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {TrafficService,segmentDistance} from '../dist/services/traffic-service.js';
import {chooseSubject} from '../dist/services/story-subject.js';
import {buildStoryPrompt} from '../dist/services/story-brief.js';
import {SubjectHistory} from '../dist/services/subject-history.js';
import {AICommentary} from '../dist/services/ai-commentary.js';

const point={latitude:51.4545,longitude:-2.5879};
const event=()=>({location:point,timestamp:new Date().toISOString(),line:'43',direction:'inbound',operatorRef:'FBRI',vehicleRef:'test',
  eventType:'delay',delayMinutes:5,lastStopName:'Broad Quay',lastStopCode:'test'});
const flow=()=>({currentSpeed:8,freeFlowSpeed:25,confidence:0.95,roadClosure:false,
  coordinates:{coordinate:[{latitude:51.45,longitude:-2.5879},{latitude:51.46,longitude:-2.5879}]}});
const response=(data=flow(),headers={})=>({ok:true,headers:new Map([['date',new Date().toUTCString()],...Object.entries(headers)]),json:async()=>({flowSegmentData:data})});
function setup(t,fetcher=async()=>response()){
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'bbb-traffic-'));
  t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const config={enabled:true,apiKey:'test-key',usagePath:path.join(dir,'usage.json')};
  return {config,service:new TrafficService(config,fetcher)};
}

test('one high-confidence nearby segment becomes plain-language context, without cached provider data',async t=>{
  const calls=[];
  const {service,config}=setup(t,async(url,options)=>{calls.push({url:new URL(url),options});return response();});
  const context=await service.getTraffic(event());
  assert.equal(context.condition,'moving slowly');
  assert.match(context.scope,/not matched to its road or direction/);
  assert.equal(context.source,'TomTom');
  assert.equal(calls[0].url.searchParams.get('point'),'51.4545,-2.5879');
  assert.equal(calls[0].url.searchParams.get('unit'),'mph');
  assert.deepEqual(calls[0].options,{timeoutMs:4000,retries:0});
  assert.equal(await service.getTraffic(event()),null,'minimum request spacing, no reused response');
  const usage=JSON.parse(fs.readFileSync(config.usagePath));
  assert.equal(usage.monthly,1);
  assert.doesNotMatch(fs.readFileSync(config.usagePath,'utf8'),/coordinates|currentSpeed|test-key/);
  assert.ok(segmentDistance(point,flow().coordinates.coordinate)<1,'middle of a long segment is near');
});

test('uncertain, distant, closed, stale and malformed responses cannot enter a prompt',async t=>{
  const cases=[
    [flow(),{date:new Date(Date.now()-300000).toUTCString()}], [flow(),{date:'invalid'}], [flow(),{age:'900'}],
    [{...flow(),confidence:0.79}], [{...flow(),confidence:2}], [{...flow(),roadClosure:true}],
    [{...flow(),currentSpeed:-1}], [{...flow(),freeFlowSpeed:0}], [{...flow(),currentSpeed:'8'}],
    [{...flow(),coordinates:{coordinate:[{latitude:51.45,longitude:-2.5},{latitude:51.46,longitude:-2.5}]}}],
    [{...flow(),coordinates:{coordinate:[point,{latitude:NaN,longitude:0}]}}],
  ];
  for(const [data,headers] of cases){
    const {service}=setup(t,async()=>response(data,headers));
    assert.equal(await service.getTraffic(event()),null);
  }
});

test('plain-language categories do not equate normal low road speed with congestion',async t=>{
  for(const [speed,baseline,expected] of [[10,10,'moving freely'],[7,10,'a little slower than on a clear road'],[4,10,'moving slowly'],[1,10,'moving very slowly']]){
    const {service}=setup(t,async()=>response({...flow(),currentSpeed:speed,freeFlowSpeed:baseline}));
    assert.equal((await service.getTraffic(event())).condition,expected);
  }
});

test('no key, no GPS, stale GPS, disabled traffic or an unrelated coordinate makes no request',async t=>{
  const {config}=setup(t);
  const forbidden=async()=>assert.fail('must not request');
  assert.equal(await new TrafficService({...config,apiKey:''},forbidden).getTraffic(event()),null);
  assert.equal(await new TrafficService({...config,enabled:false},forbidden).getTraffic(event()),null);
  for(const patch of [{location:undefined},{location:{latitude:0,longitude:0}},{location:{latitude:91,longitude:0}},
    {timestamp:new Date(Date.now()-120000).toISOString()},{timestamp:'invalid'}]){
    assert.equal(await new TrafficService(config,forbidden).getTraffic({...event(),...patch}),null);
  }
});

test('request counts survive restarts; failures spend budget, and corrupt state fails closed',async t=>{
  let calls=0;
  const {config,service}=setup(t,async()=>{calls++;throw new Error('provider unavailable');});
  assert.equal(await service.getTraffic(event()),null);
  assert.equal(JSON.parse(fs.readFileSync(config.usagePath)).monthly,1);
  assert.equal(await new TrafficService(config,async()=>{calls++;return response();}).getTraffic(event()),null);
  assert.equal(calls,1);
  const now=new Date().toISOString();
  for(const patch of [{daily:50,monthly:50},{daily:0,monthly:500}]){
    fs.writeFileSync(config.usagePath,JSON.stringify({day:now.slice(0,10),month:now.slice(0,7),lastRequest:0,...patch}));
    assert.equal(await new TrafficService(config,async()=>assert.fail('budget exceeded')).getTraffic(event()),null);
  }
  fs.writeFileSync(config.usagePath,'broken');
  assert.equal(await service.getTraffic(event()),null);
});

test('traffic cannot hold up posting when a response body hangs',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const {service}=setup(t,async()=>({...response(),json:()=>new Promise(()=>{})}));
  const pending=service.getTraffic(event());
  t.mock.timers.tick(5000);
  assert.equal(await pending,null);
});

test('traffic gets only its own turn, keeps evidence scoped, and survives publication-history reload',async t=>{
  const {service,config}=setup(t);
  const bus=event(), traffic=await service.getTraffic(bus);
  const chosen=chooseSubject(bus,null,null,7,[],[],traffic);
  assert.equal(chosen.kind,'traffic');
  assert.equal(chooseSubject(bus,null,null,0,[],[],traffic).kind,'service');
  assert.equal(chooseSubject(bus,null,null,1,[],[],traffic).kind,'service','not a fallback subject');
  assert.equal(chooseSubject(bus,null,null,7,[chosen],[],traffic).kind,'service');
  assert.equal(chooseSubject(bus,'Local weather: clear sky',null,7).kind,'weather','preserve weather turn when traffic is unavailable');
  assert.equal(chooseSubject(bus,null,null,7,[],[],{...traffic,checkedAt:'2000-01-01'}).kind,'service');
  const prompt=buildStoryPrompt(bus,bus.timestamp,null,[],[],null,chosen);
  assert.match(prompt,/"nearbyTraffic":/);
  assert.match(prompt,/"source":"local traffic reports"/);
  assert.doesNotMatch(prompt,/TomTom/i);
  assert.doesNotMatch(prompt,/currentSpeed|freeFlowSpeed|test-key/);
  assert.match(prompt,/not necessarily this bus's road or direction/);
  const history=new SubjectHistory(config.usagePath+'.subjects');
  history.record('A traffic post',chosen,'at://test/1');
  assert.equal(new SubjectHistory(config.usagePath+'.subjects').history[0].kind,'traffic');
});

test('the live context builder fetches traffic only on its scheduled turn and never substitutes a locality centre',async()=>{
  const ai=Object.create(AICommentary.prototype);
  ai.aiConfig={pipeline:'single'};
  ai.appState={getNetworkStatus:()=>({})};
  ai.weatherService={getCurrentWeather:async()=>null};
  const calls=[];
  ai.trafficService={getTraffic:async e=>{calls.push(e);return null;}};
  for(const count of [0,1,3,7]){
    ai.subjectHistory={publishedCount:count,history:[]};
    await ai.buildAIContext(event());
  }
  assert.equal(calls.length,1);
  assert.deepEqual(calls[0].location,point);
});

test('traffic drafts use natural local-report wording without naming the supplier',()=>{
  const ai=Object.create(AICommentary.prototype);
  const bus=event();
  const writer={post:'The inbound 43 was five minutes late at Broad Quay. Traffic nearby was moving slowly.',hookUsed:false};
  assert.equal(ai.prepareWriterCandidate(writer,{event:bus},null,{kind:'traffic'}).issues.length,0);
  assert.equal(ai.prepareWriterCandidate({...writer,post:'The inbound 43 was five minutes late at Broad Quay. Local traffic reports showed slow traffic nearby.'},{event:bus},null,{kind:'traffic'}).issues.length,0);
  assert.match(ai.prepareWriterCandidate({...writer,post:'The inbound 43 was five minutes late at Broad Quay. TomTom reported traffic moving slowly nearby.'},{event:bus},null,{kind:'traffic'}).issues.join(' '),/without naming the data supplier/);
  assert.equal(ai.prepareWriterCandidate(writer,{event:bus},null,{kind:'service'}).issues.length,0);
});

test('a new month resets usage but an unwritable ledger cannot cause uncounted requests',async t=>{
  const {config}=setup(t);
  fs.writeFileSync(config.usagePath,JSON.stringify({day:'2000-01-01',month:'2000-01',daily:50,monthly:500,lastRequest:0}));
  assert.ok(await new TrafficService(config,async()=>response()).getTraffic(event()));
  assert.equal(JSON.parse(fs.readFileSync(config.usagePath)).monthly,1);
  const broken={...config,usagePath:path.join(config.usagePath,'cannot-write')};
  assert.equal(await new TrafficService(broken,async()=>assert.fail('uncounted request')).getTraffic(event()),null);
});

test('the traffic subject and source reach the actual writer and exact final verifier',async t=>{
  const {service}=setup(t);
  const bus=event(),traffic=await service.getTraffic(bus);
  const ai=Object.create(AICommentary.prototype);
  ai.aiConfig={model:'test'};
  ai.appState={recentPosts:[]};
  ai.subjectHistory={publishedCount:7,history:[]};
  ai.pendingPublications=new Map();
  ai.thinkingLevels={draft:{normal:'LOW',editorial:'MEDIUM'},verifier:'LOW'};
  const prompts=[];
  ai.requestGeminiStructured=async prompt=>{
    prompts.push(prompt);
    return prompt.startsWith('Check facts')?JSON.stringify({verdict:'PASS',reasons:[]})
      :JSON.stringify({post:'The inbound 43 was five minutes late at Broad Quay. Local traffic reports showed slow traffic nearby.',hook_used:false});
  };
  const result=await ai.callSingleWriterGemini({event:bus,trafficContext:traffic},0,null);
  assert.equal(result.metadata.subject,'traffic');
  assert.match(prompts[0],/"nearbyTraffic":/);
  assert.match(prompts[1],/not a matched bus route or direction/);
  assert.equal(prompts.length,2,'traffic does not add an AI call');
});
