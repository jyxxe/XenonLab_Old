import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import {Icon} from '../src/design-system/Icon';
import {Button, IconButton} from '../src/design-system/primitives';

// Synthetic registry strings; no service, identity or external asset is required.
function Fixture() {
  const [count, setCount] = useState(0);
  return <main>
    <IconButton label="刷新示例" onClick={() => setCount(count + 1)}>⟳</IconButton>
    <Button icon="favorite">收藏示例</Button><output>{count}</output>
    <Icon value="library" label="知识库图标" />
    {['__proto__', 'constructor', '<custom>', '🦊', 'CUSTOM'].map(value =>
      <div key={value} data-custom={value}><Icon value={value} />文字标签</div>)}
  </main>;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
