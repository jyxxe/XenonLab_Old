// Prompt catalogue data for the Xenon Panel "提示词" section.
//
// The Markdown bodies and the manifest are imported with Vite's `?raw` suffix so
// the content is bundled into the desktop build. The panel therefore works on LAN
// and public origins without a runtime request to a static file route.
//
// Interface (fixed, see docs/agent-prompts/manifest.json):
//   {version, updated, note, prompts:[{id,title,audience,network,file,summary}]}
import manifestRaw from '../../../docs/agent-prompts/manifest.json?raw';
import codexBody from '../../../docs/agent-prompts/codex.md?raw';
import dshBody from '../../../docs/agent-prompts/dsh.md?raw';
import opencodeBody from '../../../docs/agent-prompts/opencode.md?raw';
import generalAgentLanBody from '../../../docs/agent-prompts/general-agent-lan.md?raw';
import generalAgentWanBody from '../../../docs/agent-prompts/general-agent-wan.md?raw';

export type PromptNetwork = 'lan' | 'wan';

export type AgentPrompt = {
  id: string;
  title: string;
  audience: string;
  network: PromptNetwork;
  summary: string;
  file: string;
  body: string;
};

type ManifestPrompt = Omit<AgentPrompt, 'body'>;

type AgentPromptManifest = {
  version: number | string;
  updated: string;
  note?: string;
  prompts: ManifestPrompt[];
};

const bodies: Record<string, string | undefined> = {
  'codex.md': codexBody,
  'dsh.md': dshBody,
  'opencode.md': opencodeBody,
  'general-agent-lan.md': generalAgentLanBody,
  'general-agent-wan.md': generalAgentWanBody,
};

const manifest = JSON.parse(manifestRaw) as AgentPromptManifest;

export const agentPromptManifest = manifest;

/** Typed catalogue; every entry carries its Markdown body bundled at build time. */
export const agentPrompts: AgentPrompt[] = manifest.prompts.map(prompt => ({
  id: prompt.id,
  title: prompt.title,
  audience: prompt.audience,
  network: prompt.network,
  summary: prompt.summary,
  file: prompt.file,
  body: bodies[prompt.file] ?? '',
}));

/** Human readable network label used by the panel badge. */
export function networkLabel(network: PromptNetwork): string {
  return network === 'lan' ? '内网' : '外网';
}
