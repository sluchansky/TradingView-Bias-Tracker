import { getTopOfBookPresentation } from './src/lib/topOfBookPresentation.js';

let failed = 0;
function check(label: string, value: boolean) {
  if (!value) {
    failed += 1;
    console.error(`FAIL: ${label}`);
  }
}

const live = getTopOfBookPresentation({
  state: 'LIVE', available: true, bid_size: 80, ask_size: 20, imbalance: 0.6, age_s: 0.2,
});
check('fresh quote is displayed as live', live.live && live.state === 'LIVE');
check('fresh quote retains bid/ask and imbalance', live.bid === 80 && live.ask === 20 && live.imbalance === 0.6);

const stale = getTopOfBookPresentation({
  state: 'STALE', available: false, bid_size: null, ask_size: null, imbalance: null, age_s: 6,
});
check('stale quote is labeled stale', !stale.live && stale.state === 'STALE');
check('stale quote never retains sizes or imbalance', stale.bid == null && stale.ask == null && stale.imbalance == null);

const missing = getTopOfBookPresentation({});
check('missing quote is unavailable', !missing.live && missing.state === 'UNAVAILABLE');

const malformed = getTopOfBookPresentation({
  state: 'LIVE', available: true, bid_size: 'bad', ask_size: 20, imbalance: 0.6,
});
check('malformed live quote is not rendered as live', !malformed.live && malformed.state === 'UNAVAILABLE');

if (failed) process.exit(1);
console.log('PASS: top-of-book presentation states');