import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import test from 'node:test';

const hooks=registerHooks({resolve(specifier,context,nextResolve){
  if(specifier==='next/headers')return {url:'data:text/javascript,export async function cookies(){return globalThis.__chartTestCookies;}',shortCircuit:true};
  if(specifier.startsWith('@/lib/'))return {url:new URL('../lib/'+specifier.slice(6)+(specifier.endsWith('.mjs')?'':'.ts'),import.meta.url).href,shortCircuit:true};
  return nextResolve(specifier,context);
}});
const {POST}=await import('../app/api/chart-edits/interpret/route.ts');
hooks.deregister();
const payload=()=>({thread_id:'t1',instruction:'改成柱状图',source_result_id:'r1',
  view:{chart_type:'bar'},columns:['revenue'],column_types:['double'],row_count:5,truncated:false});
const intent=()=>({status:'clarify',patch:null,current_result_operation:null,category_color_operations:[],query_proposal:null,clarification:{code:'operation_unsupported'}});
function req(body=payload(),headers={}){return new Request('http://localhost:3000/api/chart-edits/interpret',{
  method:'POST',headers:{origin:'http://localhost:3000',cookie:'__Host-askdb_csrf=csrf',
  'x-csrf-token':'csrf','content-type':'application/json',...headers},body:typeof body==='string'?body:JSON.stringify(body)});}
async function run(fn){const old=globalThis.fetch;let calls=[];
  globalThis.__chartTestCookies={get:()=>({value:'session-secret'}),set:()=>{}};
  globalThis.fetch=async(url,options)=>{calls.push({url,options});return Response.json(intent());};
  try{await fn(calls);}finally{globalThis.fetch=old;delete globalThis.__chartTestCookies;}}

test('BFF enforces CSRF and authentication before forwarding',async()=>run(async calls=>{
  let response=await POST(req(payload(),{'x-csrf-token':'wrong'}));assert.equal(response.status,403);
  globalThis.__chartTestCookies={get:()=>undefined,set:()=>{}};
  response=await POST(req());assert.equal(response.status,401);assert.equal(calls.length,0);
  assert.match(response.headers.get('cache-control'),/no-store/);
}));
test('BFF forwards exact path session header sanitized JSON and no-store',async()=>run(async calls=>{
  const response=await POST(req());assert.equal(response.status,200);assert.deepEqual(await response.json(),intent());
  assert.equal(calls.length,1);assert.equal(calls[0].url,'http://127.0.0.1:8000/v1/chart-edits/interpret');
  assert.equal(calls[0].options.headers.authorization,'Bearer session-secret');
  assert.deepEqual(JSON.parse(calls[0].options.body),payload());assert.equal(calls[0].options.cache,'no-store');
  assert.match(response.headers.get('cache-control'),/no-store/);
}));
test('BFF rejects size scalar and forbidden fields without fetching',async()=>run(async calls=>{
  for(const body of [' '.repeat(65537),{...payload(),sql:'secret'}, {...payload(),row_count:'5'},
    {...payload(),view:{pie_category_colors:{secret:'blue'}}}]){
    const response=await POST(req(body));assert.ok([400,413].includes(response.status));
    assert.match(response.headers.get('cache-control'),/no-store/);
  }assert.equal(calls.length,0);
}));
test('BFF sanitizes upstream codes and messages including unknown errors',async()=>run(async()=>{
  for(const code of ['CHART_EDIT_STRUCTURED_OUTPUT_UNSUPPORTED','MODEL_PROFILE_NOT_FOUND','SECRET_PROVIDER_FAILURE']){
    globalThis.fetch=async()=>Response.json({detail:{code,message:'credential-secret',trace:'secret'}},{status:502});
    const response=await POST(req());const body=await response.json();assert.equal(response.status,502);
    assert.equal(body.code,code==='SECRET_PROVIDER_FAILURE'?'CHART_EDIT_MODEL_FAILED':code);
    assert.ok(!JSON.stringify(body).includes('secret'));assert.match(response.headers.get('cache-control'),/no-store/);
  }
}));
test('BFF rejects invalid success response and hides unexpected upstream fields',async()=>run(async()=>{
  globalThis.fetch=async()=>Response.json({...intent(),sql:'credential-secret'});
  const response=await POST(req());assert.equal(response.status,502);
  assert.equal((await response.json()).code,'CHART_EDIT_OUTPUT_INVALID');
}));
