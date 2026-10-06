export const CHART_EDIT_BODY_LIMIT = 64 * 1024;
export const CHART_EDIT_REQUEST_KEYS = Object.freeze(['thread_id','model_profile_id','instruction',
  'source_result_id','view','columns','column_types','row_count','truncated']);
const PATCH_KEYS = Object.freeze(['chart_type','dimension_field','metric_fields','hidden_metric_fields',
  'bar_orientation','title','field_labels','sort','format_by_field','show_data_labels','show_legend','color_by_metric']);
const VIEW_KEYS = Object.freeze([...PATCH_KEYS,'color_palette_id','current_result_top_n']);
const INTENT_KEYS = ['status','patch','current_result_operation','category_color_operations','query_proposal','clarification'];
const PALETTE = ['blue','teal','green','amber','orange','red','purple','slate'];
const COLOR_PALETTES = ['system_default','classic','ocean','warm','earth','pastel','high_contrast','color_vision_friendly'];
const COLOR_DIRECTIONS = ['horizontal','vertical','diagonal_down','diagonal_up'];
const UNITS = ['yuan','thousand_yuan','ten_thousand_yuan','hundred_million_yuan'];
const QUERIES = ['database_filter','full_data_top_n','aggregation','period_comparison'];
const CLARIFICATIONS = ['top_n_scope_required','top_n_metric_required','source_unit_required','field_not_in_result',
  'category_not_in_result','category_ambiguous','conflicting_category_color','chart_type_incompatible',
  'conflicting_sort','operation_unsupported'];
const DIAGNOSTIC_STAGES = ['request_validation','structured_output_parse','structured_output_call',
  'intent_validation','bff_response_validation'];
const DIAGNOSTIC_REASON_CODES = ['validation_failed','sort_original_has_values',
  'sort_missing_field_or_direction','format_mode_fields_mismatch','format_suffix_blank','patch_empty',
  'duplicate_metric_fields','duplicate_hidden_metric_fields','apply_missing_operation',
  'apply_conflicting_payload','query_required_missing_proposal','clarify_missing_code',
  'clarify_conflicting_operations'];
const invalid = () => { throw new Error('CHART_EDIT_INPUT_INVALID'); };
const check = (valid) => { if (!valid) invalid(); };
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value) &&
  [Object.prototype,null].includes(Object.getPrototypeOf(value));
