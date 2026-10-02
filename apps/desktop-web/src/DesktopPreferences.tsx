import React, {useRef, useState} from 'react';
import {Badge, Button, Input, Panel, Select, Switch} from './design-system/primitives';
import type {APIRaw, DesktopApp, PreferenceApplyOptions, UserPreferences, WallpaperMode} from './shell/types';
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
  wallpaper_dim: 0,
  timezone: 'system',
  widgets: ['clock', 'status'],
  shortcut_apps: [],
  notes: '',
  dock_size: 'medium',
  dock_autohide: false,
  restore_windows: true,
  animations: true,
  reduced_motion: false,
};

const widgetOptions: Array<[UserPreferences['widgets'][number], string]> = [
  ['clock', '时钟与日期'],
  ['status', '系统状态'],
  ['calendar', '日历'],
  ['app-shortcuts', '应用快捷入口'],
  ['work', '任务队列'],
  ['notes', '快速便签'],
  ['search', '知识库搜索'],
];

export function DesktopPreferences({
  preferences,
  apply,
  saveLayout,
  apps, dockIds, togglePin, movePinned,
  apiRaw,
}: {
  apps: DesktopApp[];
  dockIds: string[];
  togglePin: (id: string) => void;
  movePinned: (id: string, direction: number) => void;
  api: (path: string, init?: RequestInit) => Promise<any>;
  apiRaw: APIRaw;
  preferences: UserPreferences;
  apply: (preferences: Partial<UserPreferences>, options?: PreferenceApplyOptions) => Promise<void>;
  saveLayout: () => Promise<void>;
}) {
  const changeVersion = useRef(0);
  const [message, setMessage] = useState('');
  const [wallpaperBusy, setWallpaperBusy] = useState(false);
  const [customWallpaperAvailable, setCustomWallpaperAvailable] = useState(Boolean(preferences.custom_wallpaper_available));
  const [timezone, setTimezone] = useState(preferences.timezone);
  React.useEffect(() => setCustomWallpaperAvailable(Boolean(preferences.custom_wallpaper_available)), [preferences.custom_wallpaper_available]);
  const wallpaperPreview = customWallpaperStyle(preferences);

  // Appearance changes apply immediately and the shell persists them; macOS-style
  // settings have no separate confirmation step for display options.
  const update = async (patch: Partial<UserPreferences>) => {
    for (const key of Object.keys(patch) as Array<keyof UserPreferences>) {delete sliderUnconfirmed.current[key];delete sliderPatch.current[key];delete sliderEdits.current[key];}
    if (!Object.keys(sliderUnconfirmed.current).length) {setSliderMessage('');setSliderFailed(false);}
    const version=++changeVersion.current;
    setMessage('保存中…');
    try {await apply(patch);if(version===changeVersion.current)setMessage('已应用并保存')}
    catch (error) {if(version===changeVersion.current)setMessage('保存失败：'+String(error))}
  };

  const sliderDragging = useRef<HTMLInputElement | null>(null);
  const sliderGeneration = useRef(0);
  const sliderUnconfirmed = useRef<Partial<UserPreferences>>({});
  const sliderEditSerial = useRef(0);
  const sliderEdits = useRef<Partial<Record<keyof UserPreferences, number>>>({});
  const sliderRequest = useRef(0);
  const sliderPatch = useRef<Partial<UserPreferences>>({});
  const sliderTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [sliderFailed, setSliderFailed] = useState(false);
  const [sliderMessage, setSliderMessage] = useState('');
  const cancelSliderTimer = () => {if (sliderTimer.current) clearTimeout(sliderTimer.current);sliderTimer.current = null;};
  const flushSlider = async (keepalive = false) => {
    cancelSliderTimer();
    if (!Object.keys(sliderPatch.current).length) return;
    const patch = {...sliderUnconfirmed.current};
    const edits = {...sliderEdits.current};
    const request = ++sliderRequest.current;
    sliderPatch.current = {};
    const version = ++changeVersion.current;
    const generation = sliderGeneration.current;
    setSliderMessage('保存中…');
    try {
      await apply(patch, {keepalive});
      if (generation !== sliderGeneration.current) return;
      for (const key of Object.keys(patch) as Array<keyof UserPreferences>) {
        if (sliderEdits.current[key] === edits[key]) {delete sliderUnconfirmed.current[key];delete sliderPatch.current[key];delete sliderEdits.current[key];}
      }
      if (!Object.keys(sliderUnconfirmed.current).length) {setSliderMessage('');setSliderFailed(false);}
      if (version === changeVersion.current) setMessage('已应用并保存');
    } catch (error) {
      // Retain failed values for an explicit retry; newer input wins per field.
      if (generation !== sliderGeneration.current || request !== sliderRequest.current) return;
      if (!Object.keys(patch).some(key => sliderEdits.current[key as keyof UserPreferences] !== undefined && sliderEdits.current[key as keyof UserPreferences] === edits[key as keyof UserPreferences])) return;
      sliderPatch.current = {...sliderUnconfirmed.current};
      setSliderMessage('壁纸保存失败：'+String(error));setSliderFailed(true);
    }
  };
  const flushRef = useRef(flushSlider);
  flushRef.current = flushSlider;
  React.useEffect(() => {
    setSliderMessage('');setSliderFailed(false);sliderDragging.current = null;
    const hidden = () => {if (document.visibilityState === 'hidden') void flushRef.current(true);};
    document.addEventListener('visibilitychange', hidden);
    const flushOnLeave = flushSlider;
    return () => {document.removeEventListener('visibilitychange', hidden);cancelSliderTimer();void flushOnLeave();++sliderGeneration.current;++changeVersion.current;sliderPatch.current = {};sliderUnconfirmed.current = {};sliderEdits.current = {};};
  }, [apply]);
  const updateSlider = (patch: Partial<UserPreferences>) => {
    const version = ++changeVersion.current;
    const generation = sliderGeneration.current;
    for (const key of Object.keys(patch) as Array<keyof UserPreferences>) sliderEdits.current[key] = ++sliderEditSerial.current;
    sliderUnconfirmed.current = {...sliderUnconfirmed.current, ...patch};
    sliderPatch.current = {...sliderUnconfirmed.current};
    setSliderFailed(false);setSliderMessage('待保存…');
    void apply(patch, {previewOnly: true}).catch(error => {if (generation === sliderGeneration.current && version === changeVersion.current) {cancelSliderTimer();sliderPatch.current = {};sliderUnconfirmed.current = {};sliderEdits.current = {};setSliderMessage('');setMessage('保存失败：'+String(error));}});
    cancelSliderTimer();
    if (!sliderDragging.current) sliderTimer.current = setTimeout(() => void flushRef.current(), 350);
  };

  const endSliderDrag = () => {sliderDragging.current = null;void flushSlider();};
  const sliderEvents = {
    onPointerDown: (event: React.PointerEvent<HTMLInputElement>) => {sliderDragging.current = event.currentTarget;cancelSliderTimer();},
    onPointerUp: endSliderDrag,
    onPointerCancel: endSliderDrag,
    onBlur: (event: React.FocusEvent<HTMLInputElement>) => {if (!sliderDragging.current || sliderDragging.current === event.currentTarget) endSliderDrag();},
  };

  const uploadWallpaper = async (file?: File) => {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      setMessage('壁纸文件超过 5 MiB，请选择较小的 JPEG、PNG 或 WebP 图片。');
      return;
    }
    setWallpaperBusy(true);
    setMessage('正在上传并验证壁纸…');
    try {
      await apiRaw('/users/me/wallpaper', {
        method: 'PUT',
        headers: {'Content-Type': file.type || 'application/octet-stream'},
        body: file,
      });
      setCustomWallpaperAvailable(true);
      await apply({wallpaper: 'custom', wallpaper_mode: 'image'});
      setMessage('自定义壁纸已保存到此账户');
    } catch (error) {
      setMessage(`壁纸上传失败：${String(error)}`);
    } finally {
      setWallpaperBusy(false);
    }
  };

  const removeWallpaper = async () => {
    setWallpaperBusy(true);
    setMessage('正在移除自定义壁纸…');
    try {
      const response = await apiRaw('/users/me/wallpaper', {method: 'DELETE'});
      const result = await response.json();
      setCustomWallpaperAvailable(false);
      await apply({wallpaper: result.data.wallpaper});
      setMessage('自定义壁纸已移除');
    } catch (error) {
      setMessage(`移除壁纸失败：${String(error)}`);
    } finally {
      setWallpaperBusy(false);
    }
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
            <option value="custom">自定义壁纸</option>
          </Select>
        </label>
        {preferences.wallpaper === 'custom' && <div className="wallpaper-custom">
          <label>类型
            <Select value={preferences.wallpaper_mode || 'solid'} onChange={event => update({wallpaper_mode: event.target.value as WallpaperMode})}>
              <option value="solid">纯色</option><option value="gradient">渐变</option>
              {customWallpaperAvailable && <option value="image">账户图片</option>}
            </Select>
          </label>
          {(preferences.wallpaper_mode || 'solid') === 'solid' && <label>纯色
            <input className="color-input" type="color" aria-label="壁纸纯色" value={preferences.wallpaper_color || '#101828'} onChange={event => update({wallpaper_color: event.target.value})} />
          </label>}
          {preferences.wallpaper_mode === 'gradient' && <>
            <label>渐变起始色<input className="color-input" type="color" aria-label="渐变起始色" value={preferences.wallpaper_gradient_from || '#0b1220'} onChange={event => update({wallpaper_gradient_from: event.target.value})} /></label>
            <label>渐变结束色<input className="color-input" type="color" aria-label="渐变结束色" value={preferences.wallpaper_gradient_to || '#1d4ed8'} onChange={event => update({wallpaper_gradient_to: event.target.value})} /></label>
            <label>渐变角度 {preferences.wallpaper_gradient_angle ?? 160}°<input type="range" min="0" max="360" aria-label="渐变角度" value={preferences.wallpaper_gradient_angle ?? 160} onChange={event => updateSlider({wallpaper_gradient_angle: Number(event.target.value)})} {...sliderEvents} /></label>
          </>}
          <label>压暗 {preferences.wallpaper_dim ?? 0}%<input type="range" min="0" max="90" aria-label="壁纸压暗程度" value={preferences.wallpaper_dim ?? 0} onChange={event => updateSlider({wallpaper_dim: Number(event.target.value)})} {...sliderEvents} /></label>
          <div className="wallpaper-preview" aria-label="壁纸预览"><div className="wallpaper-preview-surface" style={wallpaperPreview ? {backgroundColor: wallpaperPreview.backgroundColor, backgroundImage: wallpaperPreview.backgroundImage, backgroundSize: wallpaperPreview.backgroundSize, backgroundPosition: wallpaperPreview.backgroundPosition} : undefined} /></div>
          {preferences.wallpaper_mode === 'image' && <p className="muted">账户图片从已验证的图片接口加载；请在桌面背景确认实际效果。</p>}
        </div>}
        <label>
          上传账户壁纸（JPEG、PNG 或 WebP，最多 5 MiB）
          <input type="file" accept="image/jpeg,image/png,image/webp" disabled={wallpaperBusy} aria-label="选择自定义壁纸" onChange={event => {const file=event.currentTarget.files?.[0];event.currentTarget.value='';void uploadWallpaper(file);}} />
        </label>
        {customWallpaperAvailable && <Button size="sm" disabled={wallpaperBusy} onClick={() => void removeWallpaper()}>移除账户图片</Button>}
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
                  checked={(preferences.widgets || []).includes(id)}
                onChange={event =>
                  update({
                    widgets: event.target.checked ? [...new Set([...preferences.widgets, id])] : preferences.widgets.filter(widget => widget !== id),
                  })
                }
              />
              {title}
            </label>
          ))}
        </fieldset>
        {(preferences.widgets || []).includes('app-shortcuts') && <fieldset className="widget-shortcut-options">
          <legend>快捷入口应用</legend>
          <p className="muted">只显示当前账户已授权且可打开的应用；未选择时显示前四个可用应用。</p>
          {apps.map(app => <label key={app.id}>
            <input
              type="checkbox"
              checked={(preferences.shortcut_apps || []).includes(app.id)}
              onChange={event => {
                const current = preferences.shortcut_apps || [];
                const next = event.target.checked
                  ? [...current, app.id].slice(-12)
                  : current.filter(id => id !== app.id);
                void update({shortcut_apps: next});
              }}
            />
            {app.icon} {app.title}
          </label>)}
          {!apps.length && <p className="muted">当前没有可用的授权应用。</p>}
        </fieldset>}
        {preferences.widgets.includes('notes') && <p className="muted">便签最多 4,000 字；编辑后自动同步，失去焦点或离开页面时会尝试提交。未显示“已保存”的修改尚未确认写入服务器。</p>}
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

      <p role="status" aria-live="polite">{sliderMessage || message}</p>
      {sliderFailed && <Button onClick={() => void flushSlider()}>重试保存壁纸</Button>}
    </section>
  );
}
