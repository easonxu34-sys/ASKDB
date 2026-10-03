from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

import sqlglot
from sqlglot import exp

from askdb_agent.domain.memory_recall import QueryParameterSpec, QueryParameterType
from askdb_agent.domain.query_policy import SQLGLOT_DIALECTS, validate_read_query


class SqlTemplateError(ValueError):
    """A query-example template or its typed parameter values are invalid."""


@dataclass(frozen=True)
class ValidatedSqlTemplate:
    sql: str
    planned_sql: str | None


def _dialect_name(connector_type: str) -> str:
    dialect = SQLGLOT_DIALECTS.get(connector_type.lower(), connector_type.lower())
    if not dialect:
        raise SqlTemplateError("connector dialect is unavailable")
    return dialect


def _parse_template(
    template: str,
    parameter_specs: tuple[QueryParameterSpec, ...],
    connector_type: str,
) -> tuple[exp.Query, str, dict[str, QueryParameterSpec]]:
    if not isinstance(template, str) or not template.strip() or len(template) > 20_000:
        raise SqlTemplateError("SQL template is invalid")
    if len(parameter_specs) > 32:
        raise SqlTemplateError("too many SQL template parameters")
    specs: dict[str, QueryParameterSpec] = {}
    for item in parameter_specs:
        if not isinstance(item.name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item.name):
            raise SqlTemplateError("SQL template parameter name is invalid")
        if item.name in specs:
            raise SqlTemplateError("duplicate SQL template parameter")
        specs[item.name] = item
    dialect = _dialect_name(connector_type)
    try:
        statements = sqlglot.parse(template, read=dialect)
    except sqlglot.errors.ParseError as exc:
        raise SqlTemplateError("SQL template could not be parsed") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise SqlTemplateError("SQL template must be one read-only query")
    tree = statements[0]
    if isinstance(tree, exp.Select) and tree.args.get("locks"):
        raise SqlTemplateError("locking queries are not allowed")
    placeholders = list(tree.find_all(exp.Placeholder))
    names: set[str] = set()
    for placeholder in placeholders:
        name = placeholder.name
        if not name or name == "?" or name not in specs:
            raise SqlTemplateError("SQL template has an unnamed or undeclared placeholder")
        parent = placeholder.parent
        if parent is None or isinstance(
            parent,
            (exp.Table, exp.Column, exp.Identifier, exp.DataType, exp.Schema, exp.TableAlias),
        ):
            raise SqlTemplateError("SQL template parameters can only occupy value positions")
        names.add(name)
    if names != set(specs):
        raise SqlTemplateError("SQL template parameters do not match the declared parameter list")
    return tree, dialect, specs


def _decimal_literal(value: Any) -> exp.Expr:
    if isinstance(value, bool) or isinstance(value, float):
        raise SqlTemplateError("decimal parameters must be sent as an integer or decimal string")
    if not isinstance(value, (int, str, Decimal)):
        raise SqlTemplateError("decimal parameter has the wrong value type")
    try:
        decimal_value = Decimal(value) if not isinstance(value, int) else Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise SqlTemplateError("decimal parameter is invalid") from exc
    if not decimal_value.is_finite() or len(decimal_value.as_tuple().digits) > 38:
        raise SqlTemplateError("decimal parameter exceeds the supported precision")
    encoded = format(decimal_value, "f")
    if len(encoded) > 128:
        raise SqlTemplateError("decimal parameter is too large")
    return exp.Literal.number(encoded)


