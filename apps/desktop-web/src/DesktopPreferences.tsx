import React, {useRef, useState} from 'react';
import {Badge, Button, Input, Panel, Select, Switch} from './design-system/primitives';
import type {DesktopApp, UserPreferences, WallpaperMode, WidgetId} from './shell/types';
import {customWallpaperStyle} from './wallpaper.js';

export type Preferences = UserPreferences;
export const defaultPreferences: UserPreferences = {
  theme: 'system',
  accent: 'blue',
  wallpaper: 'aurora',
  wallpaper_mode: 'solid',
  wallpaper_color: '#101828',
  wallpaper_gradient_from: '#0b1220',
  wallpaper_gradient_to: '#1d4ed8',
  wallpaper_gradient_angle: 160,
  wallpaper_image_version: 0,
  wallpaper_dim: 0,
  timezone: 'system',
  widgets: ['clock', 'status'],
  notes: '',
  dock_size: 'medium',
  dock_autohide: false,
  restore_windows: true,
  animations: true,
  reduced_motion: false,
};

const widgetOptions: Array<[WidgetId, string]> = [
  ['clock', '时钟与日期'],
  ['calendar', '日历'],
  ['status', '系统状态'],
  ['work', '任务队列'],
  ['search', '知识库搜索'],
  ['notes', '快速便签'],
];

