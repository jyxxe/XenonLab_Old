import React from 'react';
export type Segment = {label: string; value: number; color?: string; hint?: string};
const palette = ['#4e8cde', '#ff9f0a', '#30d158', '#ff453a', '#5e5ce6', '#64d2ff'];
export const chartColor = (index: number) => palette[index % palette.length];
const total = (segments: Segment[]) => segments.reduce((sum, segment) => sum + segment.value, 0);

/** macOS activity-ring style donut with rounded, capped segments. */
export function Ring({segments, size = 132, thickness = 13, center}: {segments: Segment[]; size?: number; thickness?: number; center?: {value: React.ReactNode; label?: string}}) {
  const sum = total(segments);
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  return <svg className="mac-ring" width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={segments.map(segment => `${segment.label} ${segment.value}`).join('，')}>
    <circle className="ring-track" cx={size / 2} cy={size / 2} r={radius} strokeWidth={thickness} fill="none"/>
    {segments.filter(segment => segment.value > 0).map((segment, index) => {
      const length = circumference * (segment.value / sum);
      const gap = Math.min(4, length / 3);
      const element = <circle key={segment.label} className="ring-segment" cx={size / 2} cy={size / 2} r={radius} strokeWidth={thickness} fill="none" stroke={segment.color || chartColor(index)} strokeLinecap="round" strokeDasharray={`${Math.max(length - gap, 1)} ${circumference - Math.max(length - gap, 1)}`} strokeDashoffset={-offset} transform={`rotate(-90 ${size / 2} ${size / 2})`}><title>{segment.hint || `${segment.label} ${segment.value}`}</title></circle>;
      offset += length;
      return element;
    })}
    {center && <text className="ring-value" x="50%" y="46%" textAnchor="middle" dominantBaseline="central">{center.value}</text>}
    {center?.label && <text className="ring-label" x="50%" y="62%" textAnchor="middle" dominantBaseline="central">{center.label}</text>}
  </svg>;
}

/** Rounded segmented meter (macOS storage bar); the pill mask rounds the ends. */
export function Meter({segments, height = 12}: {segments: Segment[]; height?: number}) {
  const sum = total(segments) || 1;
  return <div className="mac-meter" style={{height}} role="img" aria-label={segments.map(segment => `${segment.label} ${segment.value}`).join('，')}>
    {segments.filter(segment => segment.value > 0).map((segment, index) => <i key={segment.label} style={{width: `${(segment.value / sum) * 100}%`, background: segment.color || chartColor(index)}} title={segment.hint || `${segment.label} ${segment.value}`}/>)}
    {total(segments) === 0 && <i className="mac-meter-empty"/>}
  </div>;
}

/** Vertical bars inside a rounded chart mask; values are real counts, never scaled guesses. */
export function Bars({data, height = 96, unit = ''}: {data: {label: string; value: number; hint?: string; color?: string}[]; height?: number; unit?: string}) {
  const maximum = Math.max(1, ...data.map(item => item.value));
  return <div className="mac-bars" style={{height}} role="group" aria-label={data.map(item => item.hint || `${item.label} ${item.value}${unit}`).join('，')}>
    {data.map((item, index) => <div className="mac-bar" key={item.label} role="img" aria-label={item.hint || `${item.label} ${item.value}${unit}`} title={item.hint || `${item.label} ${item.value}${unit}`}>
      <div className="mac-plot"><i style={{height: `${Math.max(4, (item.value / maximum) * 100)}%`, background: item.color || chartColor(index)}}/></div>
      <span>{item.label}</span>
    </div>)}
  </div>;
}

export function Legend({items}: {items: Segment[]}) {
  return <ul className="mac-legend">{items.map((item, index) => <li key={item.label}>
    <b style={{background: item.color || chartColor(index)}}/>
    <span>{item.label}</span>
    <em>{item.value}</em>
  </li>)}</ul>;
}
