import React, {
  createContext,
  forwardRef,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {firstEnabledIndex, lastEnabledIndex, nextEnabledIndex, typeaheadIndex} from './menu-logic.js';

export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger';
export type ControlSize = 'sm' | 'md' | 'lg';

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

export type ButtonVariant = 'default' | 'primary' | 'ghost' | 'danger';
export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ControlSize;
  loading?: boolean;
  icon?: React.ReactNode;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {variant = 'default', size = 'md', loading = false, icon, className, children, disabled, ...rest},
  ref,
) {
  return (
    <button
      {...rest}
      ref={ref}
      type={rest.type ?? 'button'}
      className={cx('ds-button', `ds-button-${variant}`, `ds-button-${size}`, className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
    >
      {loading ? <Spinner size="sm" label="处理中" /> : icon ? <span className="ds-button-icon">{icon}</span> : null}
      {children}
    </button>
  );
});

export type IconButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  variant?: ButtonVariant;
  size?: ControlSize;
};

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  {label, variant = 'ghost', size = 'md', className, children, ...rest},
  ref,
) {
  return (
    <button
      {...rest}
      ref={ref}
      type={rest.type ?? 'button'}
      className={cx('ds-button', 'ds-icon-button', `ds-button-${variant}`, `ds-button-${size}`, className)}
      aria-label={label}
      title={rest.title ?? label}
    >
      {children}
    </button>
  );
});

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement> & {invalid?: boolean}>(
  function Input({className, invalid, ...rest}, ref) {
    return <input {...rest} ref={ref} className={cx('ds-input', invalid && 'ds-invalid', className)} aria-invalid={invalid || undefined} />;
  },
);

export const Textarea = forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement> & {invalid?: boolean}>(
  function Textarea({className, invalid, ...rest}, ref) {
    return <textarea {...rest} ref={ref} className={cx('ds-input', 'ds-textarea', invalid && 'ds-invalid', className)} aria-invalid={invalid || undefined} />;
  },
);

export const Select = forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({className, children, ...rest}, ref) {
    return (
      <select {...rest} ref={ref} className={cx('ds-input', 'ds-select', className)}>
        {children}
      </select>
    );
  },
);

export function Checkbox({
  label,
  description,
  className,
  ...rest
}: React.InputHTMLAttributes<HTMLInputElement> & {label: React.ReactNode; description?: React.ReactNode}) {
  return (
    <label className={cx('ds-check', className)}>
      <input {...rest} type="checkbox" />
      <span>
        {label}
        {description ? <small>{description}</small> : null}
      </span>
    </label>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
  className,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={cx('ds-switch', checked && 'ds-switch-on', className)}
      onClick={() => onChange(!checked)}
    >
      <i aria-hidden="true" />
    </button>
  );
}

export function Toolbar({className, children, ...rest}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div {...rest} className={cx('ds-toolbar', className)} role={rest.role ?? 'toolbar'}>
      {children}
    </div>
  );
}

export function Panel({
  title,
  actions,
  className,
  children,
  ...rest
}: React.HTMLAttributes<HTMLElement> & {title?: React.ReactNode; actions?: React.ReactNode}) {
  return (
    <section {...rest} className={cx('ds-panel', className)}>
      {(title || actions) && (
        <header className="ds-panel-head">
          {title ? <h3>{title}</h3> : <span />}
          {actions ? <div className="ds-panel-actions">{actions}</div> : null}
        </header>
      )}
      {children}
    </section>
  );
}

export function Sidebar({className, children, ...rest}: React.HTMLAttributes<HTMLElement>) {
  return (
    <aside {...rest} className={cx('ds-sidebar', className)}>
      {children}
    </aside>
  );
}

