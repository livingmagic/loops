// Watch live transitions only: opening the app must not replay historical work.
export class FailureReviews {
  constructor({ review, busy, onError }) {
    this.review = review; this.busy = busy; this.onError = onError;
    this.active = new Map(); this.ignored = new Set(); this.pending = new Map(); this.draining = false; this.closed = false;
  }
  observe(loops) {
    if (this.closed) return;
    for (const loop of loops) {
      const run = loop.runs.at(-1);
      if (!run || this.ignored.has(run.id)) continue;
      if (['queued', 'preparing', 'running', 'correcting'].includes(run.status)) this.active.set(run.id, loop.id);
      else if (this.active.delete(run.id) && run.status === 'failed') this.pending.set(loop.id, run.id);
      if (this.pending.has(loop.id) && (run.status !== 'failed' || run.failureReview)) this.pending.delete(loop.id);
    }
    this.drain();
  }
  cancel(loopId) {
    this.pending.delete(loopId);
    for (const [runId, owner] of this.active) if (owner === loopId) { this.ignored.add(runId); this.active.delete(runId); }
  }
  async drain() {
    if (this.draining || this.closed) return;
    this.draining = true;
    try {
      for (const [loopId, runId] of this.pending) {
        if (this.closed) break;
        if (this.busy(loopId)) continue;
        this.pending.delete(loopId);
        try { await this.review(loopId, runId); }
        catch (error) { this.onError(loopId, error); }
      }
    } finally { this.draining = false; }
  }
  close() { this.closed = true; this.pending.clear(); }
}