// Wallpapers are user photos, so cap the stored data URL: downscale to a
// 2048px JPEG in a canvas before it is written into server-side preferences.
async function downscaleImageToDataUrl(file: File, maxEdge = 2048): Promise<string> {
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    const scale = Math.min(1, maxEdge / Math.max(image.naturalWidth, image.naturalHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const context = canvas.getContext('2d');
    if (!context) throw new Error('canvas unavailable');
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.85);
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function DesktopPreferences({
  api, preferences, apply, saveLayout,
  apps, dockIds, togglePin, movePinned,
}: {
  apps: DesktopApp[];
  dockIds: string[];
  togglePin: (id: string) => void;
  movePinned: (id: string, direction: number) => void;
  api: (path: string, init?: RequestInit) => Promise<any>;
  preferences: UserPreferences;
  apply: (preferences: Partial<UserPreferences>) => Promise<void>;
  saveLayout: () => Promise<void>;
}) {
  const changeVersion = useRef(0);
  const [message, setMessage] = useState('');
  const [timezone, setTimezone] = useState(preferences.timezone);
  // The preview uses the locally chosen gradient/image before the server round
  // trip completes; the image override mirrors the wallpaper-image endpoint.
  const [localImage, setLocalImage] = useState<string | null>(null);
  const customPreview = customWallpaperStyle(preferences, localImage ?? undefined);

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
            <option value="custom">自定义</option>
          </Select>
        </label>
        {preferences.wallpaper === 'custom' && (
          <div className="wallpaper-custom">
            <label>
              方式
              <Select value={preferences.wallpaper_mode || 'solid'} onChange={event => update({wallpaper_mode: event.target.value as WallpaperMode})}>
                <option value="solid">纯色</option>
                <option value="gradient">渐变</option>
                <option value="image">图片</option>
              </Select>
            </label>
            {(preferences.wallpaper_mode || 'solid') === 'solid' && (
              <label>
                颜色
                <input
                  className="color-input"
                  type="color"
                  value={preferences.wallpaper_color || '#101828'}
                  aria-label="壁纸颜色"
                  onChange={event => update({wallpaper_color: event.target.value})}
                />
              </label>
            )}
            {preferences.wallpaper_mode === 'gradient' && (
              <>
                <label>
                  起始颜色
                  <input
                    className="color-input"
                    type="color"
                    value={preferences.wallpaper_gradient_from || '#0b1220'}
                    aria-label="渐变起始颜色"
                    onChange={event => update({wallpaper_gradient_from: event.target.value})}
                  />
                </label>
                <label>
                  结束颜色
                  <input
                    className="color-input"
                    type="color"
                    value={preferences.wallpaper_gradient_to || '#1d4ed8'}
                    aria-label="渐变结束颜色"
                    onChange={event => update({wallpaper_gradient_to: event.target.value})}
                  />
                </label>
                <label>
                  角度 {preferences.wallpaper_gradient_angle ?? 160}°
                  <input
                    type="range"
                    min={0}
                    max={360}
                    value={preferences.wallpaper_gradient_angle ?? 160}
                    aria-label="渐变角度"
                    onChange={event => update({wallpaper_gradient_angle: Number(event.target.value)})}
                  />
                </label>
              </>
            )}
            {preferences.wallpaper_mode === 'image' && (
              <div className="wallpaper-image-controls">
                <label>
                  图片文件
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    aria-label="选择壁纸图片"
                    onChange={async event => {
                      const file = event.target.files?.[0];
                      event.target.value = '';
                      if (!file) return;
                      if (!file.type.startsWith('image/')) {
                        setMessage('壁纸仅支持 PNG / JPEG / WebP 图片');
                        return;
                      }
                      setMessage('正在处理图片…');
                      try {
                        const dataUrl = await downscaleImageToDataUrl(file);
                        // The blob is stored through the dedicated wallpaper-image
                        // endpoint; preferences only carry the version counter.
                        const put = await api('/users/me/wallpaper-image', {method: 'PUT', body: JSON.stringify({image: dataUrl})});
                        setLocalImage(dataUrl);
                        await update({wallpaper_mode: 'image', wallpaper_image_version: put?.version ?? 0});
                      } catch (error) {
                        setMessage('图片保存失败：' + String(error));
                      }
                    }}
                  />
                </label>
                {preferences.wallpaper_image_version ? (
                  <Button
                    size="sm"
                    onClick={async () => {
                      try {
                        const put = await api('/users/me/wallpaper-image', {method: 'PUT', body: JSON.stringify({image: ''})});
                        setLocalImage('');
                        await update({wallpaper_mode: 'solid', wallpaper_image_version: put?.version ?? 0});
                      } catch (error) {
                        setMessage('移除图片失败：' + String(error));
                      }
                    }}
                  >
                    移除自定义图片
                  </Button>
                ) : (
                  <p className="muted">尚未设置图片，桌面暂时使用渐变兜底。</p>
                )}
              </div>
            )}
            <label>
              压暗 {preferences.wallpaper_dim ?? 0}%
              <input
                type="range"
                min={0}
                max={90}
                value={preferences.wallpaper_dim ?? 0}
                aria-label="壁纸压暗程度"
                onChange={event => update({wallpaper_dim: Number(event.target.value)})}
              />
            </label>
            <div className="wallpaper-preview" aria-label="壁纸预览">
              <div
                className="wallpaper-preview-surface"
                data-mode={customPreview?.imageMode ? 'image' : 'other'}
                style={{backgroundColor: customPreview?.backgroundColor, backgroundImage: customPreview?.backgroundImage}}
              />
            </div>
            <p className="muted">图片会在本机压缩到 2048px / 2MB 内再保存；自定义壁纸跟随账户在所有设备生效。</p>
          </div>
        )}
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
        <p className="muted">固定项目与顺序随窗口布局一起保存；也可在 Dock 按 Shift+F10 打开菜单。</p>
        <ul className="pinned-apps" aria-label="Dock 固定应用">
          {[...dockIds.map(id => apps.find(app => app.id === id)).filter((app): app is DesktopApp => Boolean(app)), ...apps.filter(app => !dockIds.includes(app.id))].map(app => {
            const index = dockIds.indexOf(app.id);
            return <li key={app.id}>
              <label><input type="checkbox" checked={index >= 0} onChange={() => togglePin(app.id)} />{app.title}</label>
              <Button size="sm" aria-label={`向前移动 ${app.title}`} disabled={index <= 0} onClick={() => movePinned(app.id, -1)}>↑</Button>
              <Button size="sm" aria-label={`向后移动 ${app.title}`} disabled={index < 0 || index === dockIds.length - 1} onClick={() => movePinned(app.id, 1)}>↓</Button>
            </li>;
          })}
        </ul>
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