function keys(value, allowed, required=[]) {
  check(object(value));
  check(Object.keys(value).every(key=>allowed.includes(key)));
  check(required.every(key=>Object.hasOwn(value,key)));
}
function text(value,max,min=1) { check(typeof value==='string' && value.length>=min && value.length<=max); }
function enumValue(value,allowed) { check(allowed.includes(value)); }
function integer(value,min,max) { check(Number.isSafeInteger(value) && value>=min && value<=max); }
function names(value,max,min=0) {
  check(Array.isArray(value) && value.length>=min && value.length<=max);
  value.forEach(name=>text(name,128)); check(new Set(value).size===value.length);
}
function map(value,max,validate) {
  check(object(value) && Object.keys(value).length<=max);
  for(const [name,item] of Object.entries(value)) { text(name,128); validate(item); }
}
function displayColor(value) {
  if(typeof value==='string') { enumValue(value,PALETTE); return; }
  check(object(value));
  integer(value.opacity,0,100);
  if(value.mode==='solid') {
    keys(value,['mode','hex','opacity'],['mode','hex','opacity']);
    check(typeof value.hex==='string' && /^#[0-9a-fA-F]{6}$/.test(value.hex));
  } else if(value.mode==='linear_gradient') {
    keys(value,['mode','start_hex','end_hex','direction','opacity'],['mode','start_hex','end_hex','direction','opacity']);
    check(typeof value.start_hex==='string' && /^#[0-9a-fA-F]{6}$/.test(value.start_hex));
    check(typeof value.end_hex==='string' && /^#[0-9a-fA-F]{6}$/.test(value.end_hex));
    enumValue(value.direction,COLOR_DIRECTIONS);
  } else invalid();
}
function sort(value,response) {
  keys(value,['mode','field','direction'],response?['mode','field','direction']:['mode']);
  enumValue(value.mode,['original','dimension','metric']);
  if(value.mode==='original') {
    check(value.field===undefined || value.field===null);
    check(value.direction===undefined || value.direction===null);
  } else { text(value.field,128); enumValue(value.direction,['asc','desc']); }
}
function format(value,response) {
  const allowed=['mode','decimal_places','suffix','unit_family','source_unit','display_unit','encoding'];
  keys(value,allowed,response?allowed:['mode','decimal_places']);
  enumValue(value.mode,['raw','suffix','unit_scale','percent']);
  if(value.decimal_places!=='auto') integer(value.decimal_places,0,6);
  const fields={raw:[],suffix:['suffix'],unit_scale:['unit_family','source_unit','display_unit'],percent:['encoding']}[value.mode];
  for(const name of allowed.slice(2)) {
    if(fields.includes(name)) check(value[name]!==null && value[name]!==undefined);
    else check(value[name]===null || value[name]===undefined);
  }
  if(value.mode==='suffix') { text(value.suffix,24); check(value.suffix.trim().length>0); }
  if(value.mode==='unit_scale') { enumValue(value.unit_family,['CNY']); enumValue(value.source_unit,UNITS); enumValue(value.display_unit,UNITS); }
  if(value.mode==='percent') enumValue(value.encoding,['ratio_0_1','percent_0_100']);
}
function topN(value,response) {
  const allowed=response?['kind','field','count','direction','scope']:['field','count','direction'];
  keys(value,allowed,allowed); text(value.field,128); integer(value.count,1,100); enumValue(value.direction,['asc','desc']);
  if(response) { enumValue(value.kind,['top_n']); enumValue(value.scope,['current_result']); }
}
function display(value,response) {
  keys(value,response?PATCH_KEYS:VIEW_KEYS,response?PATCH_KEYS:[]);
  for(const [key,item] of Object.entries(value)) {
    if(response && item===null) continue;
    if(key==='current_result_top_n') { if(item!==null) topN(item,false); continue; }
    check(item!==null && item!==undefined);
    switch(key) {
      case 'chart_type': enumValue(item,['line','bar','pie']); break;
      case 'dimension_field': text(item,128); break;
      case 'metric_fields': names(item,4,1); break;
      case 'hidden_metric_fields': names(item,4); break;
      case 'bar_orientation': enumValue(item,['vertical','horizontal']); break;
      case 'title': text(item,120); break;
      case 'field_labels': map(item,100,label=>text(label,80,0)); break;
      case 'sort': sort(item,response); break;
      case 'format_by_field': map(item,4,item=>format(item,response)); break;
      case 'show_data_labels': case 'show_legend': check(typeof item==='boolean'); break;
      case 'color_palette_id': check(!response); enumValue(item,COLOR_PALETTES); break;
      case 'color_by_metric': map(item,4,item=>response?enumValue(item,PALETTE):displayColor(item)); break;
      default: invalid();
    }
  }
  if(!response && value.color_by_metric!==undefined) {
    check(value.chart_type==='line' || value.chart_type==='bar');
    check(Array.isArray(value.metric_fields));
    const visible=new Set(value.metric_fields.filter(field=>
      !Array.isArray(value.hidden_metric_fields) || !value.hidden_metric_fields.includes(field)));
    check(Object.keys(value.color_by_metric).every(field=>visible.has(field)));
  }
  if(response) check(Object.values(value).some(item=>item!==null));
}
function boundedClone(value) {
  const json=JSON.stringify(value);
  check(new TextEncoder().encode(json).byteLength<=CHART_EDIT_BODY_LIMIT);
  return JSON.parse(json);
}
export function sanitizeChartEditRequest(value) {
  keys(value,CHART_EDIT_REQUEST_KEYS,CHART_EDIT_REQUEST_KEYS.filter(key=>key!=='model_profile_id'));
  text(value.thread_id,128); text(value.source_result_id,256); text(value.instruction,2048);
  check(value.instruction.trim().length>0);
  if(value.model_profile_id!==undefined && value.model_profile_id!==null) text(value.model_profile_id,128);
  display(value.view,false);
  check(Array.isArray(value.columns) && value.columns.length>=1 && value.columns.length<=100);
  value.columns.forEach(column=>text(column,128));
  check(Array.isArray(value.column_types) && value.column_types.length===value.columns.length);
  value.column_types.forEach(type=>text(type,64));
  integer(value.row_count,0,1000); check(typeof value.truncated==='boolean');
  return boundedClone(value);
}
export function readChartEditIntent(value) {
  try {
    keys(value,INTENT_KEYS,INTENT_KEYS); enumValue(value.status,['apply','query_required','clarify']);
    if(value.patch!==null) display(value.patch,true);
    if(value.current_result_operation!==null) topN(value.current_result_operation,true);
    check(Array.isArray(value.category_color_operations) && value.category_color_operations.length<=8);
    for(const operation of value.category_color_operations) {
      keys(operation,['category_label','color'],['category_label','color']);
      text(operation.category_label,128); enumValue(operation.color,PALETTE);
    }
    if(value.query_proposal!==null) {
      keys(value.query_proposal,['operation'],['operation']); enumValue(value.query_proposal.operation,QUERIES);
    }
    if(value.clarification!==null) {
      keys(value.clarification,['code'],['code']); enumValue(value.clarification.code,CLARIFICATIONS);
    }
    if(value.status==='apply') {
      check(value.patch!==null || value.current_result_operation!==null || value.category_color_operations.length>0);
      check(value.query_proposal===null && value.clarification===null);
    } else if(value.status==='query_required') check(value.query_proposal!==null && value.clarification===null);
    else check(value.clarification!==null && value.patch===null && value.current_result_operation===null &&
      value.category_color_operations.length===0 && value.query_proposal===null);
    return boundedClone(value);
  } catch { throw new Error('CHART_EDIT_OUTPUT_INVALID'); }
}
function readChartEditDiagnostic(value) {
  try {
    keys(value,['diagnostic_id','stage','exception_type','cause_types','provider_status_code','provider_output_chars','validation_issues'],
      ['diagnostic_id','stage','exception_type','cause_types','provider_status_code','provider_output_chars','validation_issues']);
    check(typeof value.diagnostic_id==='string' && /^[a-f0-9]{32}$/.test(value.diagnostic_id));
    enumValue(value.stage,DIAGNOSTIC_STAGES);
    check(typeof value.exception_type==='string' && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(value.exception_type));
    check(Array.isArray(value.cause_types) && value.cause_types.length<=5 &&
      value.cause_types.every(name=>typeof name==='string' && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(name)));
    check(value.provider_status_code===null ||
      (Number.isInteger(value.provider_status_code) && value.provider_status_code>=100 && value.provider_status_code<=599));
    check(value.provider_output_chars===null ||
      (Number.isInteger(value.provider_output_chars) && value.provider_output_chars>=0 && value.provider_output_chars<=1_000_000));
    check(Array.isArray(value.validation_issues) && value.validation_issues.length<=12);
    const validation_issues = value.validation_issues.map(issue=>{
      keys(issue,['path','type','reason_code'],['path','type']);
      check(typeof issue.path==='string' && issue.path.length<=128 && /^[A-Za-z0-9_.*\[\]-]+$/.test(issue.path));
      check(typeof issue.type==='string' && issue.type.length<=64 && /^[a-z0-9_.-]+$/.test(issue.type));
      if(issue.reason_code!==undefined) enumValue(issue.reason_code,DIAGNOSTIC_REASON_CODES);
      return {path:issue.path,type:issue.type,
        ...(issue.reason_code!==undefined?{reason_code:issue.reason_code}:{})};
    });
    return {
      diagnostic_id:value.diagnostic_id,
      stage:value.stage,
      exception_type:value.exception_type,
      cause_types:[...value.cause_types],
      provider_status_code:value.provider_status_code,
      provider_output_chars:value.provider_output_chars,
      validation_issues,
    };
  } catch { return undefined; }
}
const SAFE_MESSAGES = Object.freeze({
  AUTH_REQUIRED:'登录状态已失效，请重新登录。', INVALID_SESSION:'登录状态已失效，请重新登录。',
  PASSWORD_CHANGE_REQUIRED:'请先修改临时密码。', CSRF_CHECK_FAILED:'请求来源校验失败，请刷新后重试。',
  AUTH_STORE_UNAVAILABLE:'账号服务当前不可用。', THREAD_NOT_FOUND:'找不到此会话或当前账号无权查看。',
  THREAD_MEMORY_DISABLED:'会话记忆服务当前不可用。', DATA_SOURCE_UNAVAILABLE:'所选数据源当前不可用。',
  MODEL_CONFIGURATION_INVALID:'所选模型配置当前不可用。', MODEL_PROFILE_NOT_FOUND:'找不到所选模型配置，请重新选择。',
  CHART_EDIT_INPUT_INVALID:'图表编辑请求字段无效。',
  CHART_EDIT_REQUEST_TOO_LARGE:'图表编辑请求不能超过 64 KiB。',
  CHART_EDIT_STRUCTURED_OUTPUT_UNSUPPORTED:'所选模型不支持图表编辑结构化输出。',
  CHART_EDIT_MODEL_FAILED:'无法解释此次图表编辑，请稍后重试。',
  CHART_EDIT_OUTPUT_INVALID:'图表编辑响应无效，请重试。', CHART_EDIT_TIMEOUT:'图表编辑请求超时，请重试。',
  CHART_EDIT_RUNTIME_UNAVAILABLE:'图表编辑服务当前不可用。', AGENT_UNAVAILABLE:'AskDB Agent 暂时不可用。',
  AGENT_RESPONSE_INVALID:'AskDB Agent 返回了无效响应。',
});
export function readChartEditError(value) {
  const candidate=object(value?.detail)?value.detail:value;
  const code=object(candidate) && Object.hasOwn(SAFE_MESSAGES,candidate.code)?candidate.code:'CHART_EDIT_MODEL_FAILED';
  const diagnostic=object(candidate)?readChartEditDiagnostic(candidate.diagnostic):undefined;
  return {code,message:SAFE_MESSAGES[code],...(diagnostic?{diagnostic}:{})};
}
