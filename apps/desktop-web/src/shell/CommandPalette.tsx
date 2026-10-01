import React, {useEffect, useMemo, useRef, useState} from 'react';
import {useFocusTrap} from '../design-system/primitives';

export type Command = {
  id: string;
  title: string;
  subtitle?: string;
  group: string;
  icon?: string;
  keywords?: string;
  run: () => void;
};

export type CommandProvider = {
  id: string;
  group: string;
  provide: (query: string) => Command[] | Promise<Command[]>;
};

export function CommandPalette({
  open,
  providers,
  onClose,
}: {
  open: boolean;
  providers: CommandProvider[];
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [commands, setCommands] = useState<Command[]>([]);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const trap = useFocusTrap(open, onClose);
  const [loading, setLoading] = useState(false);
  const [providerErrors, setProviderErrors] = useState<string[]>([]);
  const generation = useRef(0);

  useEffect(() => {
    if (open) {
      setQuery('');
      setActive(0);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  useEffect(() => {
    setActive(0);
  }, [query]);

  useEffect(() => {
    if (!open) return;
    const current = ++generation.current;
    setLoading(true);
    setCommands([]);
    Promise.allSettled(providers.map(provider => Promise.resolve().then(() => provider.provide(query))))
      .then(results => {
        if (current !== generation.current) return;
        setCommands(results.flatMap(result => result.status === 'fulfilled' ? result.value : []).slice(0,60));
        setProviderErrors(results.flatMap((result,index) => result.status === 'rejected' ? [`${providers[index].group}：${String(result.reason)}`] : []));
        setLoading(false);
      });
    return () => {
      generation.current += 1;
    };
  }, [open, query, providers]);

  useEffect(() => {
    if (active >= commands.length) setActive(Math.max(0, commands.length - 1));
  }, [commands.length, active]);

  useEffect(() => {
    trap.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({block: 'nearest'});
  }, [active]);

  const groups = useMemo(() => {
    const map = new Map<string, Command[]>();
    for (const command of commands) {
      if (!map.has(command.group)) map.set(command.group, []);
      map.get(command.group)!.push(command);
    }
    return [...map.entries()];
  }, [commands]);

  if (!open) return null;

  const run = (command: Command) => {
    onClose();
    setTimeout(() => command.run(), 0);
  };

  return (
    <>
      <div className="command-backdrop" onPointerDown={onClose} />
      <div ref={trap} className="command-palette" role="dialog" aria-modal="true" aria-label="命令面板">
        <input
          ref={inputRef}
          className="command-input"
          type="text"
          role="combobox"
          aria-expanded="true"
          aria-controls="command-results"
          aria-activedescendant={commands[active] ? `command-${commands[active].id}` : undefined}
          aria-label="搜索命令与应用"
          placeholder="搜索应用、窗口或命令…"
          value={query}
          onChange={event => setQuery(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'ArrowDown') {
              event.preventDefault();
              setActive(value => Math.min(value + 1, commands.length - 1));
            } else if (event.key === 'ArrowUp') {
              event.preventDefault();
              setActive(value => Math.max(value - 1, 0));
            } else if (event.key === 'Enter' && !loading && commands[active]) {
              event.preventDefault();
              run(commands[active]);
            } else if (event.key === 'Escape') {
              event.preventDefault();
              onClose();
            }
          }}
        />
        <div className="command-results" id="command-results" role="listbox">
          {groups.map(([group, items]) => (
            <div key={group}>
              <div className="command-group">{group}</div>
              {items.map(command => {
                const index = commands.indexOf(command);
                return (
                  <button
                    id={`command-${command.id}`}
                    key={command.id}
                    type="button"
                    role="option"
                    aria-selected={index === active}
                    disabled={loading}
                    className={`command-item${index === active ? ' ds-active' : ''}`}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => run(command)}
                  >
                    <b aria-hidden="true">{command.icon || '›'}</b>
                    <span>{command.title}</span>
                    {command.subtitle ? <small>{command.subtitle}</small> : null}
                  </button>
                );
              })}
            </div>
          ))}
          {loading && <p role="status" className="launchpad-empty">正在搜索命令…</p>}
          {providerErrors.map(error => <p role="alert" key={error} className="launchpad-empty">{error}</p>)}
          {!loading && !commands.length && <p className="launchpad-empty">没有匹配的命令</p>}
        </div>
      </div>
    </>
  );
}
