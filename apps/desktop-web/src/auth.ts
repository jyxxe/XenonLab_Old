type Config = {issuer:string;client_id:string};
const encode=(bytes:Uint8Array)=>btoa(String.fromCharCode(...bytes)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
async function config():Promise<Config>{const r=await fetch('/api/v1/auth/config');if(!r.ok)throw Error('身份服务配置不可用');return r.json();}
async function discovery(c:Config){const r=await fetch(c.issuer+'/.well-known/openid-configuration');if(!r.ok)throw Error('身份服务不可用');const d=await r.json();if(d.issuer!==c.issuer)throw Error('身份服务 issuer 不一致');return d;}
export async function login(){
 const c=await config(),d=await discovery(c);
 const verifier=encode(crypto.getRandomValues(new Uint8Array(32))),state=encode(crypto.getRandomValues(new Uint8Array(24)));
 const challenge=encode(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier))));
 const redirect=location.origin+'/';sessionStorage.setItem('xeonolab-oauth',JSON.stringify({verifier,state,redirect,created:Date.now()}));
 const url=new URL(d.authorization_endpoint);url.search=new URLSearchParams({client_id:c.client_id,response_type:'code',scope:'openid profile offline_access',redirect_uri:redirect,state,code_challenge:challenge,code_challenge_method:'S256'}).toString();location.assign(url);
}
export async function callback():Promise<{token:string;expires:number}|null>{
 const parameters=new URLSearchParams(location.search);if(!parameters.has('code')&&!parameters.has('error'))return null;
 const saved=sessionStorage.getItem('xeonolab-oauth');sessionStorage.removeItem('xeonolab-oauth');
 history.replaceState(null,'',location.pathname);
 if(parameters.has('error'))throw Error('登录未完成');if(!saved)throw Error('登录请求已失效');
 const data=JSON.parse(saved);if(parameters.get('state')!==data.state||Date.now()-data.created>600000)throw Error('登录状态校验失败');
 const r=await fetch('/api/v1/auth/session/exchange',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:parameters.get('code'),redirect:data.redirect,verifier:data.verifier})});
 if(!r.ok)throw Error('登录令牌交换失败');return r.json();
}
export async function sessionToken():Promise<{token:string;expires:number}|null>{
 const r=await fetch('/api/v1/auth/session/token',{method:'POST'});
 if(r.status===401)return null;if(!r.ok)throw Error('登录续期服务暂不可用');return r.json();
}
export async function logout(){const r=await fetch('/api/v1/auth/session/logout',{method:'POST'});if(!r.ok)throw Error('退出失败，请重试');}
