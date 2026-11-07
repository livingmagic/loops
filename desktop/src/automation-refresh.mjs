import { createRefreshTask } from './refresh-task.mjs';

export function createAutomationRefresh(request, { onItems, onCatalog, onDetail }) {
  const items = createRefreshTask(() => request('automation_list'), onItems);
  const catalog = createRefreshTask(() => request('automation_catalog'), onCatalog);
  let chatVersion = 0;
  const detail = createRefreshTask(async taskId => {
    const version = chatVersion;
    const value = await request('automation_get', { taskId });
    return { value, version };
  }, ({ value, version }, taskId) => onDetail(value, taskId, version !== chatVersion));
  let catalogKey = null;
  return {
    async refresh(taskId, { includeCatalog = false, invalidate = false } = {}) {
      const requests = [items.refresh({ invalidate })];
      if (includeCatalog) requests.push(catalog.refresh({ invalidate }));
      if (taskId) requests.push(detail.refresh({ key: taskId, invalidate }));
      const [tasks] = await Promise.all(requests);
      return tasks;
    },
    refreshEvent(event, taskId) {
      if (event.taskId === taskId && ['automation_agent_start', 'automation_agent_delta', 'automation_agent_done'].includes(event.event)) chatVersion++;
      if (event.event === 'state') {
        // Run progress and conversation updates do not change the version menu.
        const key = JSON.stringify(event.loops.map(loop => [loop.id, loop.title, loop.savedRevision, loop.versions]));
        if (key === catalogKey) return Promise.resolve();
        catalogKey = key;
        return catalog.refresh({ invalidate: true }).catch(error => { catalogKey = null; throw error; });
      }
      if (event.event === 'connected') return this.refresh(taskId, { includeCatalog: true, invalidate: true });
      if (['automation_state', 'automation_agent_start', 'automation_agent_done', 'automation_draft_updated'].includes(event.event)) {
        return this.refresh(!event.taskId || event.taskId === taskId ? taskId : null, { invalidate: true });
      }
      return Promise.resolve();
    },
    cancelDetail() { detail.cancel(); },
    close() { items.cancel(); catalog.cancel(); detail.cancel(); },
  };
}
