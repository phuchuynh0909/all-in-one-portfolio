import { validateUserScript } from './scriptCompiler';
import type { OpenScriptStudySpec } from './openscript';

export interface UserScript {
  id: string;
  source: string;
  title: string;
  visible: boolean;
  /** The chart keeps running this version until Update on chart is pressed. */
  appliedSource?: string;
}

export const USER_SCRIPTS_KEY = 'chartOpenScripts.v1';
export const SCRIPT_TEMPLATE = `version 1
study("My moving average", overlay = true)

length = input(20, "Length", min = 1, max = 500, step = 1)
average = sma(close, length)

plot(average, "Average", orange, width = 2)
`;

export function readUserScripts(storage: Pick<Storage, 'getItem'>): UserScript[] {
  return parseUserScripts(JSON.parse(storage.getItem(USER_SCRIPTS_KEY) ?? '[]'));
}

export function parseUserScripts(stored: unknown): UserScript[] {
  if (!Array.isArray(stored)) throw new Error('Saved scripts are not a list.');
  const ids = new Set<string>();
  return stored.map((value: unknown) => {
    if (!value || typeof value !== 'object') throw new Error('Invalid saved script.');
    const script = value as Record<string, unknown>;
    if (typeof script.id !== 'string' || !/^user_[a-zA-Z0-9_-]+$/.test(script.id) || ids.has(script.id)
      || typeof script.source !== 'string' || typeof script.visible !== 'boolean'
      || (script.appliedSource !== undefined && typeof script.appliedSource !== 'string')) {
      throw new Error('Invalid saved script.');
    }
    ids.add(script.id);
    const { title } = validateUserScript(script.source, script.id);
    if (typeof script.appliedSource === 'string') validateUserScript(script.appliedSource, script.id);
    return { id: script.id, source: script.source, title, visible: script.visible, appliedSource: script.appliedSource as string | undefined };
  });
}

export function userScriptSpec(script: UserScript): OpenScriptStudySpec {
  const source = script.appliedSource ?? script.source;
  const { title } = validateUserScript(source, script.id);
  return { id: script.id, name: `${title} · ${script.id.slice(-8)}`, source, inputsFrom: () => ({}) };
}
