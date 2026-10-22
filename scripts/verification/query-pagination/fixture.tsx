import React from 'react';
import {createRoot} from 'react-dom/client';
import './src/design-system/tokens.css';
import './src/design-system/primitives.css';
import './src/shell/shell.css';
import './src/style.css';
import {QueryConsole} from './src/library/QueryConsole';
import {useQueryConsole} from './src/library/hooks';

const apiPage=async(path:string,init:RequestInit={})=>{
  const headers=new Headers(init.headers);headers.set('Authorization','Bearer round4-local-test-token');
  if(!headers.has('Content-Type'))headers.set('Content-Type','application/json');
  const response=await fetch(`/api/v1${path}`,{...init,headers});
  const body=await response.json();if(!response.ok)throw new Error(body?.error?.message||`HTTP ${response.status}`);
  return {data:body.data,meta:body.meta||{}};
};
function Fixture(){
 const query=useQueryConsole(apiPage,'round4-synthetic-user',true,0);
 return <QueryConsole expression={query.expression} onExpression={query.setExpression} rows={query.rows} meta={query.meta} plan={query.plan} error={query.error} running={query.running} validating={query.validating} duration={query.duration} recent={query.recent} draftStatus={query.draftStatus} draftMessage={query.draftMessage} queryEnabled={true} onRun={(text,remember)=>void query.run(text,remember)} onValidate={query.validate} onOpenRow={()=>{}} onLoadRecent={query.loadRecent} onDeleteRecent={query.deleteRecent} onClearRecent={query.clearRecent} onRetryStorage={query.retryStorage} tableName={()=>'Round 4 synthetic table'}/>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