export function SplitView({
  sidebar,
  inspector,
  inspectorOpen = true,
  className,
  children,
}: {
  sidebar: React.ReactNode;
  inspector?: React.ReactNode;
  inspectorOpen?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cx('ds-split', className, inspector && inspectorOpen ? 'ds-split-inspector' : null)}>
      {sidebar}
      <div className="ds-split-main">{children}</div>
      {inspector && inspectorOpen ? <div className="ds-split-inspector-pane">{inspector}</div> : null}
    </div>
  );
}

export type MenuItem = {
  id?: string;
  label?: React.ReactNode;
  icon?: React.ReactNode;
  shortcut?: string;
  disabled?: boolean;
  checked?: boolean;
  danger?: boolean;
  separator?: boolean;
  onSelect?: () => void;
};

function MenuList({
  items,
  onClose,
  autoFocus = false,
  className,
  style,
  role = 'menu',
  ariaLabel,
}: {
  items: MenuItem[];
  onClose: () => void;
  autoFocus?: boolean;
  className?: string;
  style?: React.CSSProperties;
  role?: string;
  ariaLabel?: string;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(() => (autoFocus ? firstEnabledIndex(items) : -1));
  const typed = useRef({value: '', at: 0});

  useEffect(() => {
    if (active >= 0) listRef.current?.querySelectorAll<HTMLButtonElement>('button[role="menuitem"],button[role="menuitemcheckbox"]')[active]?.focus();
  }, [active]);

  const move = (direction: number) => {
    const next = direction > 0 ? nextEnabledIndex(items, active, 1) : nextEnabledIndex(items, active, -1);
    if (next >= 0) setActive(next);
  };

  const choose = (item: MenuItem) => {
    if (item.disabled || item.separator) return;
    item.onSelect?.();
    onClose();
  };

  return (
    <div
      ref={listRef}
      role={role}
      aria-label={ariaLabel}
      className={cx('ds-menu', className)}
      style={style}
      onKeyDown={event => {
        if (event.key === 'ArrowDown') {
          event.preventDefault();
          move(1);
        } else if (event.key === 'ArrowUp') {
          event.preventDefault();
          move(-1);
        } else if (event.key === 'Home') {
          event.preventDefault();
          setActive(firstEnabledIndex(items));
        } else if (event.key === 'End') {
          event.preventDefault();
          setActive(lastEnabledIndex(items));
        } else if (event.key === 'Escape' || event.key === 'Tab') {
          event.preventDefault();
          onClose();
        } else if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
          const now = Date.now();
          typed.current.value = now - typed.current.at > 700 ? event.key : typed.current.value + event.key;
          typed.current.at = now;
          const index = typeaheadIndex(items, active, typed.current.value);
          if (index >= 0) setActive(index);
        }
      }}
    >
      {items.map((item, index) =>
        item.separator ? (
          <hr key={item.id ?? `separator-${index}`} className="ds-menu-separator" />
        ) : (
          <button
            key={item.id ?? index}
            type="button"
            role="menuitemcheckbox"
            aria-checked={item.checked ?? false}
            disabled={item.disabled}
            className={cx('ds-menu-item', item.danger && 'ds-danger', item.checked && 'ds-checked')}
            tabIndex={index === active ? 0 : -1}
            onMouseEnter={() => setActive(index)}
            onClick={() => choose(item)}
          >
            <span className="ds-menu-icon" aria-hidden="true">{item.checked ? '✓' : item.icon ?? ''}</span>
            <span className="ds-menu-label">{item.label}</span>
            {item.shortcut ? <kbd>{item.shortcut}</kbd> : null}
          </button>
        ),
      )}
    </div>
  );
}

