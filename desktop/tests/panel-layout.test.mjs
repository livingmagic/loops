import test from 'node:test';
import assert from 'node:assert/strict';
import { CONVERSATION_MIN, LAYOUT_STORAGE_KEY, PANEL_LIMITS, defaultPanelWidths, fitPanelWidths, readPanelWidths, resizePanel, savePanelWidths } from '../src/panel-layout.js';

test('resizing either panel keeps the opposite panel steady and reserves the conversation', () => {
  const initial = { sidebar: 240, inspector: 360 };
  const widerSidebar = resizePanel('sidebar', 900, initial, 1000);
  assert.deepEqual(widerSidebar, { sidebar: 240, inspector: 360 });
  const narrowerSidebar = resizePanel('sidebar', 0, initial, 1000);
  assert.deepEqual(narrowerSidebar, { sidebar: 180, inspector: 360 });
  const widerInspector = resizePanel('inspector', 900, narrowerSidebar, 1000);
  assert.deepEqual(widerInspector, { sidebar: 180, inspector: 420 });
  assert.equal(1000 - widerInspector.sidebar - widerInspector.inspector, CONVERSATION_MIN);
});

test('narrowing a window fits both panels without overwriting the preferred sizes', () => {
  const preferred = { sidebar: 400, inspector: 640 };
  for (const viewport of [840, 1000, 1200, 1487, 2000]) {
    const fitted = fitPanelWidths(preferred, viewport);
    assert.ok(viewport - fitted.sidebar - fitted.inspector >= CONVERSATION_MIN);
    for (const [panel, limits] of Object.entries(PANEL_LIMITS)) {
      assert.ok(fitted[panel] >= limits.min && fitted[panel] <= limits.max);
    }
  }
  assert.deepEqual(fitPanelWidths(preferred, 2000), preferred);
  assert.deepEqual(preferred, { sidebar: 400, inspector: 640 });
  assert.deepEqual(fitPanelWidths(defaultPanelWidths(1000), 1000), { sidebar: 180, inspector: 260 });
});

test('panel preferences survive reload and invalid or unavailable storage is safe', () => {
  const values = new Map();
  const storage = { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value) };
  const widths = { sidebar: 310, inspector: 430 };
  savePanelWidths(storage, widths);
  assert.deepEqual(readPanelWidths(storage), widths);
  for (const value of ['{broken', 'null', '[]', '{"sidebar":"310","inspector":430}', '{"sidebar":-1,"inspector":430}']) {
    values.set(LAYOUT_STORAGE_KEY, value);
    assert.equal(readPanelWidths(storage), null);
  }
  const unavailable = { getItem() { throw new Error('unavailable'); }, setItem() { throw new Error('unavailable'); } };
  assert.equal(readPanelWidths(unavailable), null);
  assert.doesNotThrow(() => savePanelWidths(unavailable, widths));
});
