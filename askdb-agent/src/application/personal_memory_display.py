"""Presentation metadata derived only from declared MDL source units."""
from __future__ import annotations

import json
from pathlib import Path

import sqlglot
from sqlglot import exp

UNITS={'元':'yuan','千元':'thousand_yuan','万元':'ten_thousand_yuan','亿元':'hundred_million_yuan'}

def load_units(project: Path):
    mdl=json.loads((project/'target/mdl.json').read_text(encoding='utf-8'))
    units={}
    for model in mdl.get('models',[]):
        if model.get('isHidden') or model.get('is_hidden'):continue
        for column in model.get('columns',[]):
            if column.get('isHidden') or column.get('is_hidden'):continue
            unit=(column.get('properties') or {}).get('unit')
            if isinstance(unit,str) and unit in UNITS:
                units[f"{model['name']}.{column['name']}".casefold()]=UNITS[unit]
    return units

def projection_formats(sql, dialect, units, display_unit):
    if not units or display_unit not in UNITS:return {}
    tree=sqlglot.parse_one(sql,read=dialect)
    if not isinstance(tree,exp.Select) or len(list(tree.find_all(exp.Select)))!=1:return {}
    tables={x.alias_or_name.casefold():x.name.casefold() for x in tree.find_all(exp.Table)}
    formats={}
    for projection in tree.expressions:
        value=projection.this if isinstance(projection,exp.Alias) else projection
        if isinstance(value,(exp.Sum,exp.Avg,exp.Min,exp.Max)):value=value.this
        if not isinstance(value,exp.Column):continue
        model=tables.get(value.table.casefold(),'') if value.table else next(iter(tables.values())) if len(tables)==1 else ''
        source=units.get(f'{model}.{value.name}'.casefold())
        if source and projection.alias_or_name:
            formats[projection.alias_or_name]={'mode':'unit_scale','unit_family':'CNY','source_unit':source,'display_unit':UNITS[display_unit],'decimal_places':'auto'}
    return formats
