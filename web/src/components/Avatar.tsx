import { useState } from 'react';

import type { Friend } from '../api';
import { avatarSource, friendName, initials } from '../format';

export function Avatar({ friend, size = 'medium' }: { friend: Friend; size?: 'small' | 'medium' | 'large' }) {
  const [failed, setFailed] = useState(false);
  const source = avatarSource(friend);
  const name = friendName(friend);

  return (
    <span className={`avatar avatar-${size}`} aria-hidden="true">
      {source && !failed ? (
        <img
          src={source}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
        />
      ) : (
        <span>{initials(name)}</span>
      )}
    </span>
  );
}
