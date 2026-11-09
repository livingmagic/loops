const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('loops', {
  request: (method, params) => ipcRenderer.invoke('loops:request', method, params),
  subscribe: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on('loops:event', listener);
    return () => ipcRenderer.removeListener('loops:event', listener);
  },
  choose: (kind) => ipcRenderer.invoke('loops:choose', kind),
  openInFinder: (path) => ipcRenderer.invoke('loops:open-in-finder', path),
  openUrl: (url) => ipcRenderer.invoke('loops:url', url),
});
