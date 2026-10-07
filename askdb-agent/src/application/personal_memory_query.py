"""Narrow SQLGlot checks for validated personal filters/metric references."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import sqlglot
from sqlglot import exp

from domain.personal_memory import PersonalMemoryError
from domain.turn_interpretation import BoundQuerySemantics, RuntimeRef


def enforce_bound_query(sql: str, dialect: str, semantics: BoundQuerySemantics,
                        runtime_ref: RuntimeRef | None) -> None:
    if any(x.required for x in semantics.issues):
        raise PersonalMemoryError('PERSONAL_MEMORY_BINDING_UNCERTAIN', '个人条件尚未确认，不能执行查询。')
    if (semantics.filters or semantics.metrics) and (runtime_ref is None or semantics.runtime != runtime_ref):
        raise PersonalMemoryError('PERSONAL_MEMORY_VERSION_CHANGED', '个人条件与本轮数据源版本不一致，请重试。')
    enforce_personal_query(sql, dialect, semantics.constraints())


def time_conditions(rule):
    if not isinstance(rule,dict) or not rule.get('column'):
        raise ValueError('time rule needs a field')
    now=datetime.now(ZoneInfo('Asia/Shanghai'))
    today=now.date()
    relative=rule.get('relative')
    if relative=='last_30_days':
        start,end=today-timedelta(days=29),today+timedelta(days=1)
    elif relative=='this_month':
        start=today.replace(day=1)
        end=start.replace(year=start.year+1,month=1) if start.month==12 else start.replace(month=start.month+1)
    elif relative=='last_month':
        end=today.replace(day=1);start=(end-timedelta(days=1)).replace(day=1)
    elif not relative:
        start,end=rule.get('start'),rule.get('end')
        if not start or not end:
            raise ValueError('absolute range needs boundaries')
    else:
        raise ValueError('unknown relative time rule')
    return [{'column':rule['column'],'op':'gte','value':str(start)},{'column':rule['column'],'op':'lt','value':str(end)}]


def enforce_personal_query(sql, dialect, constraints):
    if not constraints or not (constraints.get('filters') or constraints.get('metrics')):
        return
    tree=sqlglot.parse_one(sql,read=dialect)
    # Conservative supported subset: one SELECT, no disjunction or nested scopes.
    if len(list(tree.find_all(exp.Select)))!=1 or tree.find(exp.Or) or tree.find(exp.Union) or tree.find(exp.Not):
        raise PersonalMemoryError('PERSONAL_MEMORY_BINDING_UNCERTAIN','个人范围或口径无法在本轮查询中确认，请澄清查询条件。')
    tables={t.alias_or_name.casefold():t.name.casefold() for t in tree.find_all(exp.Table)}
    def matches(column, reference):
        if not isinstance(column, exp.Column):
            return False
        model,field=reference.casefold().rsplit('.',1)
        actual=tables.get(column.table.casefold(), column.table.casefold()) if column.table else next(iter(tables.values())) if len(tables)==1 else ''
        return column.name.casefold()==field and actual==model
    where=tree.args.get('where')
    predicates=list(where.find_all(exp.Predicate)) if where else []
    for condition in constraints.get('filters',[]):
        found=False
        expected=condition['value']
        for predicate in predicates:
            if not matches(predicate.this,condition['column']):
                continue
            if condition['op']=='in' and isinstance(predicate,exp.In):
                found=all(isinstance(x,(exp.Literal,exp.Boolean)) for x in predicate.expressions) and isinstance(expected,list) and sorted(str(x.this) for x in predicate.expressions)==sorted(str(x) for x in expected)
            elif condition['op'] in {'eq','gte','lt'} and isinstance(predicate,{'eq':exp.EQ,'gte':exp.GTE,'lt':exp.LT}[condition['op']]):
                value=predicate.expression
                found=isinstance(value,(exp.Literal,exp.Boolean)) and str(value.this)==str(expected)
            if found:
                break
        if not found:
            raise PersonalMemoryError('PERSONAL_MEMORY_FILTER_MISSING','查询未能确认采用个人默认范围，请明确范围后重试。')
    for metric in constraints.get('metrics',[]):
        aggregate={'sum':exp.Sum,'avg':exp.Avg,'count':exp.Count,'min':exp.Min,'max':exp.Max}[metric['aggregation']]
        outputs=[x.this if isinstance(x,exp.Alias) else x for x in tree.expressions]
        if not any(isinstance(x,aggregate) and matches(x.this,metric['column']) for x in outputs):
            raise PersonalMemoryError('PERSONAL_MEMORY_METRIC_MISSING','查询未能确认采用个人指标口径，请确认字段与计算方式。')


def successful_descriptor(sql, dialect, question):
    tree=sqlglot.parse_one(sql,read=dialect)
    tables={x.alias_or_name.casefold():x.name for x in tree.find_all(exp.Table)}
    def reference(column):
        model=tables.get(column.table.casefold(),column.table) if column.table else next(iter(tables.values())) if len(tables)==1 else ''
        return f'{model}.{column.name}' if model else column.name
    references=sorted({reference(x) for x in tree.find_all(exp.Column)})[:40]
    steps=['query']
    if tree.find(exp.Group):steps.append('group')
    if tree.find(exp.Order):steps.append('sort')
    aggregations=sorted({x.key for x in tree.find_all(exp.AggFunc)})[:10]
    return {'steps':steps,'references':references,'aggregations':aggregations,'question':question[:500]}
