// public/readalong.js: tokens and sentences, the syllable estimate, time -> word lookups,
// the stemmer, and which narration words light up which slide words (synthetic slides only).
import assert from 'node:assert/strict';
import * as RA from '../../public/readalong.js';

// ---- tokens, sentences, lookups
const text = 'On this slide, gradient descent moves downhill. Then the learning rate sets the step.';
const tokens = RA.tokenize(text);
assert.equal(tokens.length, 14);
assert.deepEqual(tokens[2], { text: 'slide,', start: 8, end: 14, sentence: 0 });
assert.equal(tokens[7].sentence, 1);
assert.deepEqual(RA.sentences(tokens), [{ first: 0, last: 6 }, { first: 7, last: 13 }]);

assert.equal(RA.charAt([], 3), -1);
const words = [[0, 0], [0.3, 3], [0.6, 8], [1.2, 15]];
assert.equal(RA.charAt(words, -1), -1);
assert.equal(RA.charAt(words, 0.62), 8);
assert.equal(RA.charAt(words, 0.56), 8); // a 50 ms lead so the highlight never lags the voice
assert.equal(RA.charAt(words, 99), 15);
assert.equal(RA.tokenAt(tokens, -1), -1);
assert.equal(RA.tokenAt(tokens, 15), 3);
assert.equal(RA.tokenAt(tokens, 17), 3); // inside "gradient"

assert.equal(RA.validTimings(null), null);
assert.equal(RA.validTimings([]), null);
assert.equal(RA.validTimings([[1, 0], [0.5, 3]]), null); // going back in time
assert.equal(RA.validTimings([[0, 'x']]), null);
assert.deepEqual(RA.validTimings(words), words);

// ---- the estimate (same rule as app/timings.py: syllables, plus pauses at commas and sentence ends)
assert.equal(RA.syllables('banana'), 3);
assert.equal(RA.syllables('make'), 1);
assert.equal(RA.syllables('table'), 2);
assert.equal(RA.syllables('42'), 1);
const est = RA.estimateTimings('Hi there, banana. Go', 10);
assert.deepEqual(est.map(w => w[1]), [0, 3, 10, 18]);
assert.ok(est[2][0] - est[1][0] > est[1][0] - est[0][0]);
assert.deepEqual(RA.estimateTimings('', 3), []);
assert.deepEqual(RA.estimateTimings('Words here', 0), []);

// ---- stemming: the same on both sides
for (const [a, b] of [['clusters', 'cluster'], ['Clustering', 'cluster'], ['embeddings', 'embedding'],
  ['predictions', 'Prediction'], ['learned', 'learning'], ['policies', 'policy'], ['quickly', 'quick'],
  ['computes', 'computing'], ["model's", 'models'], ['running', 'run']]) {
  assert.equal(RA.stem(a), RA.stem(b), `${a} ~ ${b}`);
}
assert.notEqual(RA.stem('class'), RA.stem('cla'));
assert.equal(RA.normWord('“Gradient,”'), 'gradient');

// ---- merging boxes into rectangles
const rects = RA.mergeRects([
  { x0: 0.1, y0: 0.1, x1: 0.2, y1: 0.15, line: 0 }, { x0: 0.21, y0: 0.1, x1: 0.3, y1: 0.16, line: 0 },
  { x0: 0.8, y0: 0.1, x1: 0.9, y1: 0.15, line: 0 }, { x0: 0.1, y0: 0.3, x1: 0.2, y1: 0.35, line: 1 }]);
assert.deepEqual(rects, [{ x0: 0.1, y0: 0.1, x1: 0.3, y1: 0.16 }, { x0: 0.8, y0: 0.1, x1: 0.9, y1: 0.15 },
  { x0: 0.1, y0: 0.3, x1: 0.2, y1: 0.35 }]);

// ---- matching narration words to slide words
const box = (t, x, y, line) => [t, x, y, x + 0.08, y + 0.05, line];
const slide = { v: 1, src: 'pdf', words: [
  box('Gradient', 0.1, 0.1, 0), box('Descent', 0.2, 0.1, 0),
  box('The', 0.1, 0.3, 1), box('learning', 0.2, 0.3, 1), box('rate', 0.3, 0.3, 1), box('sets', 0.4, 0.3, 1),
  box('the', 0.5, 0.3, 1), box('step', 0.6, 0.3, 1),
  box('Overfitting', 0.1, 0.5, 2), box('is', 0.2, 0.5, 2), box('bad', 0.3, 0.5, 2),
  box('data', 0.1, 0.7, 3), box('data', 0.2, 0.7, 3), box('data', 0.3, 0.7, 3), box('data', 0.4, 0.7, 3),
  ['broken', 0.5, 0.5], ['zero', 0.5, 0.5, 0.5, 0.5, 4],
] };
const narration = 'Gradient descent moves downhill. The learning rate sets the step. Overfitting is bad. ' +
  'More data helps. The learning rate matters.';
const nt = RA.tokenize(narration);
const plan = RA.planHighlights(nt, slide);
const fired = [...plan.entries()].map(([i, m]) => [nt[i].text, m.words, m.length]);
assert.deepEqual(fired, [
  ['Gradient', 'Gradient Descent', 2], // a phrase, not two single words
  ['learning', 'learning rate sets the step', 4], // stopwords between content words are inside the region
  ['Overfitting', 'Overfitting is bad', 2],
]);
// A single word fires when it is specific (4+ letters, at most 3 times on the slide).
assert.deepEqual([...RA.planHighlights(RA.tokenize('Watch for overfitting.'), slide).values()].map(m => m.words), ['Overfitting']);
// The second phrase stops at the narrator's full stop, not at the slide's next line. "data" appears 4 times on
// the slide (too common to point at); "learning rate" again is already lit; "The" is a stopword.
assert.deepEqual(plan.get(0).rects.map(r => Object.values(r).map(v => +v.toFixed(4))), [[0.1, 0.1, 0.28, 0.15]]);
assert.equal(plan.get(0).key, '0-1');
assert.equal(RA.planHighlights(nt, null).size, 0);
assert.equal(RA.planHighlights(nt, { words: 'nope' }).size, 0);

// The occurrence after the last match wins when a word appears twice.
const twice = { words: [box('weights', 0.1, 0.1, 0), box('Neural', 0.1, 0.8, 1), box('network', 0.2, 0.8, 1),
  box('weights', 0.1, 0.9, 2)] };
const p2 = RA.planHighlights(RA.tokenize('A neural network learns weights.'), twice);
assert.deepEqual([...p2.values()].map(m => m.key), ['1-2', '3-3']);
// With no earlier match, the top-most copy wins.
assert.deepEqual([...RA.planHighlights(RA.tokenize('Look at the weights.'), twice).values()].map(m => m.key), ['0-0']);

console.log('readalong ok');
