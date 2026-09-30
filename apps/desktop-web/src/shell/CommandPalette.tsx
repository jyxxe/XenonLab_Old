import React, {useEffect, useMemo, useRef, useState} from 'react';

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
    Promise.all(providers.map(provider => Promise.resolve(provider.provide(query)).catch(() => [])))
      .then(results => {
        if (current === generation.current) setCommands(results.flat().slice(0, 60));
      })
      .catch(() => undefined);
    return () => {
      generation.current += 1;
    };
  }, [open, query, providers]);

  useEffect(() => {
    if (active >= commands.length) setActive(Math.max(0, commands.length - 1));
  }, [commands.length, active]);

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
      <div className="command-palette" role="dialog" aria-modal="true" aria-label="命令面板">
        <input
          ref={inputRef}
          className="command-input"
          type="text"
          role="combobox"
          aria-expanded="true"
          aria-controls="command-results"
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
            } else if (event.key === 'Enter' && commands[active]) {
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
                    key={command.id}
                    type="button"
                    role="option"
                    aria-selected={index === active}
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
          {!commands.length && <p className="launchpad-empty">没有匹配的命令</p>}
        </div>
      </div>
    </>
  );
}
