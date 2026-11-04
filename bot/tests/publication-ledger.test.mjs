import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { PublicationLedger } from '../dist/services/publication-ledger.js';

test('confirmed publications survive restart, retain reasons, and deduplicate by URI',t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'bbb-ledger-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const file=path.join(dir,'ledger.json'),now=Date.now(),a=new PublicationLedger(file);
 a.record('Draft',undefined);assert.equal(a.records.length,0);
 a.record('Published','at://test/1',{kind:'wider',key:'wider:fact',context:{}},['fact'],undefined,now);
 a.record('Duplicate','at://test/1');
 a.record('Reserve','at://test/2',undefined,[],'no_eligible_observation',now);
 const b=new PublicationLedger(file);assert.equal(b.records.length,2);
 assert.deepEqual(b.recentTexts(),['Reserve','Published']);assert.deepEqual(b.records[0].factIds,['fact']);
 assert.equal(b.records[1].fallbackReason,'no_eligible_observation');assert.equal(b.report(now).fallbackShare,0.5);
});

test('only comparable aged posts are sampled, count-only data persists, missing is not zero',async()=>{
 const l=new PublicationLedger(),now=Date.now(),hour=3600000;
 for(const [id,age] of [['fresh',1],['due',25],['missing',25],['old',27]])l.record(id,'at://test/'+id,undefined,[],undefined,now-age*hour);
 let requests=0;
 await l.collectMetrics(async uris=>{requests++;assert.deepEqual(uris,['at://test/due','at://test/missing']);return [{uri:'at://test/due',likeCount:4,repostCount:1,likers:['not stored']}];},now);
 assert.equal(requests,1);assert.equal(l.records.find(r=>r.text==='due').metrics.likes,4);
 assert.equal(l.records.find(r=>r.text==='missing').metrics,undefined);
 assert.doesNotMatch(JSON.stringify(l.records),/likers|not stored/);
 const report=l.report(now);assert.equal(Object.values(report.groups)[0].sampled,1);
});

test('unwritable or corrupt ledger cannot turn successful publication into a retry',t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'bbb-ledger-bad-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const file=path.join(dir,'bad');fs.writeFileSync(file,'not json');
 assert.equal(new PublicationLedger(file).records.length,0);
 const l=new PublicationLedger(path.join(file,'nested'));assert.doesNotThrow(()=>l.record('Already posted','at://test/1'));assert.equal(l.records.length,1);
});

test('the report lists the latest posts for the health check and is saved after each post',t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'bbb-ledger-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const file=path.join(dir,'ledger.json'),now=Date.now(),l=new PublicationLedger(file);
 for(let i=0;i<8;i++)l.record('Post '+i,'at://test/'+i,i===7?{kind:'service',key:'',context:{}}:undefined,[],i===7?undefined:'writer_rejected_or_failed',now-(8-i)*60000);
 const saved=JSON.parse(fs.readFileSync(file+'.report.json','utf8'));
 assert.equal(saved.recent.length,6);
 assert.deepEqual(saved.recent.at(-1),{publishedAt:new Date(now-60000).toISOString(),fallback:false});
 assert.deepEqual(saved.recent[0],{publishedAt:new Date(now-6*60000).toISOString(),fallback:true,fallbackReason:'writer_rejected_or_failed'});
 assert.doesNotMatch(JSON.stringify(saved.recent),/Post /);
});
