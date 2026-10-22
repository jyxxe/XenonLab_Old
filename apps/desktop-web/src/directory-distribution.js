/** Summarize only the loaded tables, with deterministic count/name ordering. */
export function directoryDistribution(tables) {
  const counts = new Map();
  for (const table of tables) {
    const name = table.path.split('/').filter(Boolean)[0] || '/';
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  const directories = [...counts].map(([name, value]) => ({name, value}))
    .sort((a, b) => b.value - a.value || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  const categories = directories.slice(0, 6).map(directory => ({
    kind: 'directory', name: directory.name, value: directory.value, directories: [directory],
  }));
  if (directories.length > 6) {
    const remaining = directories.slice(6);
    categories.push({kind: 'other', name: '其他（汇总）',
      value: remaining.reduce((sum, directory) => sum + directory.value, 0), directories: remaining});
  }
  return categories;
}
