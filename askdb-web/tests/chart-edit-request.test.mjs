import assert from 'node:assert/strict';
import test from 'node:test';
import { sanitizeChartEditRequest, readChartEditError, readChartEditIntent } from '../lib/chart-edit-request.mjs';

const request = () => ({thread_id:'t1', model_profile_id:'p1', instruction:'改成柱状图', source_result_id:'r1',
  view:{chart_type:'bar',dimension_field:'region',metric_fields:['revenue'],hidden_metric_fields:[],
    sort:{mode:'original'},format_by_field:{revenue:{mode:'raw',decimal_places:'auto'}}},
  columns:['region','revenue'],column_types:['string','double'],row_count:5,truncated:false});
const patch = () => ({chart_type:'bar',dimension_field:null,metric_fields:null,hidden_metric_fields:null,
  bar_orientation:null,title:null,field_labels:null,sort:null,format_by_field:null,
  show_data_labels:null,show_legend:null,color_by_metric:null});
const intent = () => ({status:'apply',patch:patch(),current_result_operation:null,
  category_color_operations:[],query_proposal:null,clarification:null});

test('request returns independent valid allowlisted copy', () => {
  const source=request(); const result=sanitizeChartEditRequest(source);
  assert.deepEqual(result, source); assert.notEqual(result.view,source.view);
});
test('chart edit errors preserve only the safe selected-profile error code and message', () => {
  assert.deepEqual(readChartEditError({ detail: { code: 'MODEL_PROFILE_NOT_FOUND', message: 'credential-secret' } }), {
    code: 'MODEL_PROFILE_NOT_FOUND',
    message: '找不到所选模型配置，请重新选择。',
  });
});
test('request rejects SQL rows categories tools history and source authorization fields', () => {
  for(const key of ['sql','rows','categories','tools','history','data_source_id'])
    assert.throws(()=>sanitizeChartEditRequest({...request(),[key]:'secret'}));
  for(const key of ['rows','pie_category_colors','echarts_options'])
    assert.throws(()=>sanitizeChartEditRequest({...request(),view:{...request().view,[key]:{secret:'red'}}}));
});
test('request bounds and scalar types are exact with no coercion', () => {
  for(const change of [{thread_id:''},{model_profile_id:9},{source_result_id:''},{instruction:' '},
    {instruction:'x'.repeat(2049)},{columns:Array(101).fill('x')},
    {columns:['x'.repeat(129),'revenue']},{column_types:['x'.repeat(65),'double']},
    {column_types:['double']},{row_count:true},{row_count:'5'},{row_count:1.5},{row_count:1001},
    {truncated:0},{view:{title:'x'.repeat(121)}},{view:{field_labels:{x:'x'.repeat(81)}}},
    {view:{metric_fields:['x','x']}},{view:{show_legend:1}},
    {view:{sort:{mode:'original',sql:'secret'}}}])
    assert.throws(()=>sanitizeChartEditRequest({...request(),...change}));
});
test('total UTF8 byte bound is enforced', () => {
  const columns=Array.from({length:100},(_,i)=>`${i}`+'名'.repeat(125));
  assert.throws(()=>sanitizeChartEditRequest({...request(), columns,column_types:Array(100).fill('double'),
    view:{field_labels:Object.fromEntries(columns.map(c=>[c,'标'.repeat(80)]))}}));
});
test('strict reader accepts apply query-required and clarification', () => {
  assert.deepEqual(readChartEditIntent(intent()),intent());
  const query={...intent(),status:'query_required',query_proposal:{operation:'aggregation'}};
  assert.deepEqual(readChartEditIntent(query),query);
  const clarify={...intent(),status:'clarify',patch:null,clarification:{code:'source_unit_required'}};
  assert.deepEqual(readChartEditIntent(clarify),clarify);
});
test('strict reader rejects malformed extra text and contradictory statuses', () => {
  for(const value of ['text',JSON.stringify(intent()),{status:'apply'},
    {...intent(),sql:'secret'},{...intent(),patch:{}},{...intent(),patch:null},
    {...intent(),status:'clarify'},{...intent(),query_proposal:{operation:'aggregation'}},
    {...intent(),status:'query_required',query_proposal:{operation:'execute_sql'}},
    {...intent(),patch:{...patch(),chart_type:'scatter'}},
    {...intent(),patch:{...patch(),title:'x'.repeat(121)}},
    {...intent(),category_color_operations:[{category_label:'x',color:'#fff'}]}])
    assert.throws(()=>readChartEditIntent(value));
});
test('strict reader validates typed Top N format and color bounds', () => {
  const valid={...intent(),current_result_operation:{kind:'top_n',field:'revenue',count:3,direction:'desc',scope:'current_result'},
    category_color_operations:[{category_label:'华东',color:'blue'}]};
  assert.deepEqual(readChartEditIntent(valid),valid);
  for(const op of [{...valid.current_result_operation,count:true},{...valid.current_result_operation,count:101},
    {...valid.current_result_operation,scope:'all_data'}])
    assert.throws(()=>readChartEditIntent({...valid,current_result_operation:op}));
  assert.throws(()=>readChartEditIntent({...valid,category_color_operations:Array(9).fill(valid.category_color_operations[0])}));
  assert.throws(()=>readChartEditIntent({...intent(),patch:{...patch(),format_by_field:{revenue:{mode:'percent',decimal_places:2,encoding:'ratio_0_1',suffix:null,unit_family:null,source_unit:null,display_unit:null,sql:'secret'}}}}));
});
