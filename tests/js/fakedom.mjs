// TEST FAKE browser for running public/*.js under node (tests only, never shipped).
//
// Just enough DOM for the pages to load and for a test to drive one function: every element a page
// looks up is a FakeElement remembered by its selector, so a test can read what the page wrote
// (textContent, hidden, src) and fire its listeners. Unknown properties and methods are harmless
// no-ops. fetch is whatever the test passes in. Nothing here touches the network.

import { readFileSync } from 'node:fs';

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

class FakeElement {
  constructor(name = '') {
    this._name = name;
    this._listeners = {};
    this._attrs = {};
    this._kids = new Map();
    this.style = { setProperty() {}, removeProperty() {} };
    this.dataset = {};
    this.hidden = false;
    this.disabled = false;
    this.textContent = '';
    this.value = '';
    this.className = '';
    this.checked = false;
    this.clientWidth = 640;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    const classes = new Set();
    this.classList = {
      add: (...c) => c.forEach(x => classes.add(x)),
      remove: (...c) => c.forEach(x => classes.delete(x)),
      toggle: (c, on) => { const v = on === undefined ? !classes.has(c) : !!on; if (v) classes.add(c); else classes.delete(c); return v; },
      contains: (c) => classes.has(c),
    };
    return new Proxy(this, {
      get(target, prop, receiver) {
        if (prop in target || typeof prop === 'symbol') return Reflect.get(target, prop, receiver);
        if (prop === 'then') return undefined; // never look like a promise
        return () => receiver; // any other method: a chainable no-op
      },
    });
  }

  addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) { this._listeners[type] = (this._listeners[type] || []).filter(f => f !== fn); }
  /** Fire `type` and wait for async listeners. */
  async fire(type, extra = {}) {
    const ev = { type, target: this, currentTarget: this, preventDefault() {}, stopPropagation() {}, ...extra };
    for (const fn of this._listeners[type] || []) await fn(ev);
  }
  setAttribute(k, v) { this._attrs[k] = String(v); }
  getAttribute(k) { return k in this._attrs ? this._attrs[k] : (k === 'src' && this.src != null ? this.src : null); }
  hasAttribute(k) { return k in this._attrs; }
  removeAttribute(k) { delete this._attrs[k]; if (k === 'src') this.src = ''; }
  querySelector(sel) { if (!this._kids.has(sel)) this._kids.set(sel, new FakeElement(`${this._name} ${sel}`)); return this._kids.get(sel); }
  querySelectorAll() { return []; }
  closest() { return new FakeElement('closest'); }
  matches() { return false; }
  append() {}
  replaceChildren() {}
  remove() {}
  focus() {}
  play() { return Promise.resolve(); }
  pause() {}
  load() {}
  get parentElement() { return new FakeElement('parent'); }
}

export function makeBrowser({ fetch, confirm = () => true, hostname = 'faculty-twin.example' } = {}) {
  const byselector = new Map();
  const docListeners = {};
  const winListeners = {};
  const element = (sel) => {
    if (!byselector.has(sel)) byselector.set(sel, new FakeElement(sel));
    return byselector.get(sel);
  };
  const document = {
    querySelector: element,
    querySelectorAll: () => [],
    getElementById: (id) => element(`#${id}`),
    createElement: (tag) => new FakeElement(tag),
    createElementNS: (_ns, tag) => new FakeElement(tag),
    addEventListener: (t, fn) => { (docListeners[t] ||= []).push(fn); },
    dispatchEvent: () => true,
    documentElement: new FakeElement('html'),
    body: new FakeElement('body'),
  };
  const storage = new Map();
  class FakeAudio extends FakeElement {}
  const globals = {
    document,
    window: {
      addEventListener: (t, fn) => { (winListeners[t] ||= []).push(fn); },
      open: () => null,
      confirm,
      location: { hostname, search: '' },
    },
    location: { hostname, search: '' },
    localStorage: {
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
      removeItem: (k) => storage.delete(k),
    },
    fetch,
    Audio: FakeAudio,
    URL: { createObjectURL: () => 'blob:fake', revokeObjectURL() {} },
    Blob: class { constructor(parts, opts) { this.parts = parts; this.opts = opts; } },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    performance: { now: () => Date.now() },
    requestAnimationFrame: () => 0,
    cancelAnimationFrame: () => {},
    IntersectionObserver: class { observe() {} unobserve() {} disconnect() {} },
  };
  return { globals, element, docListeners, winListeners };
}

/** Run `file` (a public/*.js page script) in the fake browser and return the named top-level bindings. */
export async function loadPage(file, browser, expose) {
  const src = readFileSync(file, 'utf8');
  const names = Object.keys(browser.globals);
  const body = `${src}\n;return { ${expose.map(n => `${n}: typeof ${n} === 'undefined' ? undefined : ${n}`).join(', ')} };`;
  const run = new AsyncFunction(...names, body);
  return run(...names.map(n => browser.globals[n]));
}

/** A response object like fetch's. */
export function reply(status, data) {
  return { status, ok: status >= 200 && status < 300, json: async () => data };
}

/** A promise plus its resolve function, to hold a request open while the test acts. */
export function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

export const tick = () => new Promise((r) => setTimeout(r, 0));
