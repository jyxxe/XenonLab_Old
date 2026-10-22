import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import {CalendarWidget} from '../src/Widgets';

// This component needs no API, identity or event data. Only the configured
// timezone prop and browser clock are controlled by this local fixture.
function Fixture() {
  const [timezone, setTimezone] = useState(new URLSearchParams(location.search).get('timezone') || 'system');
  return <main style={{width:224, margin:24}}>
    <CalendarWidget timezone={timezone}/>
    <label style={{display:'block', marginTop:16}}>合成配置时区
      <select aria-label="合成配置时区" value={timezone} onChange={event=>setTimezone(event.target.value)}>
        {['system','UTC','Pacific/Kiritimati','America/Los_Angeles','not/a-timezone'].map(zone=><option key={zone}>{zone}</option>)}
      </select>
    </label>
  </main>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
