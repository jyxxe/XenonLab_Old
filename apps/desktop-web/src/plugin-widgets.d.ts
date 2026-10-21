export type PluginWidgetRecord = {plugin_id: string; [key: string]: unknown};

export function selectedPluginWidgets<T extends {plugin_id: string}>(
  selected: string[] | null | undefined,
  widgets: T[] | null | undefined,
): T[];
