import React from 'react';

// Original interface vectors share optical margins and rounded 1.75px strokes.
// Registry icon strings stay data; unknown values are rendered as escaped text.
export const paths:Record<string,React.ReactNode>={
 library:<><path d="M12 5.5C9 3.5 5.5 3.5 2.5 5v14c3-1.5 6.5-1.5 9.5.5 3-2 6.5-2 9.5-.5V5c-3-1.5-6.5-1.5-9.5.5Z"/><path d="M12 5.5v14M6 8h2M6 11h2M16 8h2M16 11h2"/></>,
 chat:<><path d="M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 3v-3a2 2 0 0 1-2-2V7a3 3 0 0 1 3-3Z"/><path d="M7 9h10M7 13h7"/></>,
 code:<><path d="m7 6-5 6 5 6m10-12 5 6-5 6M14 4l-4 16"/></>,
 issues:<><rect x="4" y="3" width="16" height="18" rx="3"/><path d="m7 8 1 1 2-2m-3 6 1 1 2-2M13 8h4M13 13h4M7 18h10"/></>,
 monitor:<><rect x="2.5" y="3.5" width="19" height="14" rx="2.5"/><path d="M9 21h6M12 17.5V21M5.5 11H8l2-4 4 7 2-3h2.5"/></>,
 embedding:<><path d="m6 6 12 6M6 6v12m0 0 12-6"/><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="12" r="3"/></>,
 settings:<><path d="m9 3-.7 2.4-2 .9L4 5.8 2.5 8.5l1.7 1.8-.2 2.4-2 1.5 1.5 2.8 2.5-.5 1.9 1.3.4 2.7 3 .5 1.2-2.4 2.4-.4 1.9 1.8 2.5-1.8-.8-2.5 1.1-2.1 2.5-.7-.2-3-2.5-.8-1.2-2 .6-2.5L15.7 3l-2 1.6-2.3-.1L10.8 3Z"/><circle cx="12" cy="12" r="3.2"/></>,
 launchpad:<>{[5,12,19].flatMap(x=>[5,12,19].map(y=><circle key={`${x}-${y}`} cx={x} cy={y} r="2" fill="currentColor" stroke="none"/>))}</>,
 search:<><circle cx="10.5" cy="10.5" r="7"/><path d="m16 16 5 5"/></>,
 check:<path d="m4 12 5 5L20 6"/>,
 error:<><circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 17h.01"/></>,
 warning:<><path d="M10.3 3.8a2 2 0 0 1 3.4 0l8 14A2 2 0 0 1 20 21H4a2 2 0 0 1-1.7-3.2Z"/><path d="M12 8v5M12 17h.01"/></>,
 close:<path d="m6 6 12 12M18 6 6 18"/>,
 sort:<><path d="M8 20V4m-4 4 4-4 4 4M16 4v16m-4-4 4 4 4-4"/></>,
 'arrow-up':<path d="M12 21V3M5 10l7-7 7 7"/>,
 'arrow-down':<path d="M12 3v18M5 14l7 7 7-7"/>,
 'arrow-right':<path d="M3 12h18M14 5l7 7-7 7"/>,
 'chevron-left':<path d="m15 5-7 7 7 7"/>,
 'chevron-right':<path d="m9 5 7 7-7 7"/>,
 'chevron-down':<path d="m5 9 7 7 7-7"/>,
 'chevron-up':<path d="m5 15 7-7 7 7"/>,
 account:<><circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/></>,
 delete:<><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></>,
 download:<><path d="M12 3v12m-5-5 5 5 5-5M3 16v5h18v-5"/></>,
 upload:<><path d="M12 15V3m-5 5 5-5 5 5M3 16v5h18v-5"/></>,
 refresh:<><path d="M20 9a8 8 0 1 0 .2 6M20 3v6h-6"/></>,
 plus:<path d="M12 3v18M3 12h18"/>,
 favorite:<path d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21 7 14.2 2 9.3l6.9-1Z"/>,
 attachment:<path d="m9 8 7-5a4 4 0 0 1 5 6L10 20a5 5 0 0 1-7-7L14 2m-6 13 8-8"/>,
 query:<><path d="M9 3H7a2 2 0 0 0-2 2v4a3 3 0 0 1-2 3 3 3 0 0 1 2 3v4a2 2 0 0 0 2 2h2m6-18h2a2 2 0 0 1 2 2v4a3 3 0 0 0 2 3 3 3 0 0 0-2 3v4a2 2 0 0 1-2 2h-2"/></>,
 theorem:<path d="M6 4v16M6 12h14"/>,
 home:<><path d="m2 11 10-8 10 8M5 9v12h14V9M10 21v-7h4v7"/></>,
 rows:<path d="M3 6h18M3 12h18M3 18h18"/>,
 window:<><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/></>,
 maximize:<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>,
 minimize:<path d="M4 12h16"/>,
 power:<><path d="M12 2v10M7 4.5a9 9 0 1 0 10 0"/></>,
 theme:<><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" stroke="none"/></>,
 sparkle:<path d="m12 2 2.8 7.2L22 12l-7.2 2.8L12 22l-2.8-7.2L2 12l7.2-2.8Z"/>,
 external:<><path d="M14 3h7v7m0-7L11 13M10 3H3v18h18v-7"/></>,
 calendar:<><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 2v6M17 2v6M3 11h18M7 15h3M14 15h3"/></>,
 generic:<path d="m12 3 9 9-9 9-9-9Z"/>,
};
export const aliases:Record<string,string>={
 '▤':'library','✦':'chat','⌘':'code','⚑':'issues','◎':'monitor','⟡':'embedding','⚙':'settings','▦':'launchpad','⌕':'search','✓':'check','⚠':'warning','✕':'close','—':'minimize','↑':'arrow-up','↓':'arrow-down','→':'arrow-right','⇩':'download','⇧':'upload','◐':'theme','⏻':'power','▭':'window','◱':'maximize','▸':'chevron-right','▾':'chevron-down','‹':'chevron-left','›':'chevron-right','★':'favorite','🗎':'attachment','{ }':'query','⊢':'theorem','⌂':'home','≡':'rows','＋':'plus','⟳':'refresh','◇':'generic',
 'dashboard':'monitor','system':'monitor','knowledge':'library','openwebui':'chat','development':'code','dev':'code',
};
export function Icon({value, size = 20, label, className = ''}: {
  value: React.ReactNode; size?: number; label?: string; className?: string;
}) {
  if (typeof value !== 'string') return <>{value}</>;
  const name = Object.hasOwn(aliases, value) ? aliases[value] : value;
  const shape = Object.hasOwn(paths, name) ? paths[name] : null;
  const accessibility = label ? {role: 'img', 'aria-label': label} : {'aria-hidden': true as const};
  if (!shape) return <span className={`ui-icon ui-icon-custom ${className}`} style={{minWidth: size, height: size}} {...accessibility}>{value}</span>;
  return <svg className={`ui-icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" {...accessibility} focusable="false" data-icon={name}>{shape}</svg>;
}
