import { createAssistantMessageEventStream } from '@earendil-works/pi-ai';
import { setTimeout as delay } from 'node:timers/promises';

export const MODEL_IDLE_MS = 60_000;
export const MODEL_RETRIES = 5;
export const MODEL_IDLE_MESSAGE = '模型连续 60 秒没有返回新的内容，本次等待已超时。已完成的工具结果已保留，不会自动重跑已执行的工具。';
const progressTypes = new Set(['text_delta', 'thinking_delta', 'toolcall_delta']);
const transient = message => /^(408|409|429|500|502|503|504)\b/.test(message) || /fetch failed|network|connection|socket|ECONN|ETIMEDOUT|timed out|stream ended without finish_reason/i.test(message);

// Retry one model response, never agent.prompt or a tool. Only the successful
// attempt's terminal message reaches Agent; previous tool results stay in context.
export function modelStream(send, { record = () => {}, status = () => {}, idleMs = MODEL_IDLE_MS, slowMs = 30_000, retries = MODEL_RETRIES, retryDelayMs = 1000 } = {}) {
  return (model, context, options = {}) => {
    const output = createAssistantMessageEventStream();
    const request = { ...context, messages: structuredClone(context.messages || []) };
    let announced = false;
    const errorMessage = (text, aborted = false) => ({ role:'assistant', provider:model.provider, model:model.id, api:model.api,
      content:[], timestamp:Date.now(), stopReason:aborted ? 'aborted' : 'error', errorMessage:text,
      usage:{input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}} });
    const end = message => { output.push({type:'error',reason:message.stopReason,error:message}); output.end(message); };
    void (async () => {
      for (let attempt = 0; attempt <= retries; attempt++) {
        if (options.signal?.aborted) { end(errorMessage('本次回复已停止，已完成的工具结果已保留。', true)); return; }
        const controller = new AbortController();
        const signal = options.signal ? AbortSignal.any([options.signal, controller.signal]) : controller.signal;
        const started = performance.now(), elapsed = () => Math.round(performance.now() - started);
        const trace = {provider:model.provider,model:model.id,startedAt:new Date().toISOString(),attempt:attempt + 1,http:[],deltaCount:0,bodyBytes:0};
        let timer, warning, slow = false, failure, result, removeAbort;
        const arm = () => {
          clearTimeout(timer); clearTimeout(warning);
          timer = setTimeout(() => controller.abort(new Error(MODEL_IDLE_MESSAGE)), idleMs);
          warning = setTimeout(() => { slow = true; status('模型暂时没有新内容，仍在等待；你可以随时停止。', 'slow'); }, slowMs);
        };
        const fetcher = options.fetch || globalThis.fetch;
        const observedFetch = async (input, init) => {
          const http = {sentMs:elapsed()}; trace.http.push(http);
          try {
            const response = await fetcher(input, init); http.headersMs = elapsed(); http.status = response.status;
            const retryAfter = response.headers.get('retry-after');
            if (retryAfter) { const seconds = Number(retryAfter); const ms = Number.isFinite(seconds) ? seconds * 1000 : Date.parse(retryAfter) - Date.now(); if (Number.isFinite(ms)) http.retryAfterMs = Math.max(0, ms); }
            if (!response.body) return response;
            const body = response.body.pipeThrough(new TransformStream({transform(chunk, sink) {
              trace.firstBodyMs ??= elapsed(); trace.lastBodyMs = elapsed(); trace.bodyBytes += chunk.byteLength; sink.enqueue(chunk);
            }}));
            return new Response(body, {status:response.status,statusText:response.statusText,headers:response.headers});
          } catch (error) { http.errorType = error?.name || 'Error'; throw error; }
        };
        arm(); status('正在等待模型响应…', 'waiting');
        try {
          const aborted = new Promise((_, reject) => {
            const abort = () => reject(new Error(controller.signal.aborted ? MODEL_IDLE_MESSAGE : 'cancelled'));
            signal.addEventListener('abort', abort, {once:true}); removeAbort = () => signal.removeEventListener('abort', abort);
            if (signal.aborted) abort();
          });
          result = await Promise.race([aborted, (async () => {
            const source = await send(model, {...request,messages:structuredClone(request.messages)}, {...options,signal,fetch:observedFetch,maxRetries:0});
            for await (const event of source) {
              if (signal.aborted) return;
              if (progressTypes.has(event.type) && event.delta) {
                trace.firstDeltaMs ??= elapsed(); trace.lastDeltaMs = elapsed(); trace.deltaCount++;
                if (trace.deltaCount === 1 || slow) { slow = false; status('正在处理…', 'generating'); }
                arm();
              }
              // Agent appends a message for every start: suppress retry starts to
              // avoid leaking abandoned partial tool calls into conversation history.
              if (event.type === 'start') { if (announced) continue; announced = true; }
              if (event.type !== 'done' && event.type !== 'error') output.push(event);
            }
            return source.result();
          })()]);
          if (result?.stopReason === 'error' || result?.stopReason === 'aborted') failure = new Error(result.errorMessage || 'Model request failed');
          else if (!result) failure = new Error('Stream ended without finish_reason');
        } catch (error) { failure = error; }
        finally {
          clearTimeout(timer); clearTimeout(warning); removeAbort?.();
          trace.durationMs = elapsed(); trace.outcome = options.signal?.aborted ? 'cancelled' : controller.signal.aborted ? 'idle_timeout' : failure ? 'error' : result.stopReason;
          try { record(trace); } catch { /* Diagnostics must not interrupt the user's request. */ }
        }
        if (options.signal?.aborted) { end(errorMessage('本次回复已停止，已完成的工具结果已保留。', true)); return; }
        if (!failure) { output.push({type:'done',reason:result.stopReason,message:result}); output.end(result); return; }
        const canRetry = controller.signal.aborted || transient(String(failure.message));
        if (!canRetry || attempt === retries) { end(errorMessage(controller.signal.aborted ? MODEL_IDLE_MESSAGE : String(failure.message))); return; }
        const waitMs = Math.min(Math.max(Math.min(retryDelayMs * 2 ** attempt, 10_000), trace.http.at(-1)?.retryAfterMs || 0), options.maxRetryDelayMs ?? 30_000);
        status(`模型请求未完成，正在重试 ${attempt + 1}/${retries}…`, 'retrying', {retry:attempt + 1,maxRetries:retries});
        try { await delay(waitMs, undefined, {signal:options.signal}); }
        catch { end(errorMessage('本次回复已停止，已完成的工具结果已保留。', true)); return; }
      }
    })().catch(error => end(errorMessage(String(error.message))));
    return output;
  };
}
