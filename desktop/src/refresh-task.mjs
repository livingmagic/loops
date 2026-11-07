// Coalesce requests for the same resource. An invalidation discards the
// in-flight result and schedules one fresh read before publishing.
export function createRefreshTask(read, publish) {
  let current = null;
  function refresh({ key = '', invalidate = false } = {}) {
    if (current?.key === key) {
      current.dirty ||= invalidate;
      return current.promise;
    }
    const job = { key, dirty: false };
    current = job;
    job.promise = Promise.resolve().then(async () => {
      do {
        job.dirty = false;
        let value;
        try {
          value = await read(key);
        } catch (error) {
          if (current !== job) return;
          if (job.dirty) continue;
          throw error;
        }
        if (current !== job) return;
        if (!job.dirty) {
          publish(value, key);
          return value;
        }
      } while (current === job);
    }).finally(() => {
      if (current === job) current = null;
    });
    return job.promise;
  }
  return {
    refresh,
    cancel() { current = null; },
    accept(value) { current = null; publish(value); },
  };
}
