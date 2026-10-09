// TEST FAKE browser for running public/*.js under node (tests only, never shipped).
//
// Just enough DOM for the pages to load and for a test to drive one function: every element a page
// looks up is a FakeElement remembered by its selector, so a test can read what the page wrote
// (textContent, hidden, src) and fire its listeners. Unknown properties and methods are harmless
// no-ops. fetch is whatever the test passes in. Nothing here touches the network.
//
// Pages are real ES modules (a page script imports its sections from public/student/ or
// public/settings/), so loadPage() imports them as modules: see "Loading a page" at the bottom.

import { registerHooks } from 'node:module';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

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

/* ---- Loading a page ----
   Each loadPage() call is one page load: its own copy of every public/ module, run against its own fake
   browser. Two module hooks make that work without a bundler:
   - resolve: a module that a tagged module imports from public/ gets the same `?ftload=N` tag, so each
     load has its own module graph (top-level code such as listeners and boot() runs once per load, as
     in a browser tab);
   - load: every tagged module gets `const { document, window, fetch, ... } = <load N's fakes>;` in front
     of its first line (so line numbers stay true), and a test-only `__ftPeek(name)` after its last, which
     reads one of its top-level bindings: a test can reach a function or state object the page never
     exports.
   So a page module must not declare a top-level binding named like a fake global (document, fetch, ...),
   which a browser page that uses those globals cannot do either. */

const LOADS = new Map(); // load id -> { globals, modules: [module URL, in load order] }
globalThis.__ftLoads = LOADS;
const PUBLIC = `${pathToFileURL(resolve('public')).href}/`;
let lastLoad = 0;

registerHooks({
  resolve(specifier, context, nextResolve) {
    const found = nextResolve(specifier, context);
    const id = context.parentURL ? new URL(context.parentURL).searchParams.get('ftload') : null;
    if (!id || !found.url.startsWith(PUBLIC)) return found;
    const url = new URL(found.url);
    url.searchParams.set('ftload', id);
    return { ...found, url: url.href, shortCircuit: true };
  },
  load(url, context, nextLoad) {
    const id = Number(new URL(url).searchParams.get('ftload'));
    if (!id) return nextLoad(url, context);
    const { source } = nextLoad(url, { ...context, format: 'module' });
    const load = LOADS.get(id);
    load.modules.push(url);
    const fakes = `const { ${Object.keys(load.globals).join(', ')} } = globalThis.__ftLoads.get(${id}).globals;`;
    const peek = '\nexport function __ftPeek(name) { try { return eval(name); } catch { return undefined; } }\n';
    return { format: 'module', source: fakes + String(source) + peek, shortCircuit: true };
  },
});

/** Run `file` (a public/*.js page script, with every module it imports) in the fake browser, and return
    the named top-level bindings: from the page script if it has one by that name, else from the first
    module it loaded that does. */
export async function loadPage(file, browser, expose) {
  const id = ++lastLoad;
  LOADS.set(id, { globals: browser.globals, modules: [] });
  const url = pathToFileURL(resolve(file));
  url.searchParams.set('ftload', String(id));
  await import(url.href);
  const modules = await Promise.all(LOADS.get(id).modules.map((u) => import(u)));
  const found = {};
  for (const name of expose) {
    const holder = modules.find((m) => m.__ftPeek(name) !== undefined);
    found[name] = holder ? holder.__ftPeek(name) : undefined;
  }
  return found;
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
