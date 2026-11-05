import { X } from '@phosphor-icons/react';
import { Composer } from './Composer.jsx';

export function NewLoop({ input, text, onChange, onSubmit, pending, configured, error, onDismissError, title = '你希望 Loop 自动完成什么？', pageLabel = '新建 Loop', placeholder = '描述你希望自动完成的工作…', pendingLabel, draftKey }) {
  return <main className="workspace new-loop-page" aria-label={pageLabel}>
    <div className="new-loop-drag-region" aria-hidden="true" />
    <section className="new-loop-welcome" aria-labelledby="new-loop-heading">
      <img src="./assets/loops-icon.png" alt="" className="avatar ai-avatar" />
      <h1 id="new-loop-heading">{title}</h1>
    </section>
    <div className="new-loop-input-area">
      {error && <div className="service-error" role="alert"><span>{error}</span><button type="button" aria-label="关闭提示" onClick={onDismissError}><X size={17} /></button></div>}
      <Composer draftKey={draftKey} input={input} text={text} onChange={onChange} onSubmit={onSubmit} pending={pending} placeholder={placeholder} pendingLabel={pendingLabel} className="new-loop-composer" note={configured === false ? '模型尚未配置，需求可以先保留。' : undefined} />
    </div>
  </main>;
}
