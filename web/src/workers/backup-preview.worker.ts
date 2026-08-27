import { normalizeBackupFile } from '../backup-normalizer';

type PreviewRequest = {
  file: File;
  maximum: number;
  maximumSourceExpanded: number;
  maximumServerExpanded: number;
};

self.onmessage = async (event: MessageEvent<PreviewRequest>) => {
  const { file, maximum, maximumSourceExpanded, maximumServerExpanded } = event.data;
  if (!(file instanceof File)) {
    self.postMessage({ ok: false, reason: 'invalid' });
    return;
  }
  self.postMessage(
    await normalizeBackupFile(file, maximum, maximumSourceExpanded, maximumServerExpanded),
  );
};
