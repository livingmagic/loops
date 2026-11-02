export const LAYOUT_STORAGE_KEY = 'loops.panel-widths.v1';
export const PANEL_LIMITS = {
  sidebar: { min: 180, max: 400 },
  inspector: { min: 260, max: 640 },
};
export const CONVERSATION_MIN = 400;
export const LAYOUT_MIN = PANEL_LIMITS.sidebar.min + PANEL_LIMITS.inspector.min + CONVERSATION_MIN;

const clamp = (value, min, max) => Math.min(max, Math.max(min, Math.round(value)));

export function defaultPanelWidths(viewport) {
  const sidebar = viewport <= 1000 ? 180 : viewport <= 1200 ? 210 : clamp(viewport * .17, 210, 254);
  const inspector = viewport <= 1000 ? 260 : (viewport - sidebar) * (viewport <= 1200 ? .355 : .364);
  return { sidebar, inspector: Math.round(inspector) };
}

export function fitPanelWidths(preferred, viewport) {
  const widths = Object.fromEntries(Object.entries(PANEL_LIMITS).map(([panel, { min, max }]) => [panel, clamp(preferred[panel], min, max)]));
  const available = Math.max(LAYOUT_MIN, Math.floor(viewport)) - CONVERSATION_MIN;
  if (widths.sidebar + widths.inspector > available) {
    const flexible = widths.sidebar + widths.inspector - PANEL_LIMITS.sidebar.min - PANEL_LIMITS.inspector.min;
    const remaining = available - PANEL_LIMITS.sidebar.min - PANEL_LIMITS.inspector.min;
    widths.sidebar = PANEL_LIMITS.sidebar.min + Math.round((widths.sidebar - PANEL_LIMITS.sidebar.min) * remaining / flexible);
    widths.inspector = available - widths.sidebar;
  }
  return widths;
}

export function panelBounds(panel, widths, viewport) {
  const other = panel === 'sidebar' ? 'inspector' : 'sidebar';
  return {
    min: PANEL_LIMITS[panel].min,
    max: Math.min(PANEL_LIMITS[panel].max, Math.max(LAYOUT_MIN, viewport) - CONVERSATION_MIN - widths[other]),
  };
}

export function resizePanel(panel, requested, widths, viewport) {
  const { min, max } = panelBounds(panel, widths, viewport);
  return { ...widths, [panel]: clamp(requested, min, max) };
}

export function readPanelWidths(storage) {
  try {
    const value = JSON.parse(storage.getItem(LAYOUT_STORAGE_KEY));
    if (Object.keys(PANEL_LIMITS).every((panel) => Number.isFinite(value?.[panel]) && value[panel] > 0)) return value;
  } catch { /* Window preferences must not prevent opening the client. */ }
  return null;
}

export function savePanelWidths(storage, widths) {
  try { storage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(widths)); } catch { /* Resizing still works if preferences cannot be saved. */ }
}
