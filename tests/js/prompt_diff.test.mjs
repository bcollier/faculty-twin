// public/prompt-diff.js: the word-level diff in Settings > Prompts' review panel (split out of admin.js
// in the Oct 8 clean-code pass). Synthetic prompt text only.
import assert from 'node:assert/strict';
import { countWords, diffParts } from '../../public/prompt-diff.js';

const join = (parts, keep) => parts.filter(([op]) => op === '=' || op === keep).map(([, t]) => t).join('');

// ---- no change, one word changed
assert.deepEqual(diffParts('Answer in JSON.', 'Answer in JSON.'), [['=', 'Answer in JSON.']]);
assert.deepEqual(diffParts('Answer in plain JSON.', 'Answer in strict JSON.'),
  [['=', 'Answer in '], ['-', 'plain'], ['+', 'strict'], ['=', ' JSON.']]);
assert.deepEqual(diffParts('', 'New text'), [['+', 'New text']]);
assert.deepEqual(diffParts('Old text', ''), [['-', 'Old text']]);

// ---- word counts ignore whitespace runs
const parts = diffParts('Use the slides.', 'Use only the  course slides.');
assert.equal(countWords(parts, '+'), 2);
assert.equal(countWords(parts, '-'), 0);
// Punctuation belongs to its word: "slides." to "slides here." removes one word and adds two.
const punct = diffParts('Use the slides.', 'Use the slides here.');
assert.equal(countWords(punct, '-'), 1);
assert.equal(countWords(punct, '+'), 2);

// ---- the kept and removed runs rebuild the old text; the kept and added runs rebuild the new text
const before = 'You explain {question} from the slides.\nNever invent a slide.\nKeep it short.';
const after = 'You explain {question} using only the slides.\nKeep it short and plain.\nSay JSON.';
const d = diffParts(before, after);
assert.equal(join(d, '-'), before);
assert.equal(join(d, '+'), after);
for (let k = 1; k < d.length; k++) assert.notEqual(d[k][0], d[k - 1][0], 'neighbouring runs are joined');

// ---- a huge rewrite takes the line fallback and still rebuilds both texts
const big = (w) => Array.from({ length: 1400 }, (_, i) => `${w}${i % 7}${i % 9 ? ' ' : '\n'}`).join('');
const lines = (w) => Array.from({ length: 200 }, (_, i) => `${w} line ${i} of the prompt text here\n`).join('');
for (const [x, y] of [[big('a'), big('b')], [lines('old'), lines('new')]]) {
  const p = diffParts(x, y);
  assert.equal(join(p, '-'), x);
  assert.equal(join(p, '+'), y);
}

console.log('prompt-diff ok');
