const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('loops', {
  request: (method, params) => ipcRenderer.invoke('loops:request', method, params),
  subscribe: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on('loops:event', listener);
    return () => ipcRenderer.removeListener('loops:event', listener);
  },
  choose: (kind) => ipcRenderer.invoke('loops:choose', kind),
  reveal: (loopId) => ipcRenderer.invoke('loops:reveal', loopId),
  openUrl: (url) => ipcRenderer.invoke('loops:url', url),
});
