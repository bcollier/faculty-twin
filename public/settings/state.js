// Settings: S, the state the numbered sections share. Part of the Settings page; admin.js has the map.

/* ---------------- state ---------------- */

export const S = {
  status: null,
  settings: { provider: 'anthropic', model: '' },
  models: [],
  voiceGroups: [],
  freeDefault: '',
  courses: [],
  sources: [],
  pollTimer: null,
};
