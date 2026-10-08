// Runs public/dev/mock.js in Node (no browser) and prints the JSON it answers for each request.
// Used by tests/test_contract_mock.py to check the mock's shapes against the real FastAPI app.
//
//   node tests/contract/dump_mock.mjs < requests.json > responses.json
//
// stdin: [{"name": "...", "method": "GET", "path": "/api/...", "body": {...}|null, "as": "student"|"admin"|null}]
// stdout: {"<name>": {"status": 200, "body": <json or null>}}

import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const mockPath = path.resolve(here, '../../public/dev/mock.js');

// Minimal browser globals the mock touches at import time.
const storage = new Map();
const sessionStorage = {
  getItem: (k) => (storage.has(k) ? storage.get(k) : null),
  setItem: (k, v) => storage.set(k, String(v)),
  removeItem: (k) => storage.delete(k),
};
const realSetTimeout = globalThis.setTimeout;
globalThis.window = globalThis;
globalThis.location = new URL('http://127.0.0.1:8765/index.html?mock=1');
globalThis.sessionStorage = sessionStorage;
globalThis.XMLHttpRequest = class {};
globalThis.setTimeout = (fn, _ms, ...args) => realSetTimeout(fn, 0, ...args); // skip the mock's pauses
console.info = () => {};
console.warn = () => {};

await import(pathToFileURL(mockPath).href);

const requests = JSON.parse(readFileSync(0, 'utf8'));
const out = {};
for (const req of requests) {
  storage.clear();
  if (req.as === 'student' || req.as === 'admin') {
    await window.fetch('/api/login', { method: 'POST', body: JSON.stringify({ passcode: 'demo' }) });
  }
  if (req.as === 'admin') {
    await window.fetch('/api/admin/login', { method: 'POST', body: JSON.stringify({ passcode: 'admin' }) });
  }
  const init = { method: req.method || 'GET' };
  if (req.body != null) init.body = JSON.stringify(req.body);
  let status = null;
  let body = null;
  try {
    const res = await window.fetch(req.path, init);
    status = res.status;
    const text = await res.text();
    body = text ? JSON.parse(text) : null;
  } catch (err) {
    status = 'error';
    body = String(err && err.message);
  }
  out[req.name] = { status, body };
}
process.stdout.write(JSON.stringify(out));
