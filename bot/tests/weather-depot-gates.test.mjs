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
