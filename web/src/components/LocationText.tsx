import { useQuery } from '@tanstack/react-query';

import { worldIdFromLocation } from '../analytics';
import { getWorld } from '../api';
import { locationLabel } from '../format';

const waiting: Array<() => void> = [];
let activeWorldRequests = 0;

const resolveWorld = async (worldId: string) => {
  if (activeWorldRequests >= 2) {
    await new Promise<void>((resolve) => waiting.push(resolve));
  }
  activeWorldRequests += 1;
  try {
    return await getWorld(worldId);
  } finally {
    activeWorldRequests -= 1;
    waiting.shift()?.();
  }
};

export function LocationText({ location, status }: { location: string; status: string }) {
  const worldId = worldIdFromLocation(location);
  const world = useQuery({
    queryKey: ['world', worldId],
    queryFn: () => resolveWorld(worldId),
    enabled: Boolean(worldId),
    staleTime: 6 * 60 * 60_000,
    retry: 1,
  });

  if (!worldId) return <>{locationLabel(location, status)}</>;
  return <span title={worldId}>{world.data?.name || worldId}</span>;
}