/** Click-outside + Escape aware dropdown used by MenuButton, Dock context menus and popovers. */
export function Popover({
  open,
  onClose,
  children,
  className,
  style,
}: {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    addEventListener('pointerdown', onPointer, true);
    addEventListener('keydown', onKey);
    return () => {
      removeEventListener('pointerdown', onPointer, true);
      removeEventListener('keydown', onKey);
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div ref={ref} className={cx('ds-popover', className)} style={style} role="presentation">
      {children}
    </div>
  );
}

export function MenuButton({
  label,
  items,
  className,
  active = false,
  align = 'left',
}: {
  label: React.ReactNode;
  items: MenuItem[];
  className?: string;
  active?: boolean;
  align?: 'left' | 'right';
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={cx('ds-menu-button', className)}>
      <button
        type="button"
        className={cx('ds-menubar-item', (open || active) && 'ds-active')}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(value => !value)}
      >
        {label}
      </button>
      <Popover open={open} onClose={() => setOpen(false)} className={cx('ds-menu-popover', align === 'right' && 'ds-align-right')}>
        <MenuList items={items} onClose={() => setOpen(false)} autoFocus ariaLabel={typeof label === 'string' ? label : undefined} />
      </Popover>
    </div>
  );
}

export function ContextMenu({
  x,
  y,
  items,
  onClose,
}: {
  x: number;
  y: number;
  items: MenuItem[];
  onClose: () => void;
}) {
  const style = useMemo(() => {
    const width = 220;
    const height = Math.min(items.length * 30 + 12, innerHeight - 24);
    return {
      left: Math.max(8, Math.min(x, innerWidth - width - 8)),
      top: Math.max(8, Math.min(y, innerHeight - height - 8)),
    } as React.CSSProperties;
  }, [x, y, items.length]);
  useEffect(() => {
    const close = () => onClose();
    addEventListener('pointerdown', close, true);
    addEventListener('blur', close);
    return () => {
      removeEventListener('pointerdown', close, true);
      removeEventListener('blur', close);
    };
  }, [onClose]);
  return (
    <Popover open onClose={onClose} className="ds-context-menu" style={style}>
      <div onPointerDown={event => event.stopPropagation()}>
        <MenuList items={items} onClose={onClose} autoFocus ariaLabel="上下文菜单" />
      </div>
    </Popover>
  );
}

export function useFocusTrap(active: boolean, onEscape?: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!active) return;
    const root = ref.current;
    const focusables = () =>
      Array.from(
        root?.querySelectorAll<HTMLElement>(
          'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter(element => element.getClientRects().length > 0);
    const previous = document.activeElement as HTMLElement | null;
    focusables()[0]?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onEscape?.();
        return;
      }
      if (event.key !== 'Tab') return;
      const list = focusables();
      if (!list.length) return;
      const first = list[0];
      const last = list[list.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    addEventListener('keydown', onKey, true);
    return () => {
      removeEventListener('keydown', onKey, true);
      previous?.focus?.();
    };
  }, [active, onEscape]);
  return ref;
}

export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  size = 'md',
}: {
  open: boolean;
  title: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
  size?: 'sm' | 'md' | 'lg';
}) {
  const trap = useFocusTrap(open, onClose);
  if (!open) return null;
  return (
    <div className="ds-modal-backdrop" onPointerDown={event => event.target === event.currentTarget && onClose()}>
      <div ref={trap} role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : '对话框'} className={cx('ds-modal', `ds-modal-${size}`)}>
        <header className="ds-modal-head">
          <h2>{title}</h2>
          <IconButton label="关闭对话框" onClick={onClose}>✕</IconButton>
        </header>
        <div className="ds-modal-body">{children}</div>
        {footer ? <footer className="ds-modal-foot">{footer}</footer> : null}
      </div>
    </div>
  );
}

