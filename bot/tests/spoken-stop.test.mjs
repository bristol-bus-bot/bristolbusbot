import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import { spokenStopName } from '../dist/utils/stop-name-cleaner.js';
import { buildStoryPrompt } from '../dist/services/story-brief.js';
import { validateCommentaryCandidate } from '../dist/services/editorial-commentary-policy.js';
const pairs = [
 ['Parson Street Stn','Parson Street Station'], ['Temple Meads Stn','Temple Meads Station'],
 ['Stapleton Road Stn','Stapleton Road Station'], ['Bristol Parkway Stn','Bristol Parkway Station'],
 ['Charlton Rd Jct','Charlton Road Jct'], ['Briery Leaze Rd','Briery Leaze Road'],
 ['Parsonage Rd','Parsonage Road'], ['Naish Rd NW','Naish Road NW'],
 ...['The Centre','Wyndham Crescent','Keynsham Cemetery','High Street - Top','Eastfield Avenue - West',
 'Eastfield Avenue - Top','RUH - A&E','Bloomfield Drive - Top','Cotswold Road - East',
 'Catherine Way - Bottom','Hurn Lane - West','Wych Elm Road'].map(n=>[n,n])
];
test('spoken labels preserve twenty real names and expand only unambiguous words',()=>{
 const names = new Set(Object.values(JSON.parse(fs.readFileSync(new URL('../data/stop_localities.json',import.meta.url)))).map(s=>s.stop_name));
 for(const [raw,spoken] of pairs){ assert.ok(names.has(raw),raw); assert.equal(spokenStopName(raw),spoken); }
});
test('strip documented stand suffixes without deleting meaningful geography',()=>{
 for(const [raw,spoken] of [['Baldwin Street - C13','Baldwin Street'],['Bath Abbey - Ce','Bath Abbey'],
 ["St James's Parade - Wy","St James's Parade"],['Parson Street Stn - B','Parson Street Station'],
 ['Marine Parade - Stop MM','Marine Parade'],['Utd Reform Church','United Reform Church'],['Opp Library','opposite Library'],['Nr School','near School']]) assert.equal(spokenStopName(raw),spoken);
 assert.equal(spokenStopName('Ce Road'),'Ce Road');
});
test('spoken labels preserve raw identity and are accepted as factual locations',()=>{
 const base={line:'42',direction:'outbound',eventType:'delay',delayMinutes:8,timestamp:'2026-09-25T19:12:00Z',lastStopName:'Parson Street Stn - B',lastStopCode:'stop-b'};
 const prompt=buildStoryPrompt(base,base.timestamp,null,[]);
 assert.match(prompt,/"location":"Parson Street Station"/);
 assert.match(prompt,/"rawStopName":"Parson Street Stn - B"/);
 assert.match(prompt,/"stopIdentity":"stop-b"/);
 assert.deepEqual(validateCommentaryCandidate('The outbound 42 was eight minutes late at Parson Street Station.',base,null,false),[]);
 assert.equal(spokenStopName('Parson Street Stn - A'),spokenStopName(base.lastStopName));
 assert.notEqual(buildStoryPrompt({...base,lastStopCode:'stop-a',lastStopName:'Parson Street Stn - A'},base.timestamp,null,[]),prompt);
});