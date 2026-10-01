import React, {useRef, useState} from 'react';
import {Badge, Button, Input, Panel, Select, Switch} from './design-system/primitives';
import type {UserPreferences} from './shell/types';

export type Preferences = UserPreferences;
export const defaultPreferences: UserPreferences = {
  theme: 'system',
  accent: 'blue',
  wallpaper: 'aurora',
  timezone: 'system',
  widgets: ['clock', 'status'],
  dock_size: 'medium',
  dock_autohide: false,
  restore_windows: true,
  animations: true,
  reduced_motion: false,
};

const widgetOptions: Array<['clock' | 'status', string]> = [
  ['clock', '时钟与日期'],
  ['status', '系统状态'],
];

export function DesktopPreferences({
  preferences,
  apply,
  saveLayout,
}: {
  api: (path: string, init?: RequestInit) => Promise<any>;
  preferences: UserPreferences;
  apply: (preferences: Partial<UserPreferences>) => Promise<void>;
  saveLayout: () => Promise<void>;
}) {
  const changeVersion = useRef(0);
  const [message, setMessage] = useState('');
  const [timezone, setTimezone] = useState(preferences.timezone);

  // Appearance changes apply immediately and the shell persists them; macOS-style
  // settings have no separate confirmation step for display options.
  const update = async (patch: Partial<UserPreferences>) => {
    const version=++changeVersion.current;
    setMessage('保存中…');
    try {await apply(patch);if(version===changeVersion.current)setMessage('已应用并保存')}
    catch (error) {if(version===changeVersion.current)setMessage('保存失败：'+String(error))}
  };

  return (
    <section className="query preference-form">
      <h2>外观与桌面</h2>
      <p>设置保存在服务器端，登录同一身份的设备共享这些偏好。</p>

      <Panel title="外观">
        <label>
          主题
          <Select value={preferences.theme} onChange={event => update({theme: event.target.value as UserPreferences['theme']})}>
            <option value="system">跟随系统</option>
            <option value="light">浅色</option>
            <option value="dark">深色</option>
          </Select>
        </label>
        <label>
          强调色
          <Select value={preferences.accent} onChange={event => update({accent: event.target.value as UserPreferences['accent']})}>
            <option value="blue">蓝色</option>
            <option value="purple">紫色</option>
            <option value="green">绿色</option>
            <option value="orange">橙色</option>
          </Select>
        </label>
        <label>
          壁纸
          <Select value={preferences.wallpaper} onChange={event => update({wallpaper: event.target.value as UserPreferences['wallpaper']})}>
            <option value="aurora">极光</option>
            <option value="ocean">深海</option>
            <option value="sunset">晚霞</option>
            <option value="silver">银色</option>
          </Select>
        </label>
      </Panel>

      <Panel title="桌面">
        <label>
          时区
          <Input
            list="timezones"
            value={timezone}
            onChange={event => setTimezone(event.target.value)}
            onBlur={() => timezone !== preferences.timezone && update({timezone})}
            onKeyDown={event => {
              if (event.key === 'Enter' && timezone !== preferences.timezone) update({timezone});
            }}
          />
          <datalist id="timezones">
            {['system', 'Asia/Shanghai', 'UTC', 'America/New_York', 'Europe/London', 'Asia/Tokyo'].map(zone => (
              <option key={zone} value={zone} />
            ))}
          </datalist>
        </label>
        <p className="muted">system 使用浏览器时区，其他值使用 IANA 时区名称；离开输入框或按 Enter 时保存。</p>
        <fieldset className="widget-options">
          <legend>桌面组件</legend>
          {widgetOptions.map(([id, title]) => (
            <label key={id}>
              <input
                type="checkbox"
                checked={preferences.widgets.includes(id)}
                onChange={event =>
                  update({
                    widgets: event.target.checked ? [...preferences.widgets, id] : preferences.widgets.filter(widget => widget !== id),
                  })
                }
              />
              {title}
            </label>
          ))}
        </fieldset>
        <div className="preference-switch">
          <span>
            恢复上次窗口
            <small>重新打开桌面时恢复窗口位置、大小与最小化状态</small>
          </span>
          <Switch checked={preferences.restore_windows} label="恢复上次窗口" onChange={checked => update({restore_windows: checked})} />
        </div>
        <div className="preference-switch">
          <span>
            界面动画
            <small>窗口、菜单与启动器的过渡效果</small>
          </span>
          <Switch checked={preferences.animations} label="界面动画" onChange={checked => update({animations: checked})} />
        </div>
        <div className="preference-switch">
          <span>
            减少动态效果
            <small>优先考虑可访问性，立即切换界面状态</small>
          </span>
          <Switch checked={preferences.reduced_motion} label="减少动态效果" onChange={checked => update({reduced_motion: checked})} />
        </div>
      </Panel>

      <Panel title="Dock">
        <label>
          大小
          <Select value={preferences.dock_size} onChange={event => update({dock_size: event.target.value as UserPreferences['dock_size']})}>
            <option value="small">小</option>
            <option value="medium">中</option>
            <option value="large">大</option>
          </Select>
        </label>
        <div className="preference-switch">
          <span>
            自动隐藏
            <small>鼠标移出后 Dock 收起，移到底部边缘时显示</small>
          </span>
          <Switch checked={preferences.dock_autohide} label="Dock 自动隐藏" onChange={checked => update({dock_autohide: checked})} />
        </div>
        <p className="muted">固定项目与顺序由 Dock 右键菜单管理，并随窗口布局一起保存。</p>
      </Panel>

      <div className="preference-actions">
        <Button onClick={() => update(defaultPreferences)}>恢复默认外观</Button>
      </div>

      <Panel title="窗口布局" actions={<Badge tone="accent">自动保存</Badge>}>
        <p className="muted">窗口位置、大小与 Dock 顺序会在变动后自动保存；也可以立即保存当前布局。</p>
        <Button
          onClick={async () => {
            try {
              await saveLayout();
              setMessage('当前窗口位置、大小与 Dock 已保存');
            } catch (error) {
              setMessage(String(error));
            }
          }}
        >
          立即保存当前布局
        </Button>
      </Panel>

      <p role="status">{message}</p>
    </section>
  );
}