export function Tabs({
  tabs,
  value,
  onChange,
  className,
  ariaLabel = '选项卡',
}: {
  tabs: Array<{id: string; label: React.ReactNode; badge?: React.ReactNode; disabled?: boolean}>;
  value: string;
  onChange: (id: string) => void;
  className?: string;
  ariaLabel?: string;
}) {
  return (
    <div className={cx('ds-tabs', className)} role="tablist" aria-label={ariaLabel}>
      {tabs.map(tab => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={value === tab.id}
          aria-pressed={value === tab.id}
          disabled={tab.disabled}
          className={cx('ds-tab', value === tab.id && 'ds-active')}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
          {tab.badge != null ? <span className="ds-tab-badge">{tab.badge}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function Badge({tone = 'neutral', className, children}: {tone?: Tone; className?: string; children: React.ReactNode}) {
  return <span className={cx('ds-badge', `ds-badge-${tone}`, className)}>{children}</span>;
}

export function StatusDot({tone = 'success', pulse = false, label}: {tone?: Tone; pulse?: boolean; label?: string}) {
  return <i className={cx('ds-status-dot', `ds-status-${tone}`, pulse && 'ds-pulse')} role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true} />;
}

export function Skeleton({width = '100%', height = 14, className}: {width?: number | string; height?: number | string; className?: string}) {
  return <span className={cx('ds-skeleton', className)} style={{width, height}} aria-hidden="true" />;
}

export function Spinner({size = 'md', label = '加载中'}: {size?: ControlSize; label?: string}) {
  return <span className={cx('ds-spinner', `ds-spinner-${size}`)} role="status" aria-label={label} />;
}

export function Progress({value, max = 100, label}: {value: number; max?: number; label?: string}) {
  const ratio = max <= 0 ? 0 : Math.max(0, Math.min(1, value / max));
  return (
    <div className="ds-progress" role="progressbar" aria-valuemin={0} aria-valuemax={max} aria-valuenow={value} aria-label={label}>
      <i style={{width: `${Math.round(ratio * 100)}%`}} />
    </div>
  );
}

export function EmptyState({icon = '◇', title, description, action}: {icon?: React.ReactNode; title: React.ReactNode; description?: React.ReactNode; action?: React.ReactNode}) {
  return (
    <div className="ds-empty">
      <b aria-hidden="true">{icon}</b>
      <strong>{title}</strong>
      {description ? <p>{description}</p> : null}
      {action}
    </div>
  );
}

export function ErrorState({
  title = '出错了',
  message,
  requestId,
  onRetry,
  retryLabel = '重试',
}: {
  title?: string;
  message: React.ReactNode;
  requestId?: string | null;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div className={cx('ds-error')} role="alert">
      <b aria-hidden="true">!</b>
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
        {requestId ? <small className="ds-request-id">请求编号 {requestId}</small> : null}
      </div>
      {onRetry ? <Button onClick={onRetry}>{retryLabel}</Button> : null}
    </div>
  );
}

export type Toast = {id: string; tone?: Tone; title: React.ReactNode; description?: React.ReactNode};

const ToastContext = createContext<{push: (toast: Omit<Toast, 'id'>) => void}>({push: () => {}});
export const useToast = () => useContext(ToastContext);

export function ToastProvider({children, timeout = 6000}: {children: React.ReactNode; timeout?: number}) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback(
    (toast: Omit<Toast, 'id'>) => {
      const id = crypto.randomUUID();
      setToasts(current => [...current.slice(-4), {...toast, id}]);
      setTimeout(() => setToasts(current => current.filter(item => item.id !== id)), timeout);
    },
    [timeout],
  );
  const value = useMemo(() => ({push}), [push]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="ds-toasts" aria-live="polite">
        {toasts.map(toast => (
          <div key={toast.id} className={cx('ds-toast', `ds-toast-${toast.tone ?? 'neutral'}`)}>
            <div>
              <strong>{toast.title}</strong>
              {toast.description ? <p>{toast.description}</p> : null}
            </div>
            <IconButton label="关闭通知" onClick={() => setToasts(current => current.filter(item => item.id !== toast.id))}>✕</IconButton>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/** Headless helper: tracks the size of an element for layouts that need it. */
export function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({width: 0, height: 0});
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const update = () => setSize({width: element.clientWidth, height: element.clientHeight});
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, size] as const;
}

export {useId};
