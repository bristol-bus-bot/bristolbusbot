import test from 'node:test';
import assert from 'node:assert/strict';
import {weatherDescription} from '../dist/services/weather-description.js';
import {depotGeography} from '../dist/services/depot-geography.js';
import {availableSubjects} from '../dist/services/story-subject.js';
const weather=(temp,sky,extra='')=>`OpenWeather area observation near Bath at 2026-09-27 12:00 BST: ${temp}°C, with ${sky}${extra}`;
test('weather uses observed qualitative conditions without routine numbers',()=>{
 assert.equal(weatherDescription(weather(16,'overcast clouds')),null);
 assert.match(weatherDescription(weather(2,'clear sky')),/cold, clear skies \(2°C\)/);
 assert.match(weatherDescription(weather(26,'clear sky')),/hot/);
 assert.match(weatherDescription(weather(16,'light rain')),/light rain/);
 assert.match(weatherDescription(weather(9,'fog')),/fog/);
 assert.doesNotMatch(weatherDescription(weather(16,'clear sky',', visibility 0.3km, humidity 96%, wind S 2mph')),/fog|km|humidity|mph|16°C/);
 assert.match(weatherDescription(weather(16,'cloudy',', wind S 30mph')),/strong winds/);
 assert.equal(weatherDescription('rain somewhere'),null);
});
test('depot distance requires exact reviewed yard and stop identity and never implies unusual working',()=>{
 const event={lastStopCode:'stop',busDetails:{garage:{name:'Lawrence Hill'}}};
 const far=depotGeography(event,{stop_code:'stop',lat:51.35,lon:-2.98});
 assert.ok(far.distanceKm>=15);assert.match(far.scope,/Not distance travelled/);
 assert.equal(depotGeography(event,{stop_code:'stop',lat:51.46,lon:-2.56}),undefined);
 assert.equal(depotGeography(event,{stop_code:'other',lat:51.35,lon:-2.98}),undefined);
 assert.equal(depotGeography({...event,lowConfidence:true},{stop_code:'stop',lat:51.35,lon:-2.98}),undefined);
 assert.equal(depotGeography({...event,busDetails:{garage:{name:'Unknown'}}},{stop_code:'stop',lat:51.35,lon:-2.98}),undefined);
 assert.equal(availableSubjects(event).some(s=>s.kind==='depot'),false);
 assert.equal(availableSubjects({...event,depotContext:far}).some(s=>s.kind==='depot'),true);
});


test('fleet garage names reach their reviewed yard or garage town, and the threshold is 8 km',()=>{
 // Fleet data says "Bath"; the reviewed yard is "Bath (Weston Island)".
 const bath={lastStopCode:'stop',operatorRef:'FBRI',busDetails:{garage:{name:'Bath'}}};
 const bristol=depotGeography(bath,{stop_code:'stop',lat:51.4545,lon:-2.5879});
 assert.ok(bristol && bristol.distanceKm>=15 && bristol.name==='Bath');
 assert.match(bristol.scope,/reviewed yard centre/);
 assert.equal(depotGeography(bath,{stop_code:'stop',lat:51.38,lon:-2.36}),undefined);
 // About 10 km from Lawrence Hill now qualifies; under 8 km does not.
 const lh={lastStopCode:'stop',busDetails:{garage:{name:'Lawrence Hill'}}};
 assert.ok(depotGeography(lh,{stop_code:'stop',lat:51.5390,lon:-2.6370}));
 assert.equal(depotGeography(lh,{stop_code:'stop',lat:51.4800,lon:-2.6000}),undefined);
 // A Stagecoach West bus from Gloucester in Bristol: town-level, said so in scope.
 const glos={lastStopCode:'stop',operatorRef:'SCGL',busDetails:{garage:{name:'Gloucester'}}};
 const far=depotGeography(glos,{stop_code:'stop',lat:51.4545,lon:-2.5879});
 assert.ok(far.distanceKm>40); assert.match(far.scope,/Gloucester, where the garage is/);
 // Same garage name under another operator has no mapping.
 assert.equal(depotGeography({...glos,operatorRef:'FBRI'},{stop_code:'stop',lat:51.4545,lon:-2.5879}),undefined);
 assert.equal(availableSubjects({...glos,depotContext:far}).some(s=>s.kind==='depot'),true);
});
