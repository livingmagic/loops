import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { defaultPanelWidths, fitPanelWidths, panelBounds, readPanelWidths, resizePanel, savePanelWidths } from './panel-layout.js';

const LayoutContext = createContext(null);

export function ResizablePanels({ className, children }) {
  const [viewport, setViewport] = useState(window.innerWidth);
  const [preferred, setPreferred] = useState(() => {
    try { return readPanelWidths(window.localStorage); } catch { return null; }
  });
  const [resizing, setResizing] = useState(false);
  const drag = useRef(null), latest = useRef(preferred);
  const widths = fitPanelWidths(preferred || defaultPanelWidths(viewport), viewport);

  useEffect(() => {
    const resized = () => setViewport(window.innerWidth);
    const move = (event) => {
      const start = drag.current;
      if (!start) return;
      const delta = (event.clientX - start.x) * (start.panel === 'sidebar' ? 1 : -1);
      update(resizePanel(start.panel, start.widths[start.panel] + delta, fitPanelWidths(start.widths, window.innerWidth), window.innerWidth));
    };
    const finish = () => {
      if (!drag.current) return;
      drag.current = null;
      setResizing(false);
      persist();
    };
    window.addEventListener('resize', resized);
    window.addEventListener('mousemove', move, true);
    window.addEventListener('mouseup', finish, true);
    window.addEventListener('blur', finish);
    return () => {
      window.removeEventListener('resize', resized);
      window.removeEventListener('mousemove', move, true);
      window.removeEventListener('mouseup', finish, true);
      window.removeEventListener('blur', finish);
    };
  }, []);

  const update = (value) => { latest.current = value; setPreferred(value); };
  const persist = () => {
    try { savePanelWidths(window.localStorage, latest.current); } catch { /* Keep the session usable without storage. */ }
  };
  const adjust = (panel, requested) => {
    update(resizePanel(panel, requested, widths, viewport));
    persist();
  };

  const divider = (panel) => ({
    onMouseDown(event) {
      if (event.button !== 0 || drag.current) return;
      event.preventDefault();
      event.currentTarget.focus();
      drag.current = { panel, x: event.clientX, widths };
      setResizing(true);
    },
    onDoubleClick: () => adjust(panel, defaultPanelWidths(viewport)[panel]),
    onKeyDown(event) {
      const { min, max } = panelBounds(panel, widths, viewport);
      const direction = panel === 'sidebar' ? 1 : -1;
      const step = event.shiftKey ? 40 : 10;
      const requested = {
        ArrowLeft: widths[panel] - step * direction,
        ArrowRight: widths[panel] + step * direction,
        Home: min,
        End: max,
        Enter: defaultPanelWidths(viewport)[panel],
      }[event.key];
      if (requested === undefined) return;
      event.preventDefault();
      adjust(panel, requested);
    },
  });

  return <LayoutContext.Provider value={{ widths, viewport, divider }}><div className={`${className}${resizing ? ' resizing-panels' : ''}`} style={{ '--sidebar-width': `${widths.sidebar}px`, '--inspector-width': `${widths.inspector}px` }}>{children}</div></LayoutContext.Provider>;
}

export function PanelDivider({ panel }) {
  const { widths, viewport, divider } = useContext(LayoutContext);
  const { min, max } = panelBounds(panel, widths, viewport);
  return <div className={`panel-divider panel-divider-${panel}`} role="separator" tabIndex={0} aria-label={panel === 'sidebar' ? '调整导航栏宽度' : '调整流程栏宽度'} aria-orientation="vertical" aria-controls={panel === 'sidebar' ? 'loop-navigation' : 'loop-inspector'} aria-valuemin={min} aria-valuemax={max} aria-valuenow={widths[panel]} aria-valuetext={`${widths[panel]} 像素`} {...divider(panel)} />;
}
