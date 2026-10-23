export const MAX_EMBEDDING_INPUTS: number;
export const MAX_EMBEDDING_CHARACTERS: number;
export function validEmbeddingInputs(inputs: string[]): boolean;
export type EmbeddingResult = {index: number; input: string; embedding: number[]};
export function alignEmbeddingResults(data: unknown, inputs: string[]): EmbeddingResult[];
