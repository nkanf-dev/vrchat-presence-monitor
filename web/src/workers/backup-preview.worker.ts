import { summarizeBackup } from '../backup';

type PreviewRequest = { file: File; maximum: number };

self.onmessage = async (event: MessageEvent<PreviewRequest>) => {
  const { file, maximum } = event.data;
  if (!(file instanceof File) || !Number.isFinite(maximum) || maximum <= 0 || file.size > maximum) {
    self.postMessage({ ok: false });
    return;
  }
  try {
    const parsed: unknown = JSON.parse(await file.text());
    self.postMessage(summarizeBackup(parsed));
  } catch {
    self.postMessage({ ok: false });
  }
};
