import { User } from '@phosphor-icons/react';

export function Avatar({ role }) {
  return role === 'user'
    ? <span className="avatar user-avatar"><User size={23} weight="fill" /></span>
    : <img className="avatar ai-avatar" src="./assets/loops-icon.png" alt="" />;
}
