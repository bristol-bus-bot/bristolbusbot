import assert from 'node:assert/strict';
import test from 'node:test';
import { cleanStopName } from '../dist/utils/stop-name-cleaner.js';

test('manual labels respect the official stop locations', () => {
    for (const code of ['bthjdwg', 'bthmwjt']) {
        assert.equal(cleanStopName("Sainsbury's", code), "Odd Down Sainsbury's");
    }
    assert.equal(cleanStopName("Sainsbury's", 'sglatjp'), "Stoke Gifford Sainsbury's");
    for (const code of ['wsmpgwm', 'wsmpgwp', 'wsmpgwt', 'wsmpjad', 'wsmpjag', 'wsmpjaj', 'wsmpjam', 'wsmpjap', 'wsmpjat', 'wsmpjaw']) {
        assert.equal(cleanStopName('Public Transport Interchange', code), 'Bristol Airport Interchange');
    }
    assert.equal(cleanStopName('Public Transport Interchange', 'wsmpxxx'), 'Public Transport Interchange');
});
