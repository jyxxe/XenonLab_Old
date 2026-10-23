export const MAX_EMBEDDING_INPUTS = 32;
export const MAX_EMBEDDING_CHARACTERS = 32768;

export function validEmbeddingInputs(inputs) {
  return Array.isArray(inputs) && inputs.length >= 1 && inputs.length <= MAX_EMBEDDING_INPUTS
    && inputs.every(input => typeof input === 'string' && input.trim() && input.length <= MAX_EMBEDDING_CHARACTERS);
}

/** Validate the complete result before showing any vector, then align by API index. */
export function alignEmbeddingResults(data, inputs) {
  const invalid = () => {throw new Error('Embedding 服务返回了无法识别的向量数据或输入索引。');};
  if (!Array.isArray(data) || data.length !== inputs.length) invalid();
  const results = new Array(inputs.length);
  for (const item of data) {
    const index = item?.index;
    if (!Number.isInteger(index) || index < 0 || index >= inputs.length || results[index]
      || !Array.isArray(item.embedding) || !item.embedding.length
      || !item.embedding.every(value => typeof value === 'number' && Number.isFinite(value))) invalid();
    results[index] = {index, input: inputs[index], embedding: item.embedding};
  }
  return results;
}
