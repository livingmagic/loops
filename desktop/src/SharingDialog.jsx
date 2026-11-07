import { useEffect, useId, useRef } from 'react';
import { X } from '@phosphor-icons/react';

export function SharingDialog({ title, onClose, children }) {
  const ref = useRef(null);
  const titleId = useId();
  useEffect(() => { ref.current.showModal(); }, []);
  return <dialog ref={ref} className="dialog sharing-dialog" aria-labelledby={titleId} onCancel={onClose}><header className="dialog-header"><h2 id={titleId}>{title}</h2><button className="icon-button" aria-label="关闭" onClick={onClose}><X size={20} /></button></header>{children}</dialog>;
}