def _typed_literal(spec: QueryParameterSpec, value: Any) -> exp.Expr:
    if value is None:
        if not spec.nullable:
            raise SqlTemplateError(f"parameter {spec.name} does not allow null")
        return exp.Null()
    value_type = spec.value_type
    if value_type == QueryParameterType.STRING:
        if not isinstance(value, str) or len(value) > 4000:
            raise SqlTemplateError(f"parameter {spec.name} must be a bounded string")
        return exp.Literal.string(value)
    if value_type == QueryParameterType.INTEGER:
        if isinstance(value, bool) or not isinstance(value, int) or not -(2**63) <= value < 2**63:
            raise SqlTemplateError(f"parameter {spec.name} must be a signed 64-bit integer")
        return exp.Literal.number(value)
    if value_type == QueryParameterType.DECIMAL:
        return _decimal_literal(value)
    if value_type == QueryParameterType.BOOLEAN:
        if not isinstance(value, bool):
            raise SqlTemplateError(f"parameter {spec.name} must be a boolean")
        return exp.Boolean(this=value)
    if value_type == QueryParameterType.DATE:
        if isinstance(value, datetime):
            raise SqlTemplateError(f"parameter {spec.name} must be a date")
        try:
            parsed = value if isinstance(value, date) else date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise SqlTemplateError(f"parameter {spec.name} must use ISO date format") from exc
        return exp.Cast(this=exp.Literal.string(parsed.isoformat()), to=exp.DataType.build("DATE"))
    if value_type == QueryParameterType.DATETIME:
        try:
            parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise SqlTemplateError(f"parameter {spec.name} must use ISO datetime format") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise SqlTemplateError(f"parameter {spec.name} must include a timezone")
        return exp.Cast(
            this=exp.Literal.string(parsed.isoformat()),
            to=exp.DataType.build("TIMESTAMP"),
        )
    raise SqlTemplateError(f"parameter {spec.name} has an unsupported value type")


def bind_sql_template(
    template: str,
    *,
    parameter_specs: tuple[QueryParameterSpec, ...],
    values: dict[str, Any],
    connector_type: str,
) -> str:
    """Bind named ``:parameter`` placeholders through SQLGlot AST nodes."""
    if not isinstance(values, dict):
        raise SqlTemplateError("parameter values must be an object")
    tree, dialect, specs = _parse_template(template, parameter_specs, connector_type)
    if set(values) != set(specs):
        raise SqlTemplateError("supplied parameter values do not match the template")
    try:
        bound = tree.transform(
            lambda node: _typed_literal(specs[node.name], values[node.name])
            if isinstance(node, exp.Placeholder) else node,
            copy=True,
        )
        sql = bound.sql(dialect=dialect, pretty=False)
        validate_read_query(sql, connector_type)
        return sql
    except SqlTemplateError:
        raise
    except (KeyError, TypeError, ValueError, sqlglot.errors.ParseError) as exc:
        raise SqlTemplateError("SQL template binding failed") from exc


def sample_parameter_values(
    parameter_specs: tuple[QueryParameterSpec, ...],
) -> dict[str, Any]:
    """Generate non-sensitive typed probes for review-time Wren dry-plan checks."""
    samples: dict[str, Any] = {}
    for spec in parameter_specs:
        samples[spec.name] = {
            QueryParameterType.STRING: "__askdb_validation__",
            QueryParameterType.INTEGER: 0,
            QueryParameterType.DECIMAL: "0.0",
            QueryParameterType.BOOLEAN: False,
            QueryParameterType.DATE: "2000-01-01",
            QueryParameterType.DATETIME: "2000-01-01T00:00:00+00:00",
        }[spec.value_type]
    return samples


def validate_query_example_template(
    template: str,
    *,
    parameter_specs: tuple[QueryParameterSpec, ...],
    connector_type: str,
    toolkit: Any,
) -> ValidatedSqlTemplate:
    """Run AST binding, current SQL policy, and Wren dry-plan/dry-run for review."""
    bound_sql = bind_sql_template(
        template,
        parameter_specs=parameter_specs,
        values=sample_parameter_values(parameter_specs),
        connector_type=connector_type,
    )
    try:
        planned_sql = toolkit.dry_plan(bound_sql)
        toolkit.dry_run(bound_sql)
    except Exception as exc:
        # Never leak provider errors, SQL values, or connector details to API clients.
        raise SqlTemplateError("Wren rejected the query-example template") from exc
    return ValidatedSqlTemplate(sql=bound_sql, planned_sql=planned_sql)


def validate_bound_query_for_use(
    template: str,
    *,
    parameter_specs: tuple[QueryParameterSpec, ...],
    values: dict[str, Any],
    connector_type: str,
    toolkit: Any,
) -> ValidatedSqlTemplate:
    """Repeat the current query gate and Wren validation each time a template is used."""
    bound_sql = bind_sql_template(
        template,
        parameter_specs=parameter_specs,
        values=values,
        connector_type=connector_type,
    )
    try:
        planned_sql = toolkit.dry_plan(bound_sql)
        toolkit.dry_run(bound_sql)
    except Exception as exc:
        raise SqlTemplateError("Wren rejected the bound query-example SQL") from exc
    return ValidatedSqlTemplate(sql=bound_sql, planned_sql=planned_sql)
