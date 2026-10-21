import React from 'react';
import {parseExactJSON, stringifyExactJSON} from './library/json.js';
export type Column={name:string;type:string;nullable:boolean;default_value:any};
export const initialColumns:Column[]=[{name:'title',type:'string',nullable:false,default_value:null},{name:'body',type:'text',nullable:true,default_value:null}];
const types=['string','text','int64','float64','boolean','datetime','date','enum','json','reference','attachment_list','theorem_ref'];
export function SchemaEditor({columns,change,disabled=false}:{columns:Column[];change:(columns:Column[])=>void;disabled?:boolean}){
 function update(index:number,patch:Partial<Column>){change(columns.map((column,i)=>i===index?{...column,...patch}:column))}
 return <fieldset disabled={disabled} className="schema-editor"><legend>字段定义</legend>{columns.map((column,index)=><div className="schema-field" key={index}>
 <label>字段名<input value={column.name} onChange={e=>update(index,{name:e.target.value})}/></label>
 <label>类型<select value={column.type} onChange={e=>update(index,{type:e.target.value,default_value:null})}>{types.map(type=><option key={type}>{type}</option>)}</select></label>
 <label><input type="checkbox" checked={column.nullable} onChange={e=>update(index,{nullable:e.target.checked})}/>允许为空</label>
 <label>默认值 {['json','attachment_list'].includes(column.type)?'（JSON）':''}<input value={column.default_value===null?'':typeof column.default_value==='object'?stringifyExactJSON(column.default_value):String(column.default_value)} onChange={e=>{const text=e.target.value;let value:any=text===''?null:text;if(column.type==='float64'&&text!=='')value=Number(text);if(column.type==='boolean'&&text!=='')value=text==='true';if(['json','attachment_list'].includes(column.type)&&text!==''){try{value=parseExactJSON(text)}catch{value=text}}update(index,{default_value:value})}}/></label>
 <button type="button" disabled={columns.length===1} onClick={()=>change(columns.filter((_,i)=>i!==index))}>移除字段</button>
 </div>)}<button type="button" disabled={columns.length>=128} onClick={()=>change([...columns,{name:'field_'+(columns.length+1),type:'text',nullable:true,default_value:null}])}>添加字段</button></fieldset>
}
