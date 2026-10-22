export type DirectoryCategory = {
  kind: 'directory' | 'other';
  name: string;
  value: number;
  directories: {name: string; value: number}[];
};
export function directoryDistribution(tables: {path: string}[]): DirectoryCategory[];
