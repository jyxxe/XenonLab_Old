export type UploadSession = {uploadId: string; nextPart: number; partCount: number; partSize: number};
export type UploadState = {resume: (UploadSession & {file: File}) | null; busy: boolean; progress: number | null; message: string};
export type UploadStore = {readonly alive: boolean; get(key: string): UploadState; set(key: string, patch: Partial<UploadState>): void; subscribe(key: string, notify: () => void): () => void; dispose(): void};
export function createUploadStore(): UploadStore;
