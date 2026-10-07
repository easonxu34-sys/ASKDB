from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from application.conversation_memory import sanitize_turn_text
from domain.memory_recall import RecallDocument, RecallKind


_UNSTABLE_MDL_KEYS = frozenset(
    {
        "buildtimestamp",
        "generatedat",
        "generatedtimestamp",
        "updatedat",
        "createdat",
        "layoutversion",
        "sourcepath",
        "buildpath",
    }
)
_BUSINESS_RULE_ID = re.compile(r"^askdb_br_([a-f0-9]{32})$", re.IGNORECASE)
_HEADING = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*#*\s*$")


def _stable_mdl(value: Any) -> Any:
    if isinstance(value, dict):
        stable: dict[str, Any] = {}
        for key in sorted(value):
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized_key in _UNSTABLE_MDL_KEYS:
                continue
            stable[str(key)] = _stable_mdl(value[key])
        return stable
    if isinstance(value, list):
        return [_stable_mdl(item) for item in value]
    return value


def _project_file(project_dir: Path, relative: str) -> Path:
    root = project_dir.expanduser().resolve()
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("compiled Wren semantic artifact is missing or outside its revision")
    return path


def compute_semantic_digest(project_dir: Path, connector_type: str) -> str:
    """Hash compiled MDL semantics, connector, and sorted rule path/content pairs."""
    mdl_path = _project_file(project_dir, "target/mdl.json")
    try:
        mdl = json.loads(mdl_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("compiled Wren MDL cannot be read") from exc
    if not isinstance(mdl, dict):
        raise ValueError("compiled Wren MDL must be an object")

    rules_root = (project_dir.expanduser().resolve() / "knowledge" / "rules").resolve()
    root = project_dir.expanduser().resolve()
    if root not in rules_root.parents:
        raise ValueError("Wren rules path is outside its revision")
    rules: list[dict[str, str]] = []
    if rules_root.exists():
        for path in sorted(rules_root.rglob("*.md"), key=lambda item: item.relative_to(root).as_posix()):
            resolved = path.resolve()
            if root not in resolved.parents or not resolved.is_file():
                raise ValueError("Wren rule path is outside its revision")
            relative = resolved.relative_to(root).as_posix()
            rules.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
                }
            )

    payload = {
        "connector_type": connector_type.casefold(),
        "mdl": _stable_mdl(mdl),
        "rules": rules,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_semantic_reference_names(project_dir: Path) -> dict[str, str]:
    """Return canonical visible MDL model/member names for candidate validation."""
    mdl_path = _project_file(project_dir.expanduser().resolve(), "target/mdl.json")
    try:
        mdl = json.loads(mdl_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("compiled Wren MDL cannot be read") from exc
    if not isinstance(mdl, dict):
        raise ValueError("compiled Wren MDL must be an object")

    references: dict[str, str] = {}

    def add(value: str) -> None:
        normalized = unicodedata.normalize("NFKC", value).casefold().strip()
        if normalized:
            references[normalized] = value

    models = mdl.get("models", [])
    if not isinstance(models, list):
        raise ValueError("compiled Wren MDL models must be a list")
    for model in models:
        if not isinstance(model, dict) or model.get("isHidden") or model.get("is_hidden"):
            continue
        model_name = str(model.get("name", "")).strip()
        if not model_name:
            continue
        add(model_name)
        columns = model.get("columns", [])
        if not isinstance(columns, list):
            continue
        for column in columns:
            if (
                not isinstance(column, dict)
                or column.get("isHidden")
                or column.get("is_hidden")
            ):
                continue
            column_name = str(column.get("name", "")).strip()
            if column_name:
                add(f"{model_name}.{column_name}")

    for collection_name in ("views", "cubes", "relationships"):
        collection = mdl.get(collection_name, [])
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, dict) or item.get("isHidden") or item.get("is_hidden"):
                continue
            parent_name = str(item.get("name", "")).strip()
            if not parent_name:
                continue
            add(parent_name)
            for member_name in ("dimensions", "measures", "columns"):
                members = item.get(member_name, [])
                if not isinstance(members, list):
                    continue
                for member in members:
                    if (
                        not isinstance(member, dict)
                        or member.get("isHidden")
                        or member.get("is_hidden")
                    ):
                        continue
                    name = str(member.get("name", "")).strip()
                    if name:
                        add(f"{parent_name}.{name}")
    return references


def load_semantic_field_descriptions(
    project_dir: Path, requested_columns: Iterable[str]
) -> dict[str, str]:
    """Return bounded descriptions for uniquely named visible MDL columns."""
    requested_by_name: dict[str, set[str]] = defaultdict(set)
    for column_name in requested_columns:
        if not isinstance(column_name, str):
            continue
        normalized = unicodedata.normalize("NFKC", column_name).casefold().strip()
        if normalized:
            requested_by_name[normalized].add(column_name)
    if not requested_by_name:
        return {}

    mdl_path = _project_file(project_dir.expanduser().resolve(), "target/mdl.json")
    try:
        mdl = json.loads(mdl_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("compiled Wren MDL cannot be read") from exc
    if not isinstance(mdl, dict):
        raise ValueError("compiled Wren MDL must be an object")
    models = mdl.get("models", [])
    if not isinstance(models, list):
        raise ValueError("compiled Wren MDL models must be a list")

    matches: dict[str, list[str]] = defaultdict(list)
    for model in models:
        if not isinstance(model, dict) or model.get("isHidden") or model.get("is_hidden"):
            continue
        columns = model.get("columns", [])
        if not isinstance(columns, list):
            continue
        for column in columns:
            if (
                not isinstance(column, dict)
                or column.get("isHidden")
                or column.get("is_hidden")
            ):
                continue
            name = column.get("name")
            if not isinstance(name, str):
                continue
            normalized = unicodedata.normalize("NFKC", name).casefold().strip()
            if normalized not in requested_by_name:
                continue
            properties = column.get("properties")
            properties = properties if isinstance(properties, dict) else {}
            description = properties.get("description")
            if not isinstance(description, str):
                description = ""
            safe_description = sanitize_turn_text(description, max_chars=500).strip()
            matches[normalized].append(safe_description)

    descriptions: dict[str, str] = {}
    for normalized, result_names in requested_by_name.items():
        candidates = matches.get(normalized, [])
        if len(result_names) != 1 or len(candidates) != 1 or not candidates[0]:
            continue
        descriptions[next(iter(result_names))] = candidates[0]
    return descriptions


def load_semantic_rule_terms(project_dir: Path) -> tuple[str, ...]:
    """Return visible Wren rule titles for conservative exact-term collision checks."""
    project_root = project_dir.expanduser().resolve()
    rules_root = (project_root / "knowledge" / "rules").resolve()
    if project_root not in rules_root.parents:
        raise ValueError("Wren rules path is outside its revision")
    if not rules_root.exists():
        return ()

    titles: set[str] = set()
    for path in sorted(rules_root.rglob("*.md"), key=lambda item: item.relative_to(project_root).as_posix()):
        resolved = path.resolve()
        if project_root not in resolved.parents or not resolved.is_file():
            raise ValueError("Wren rule path is outside its revision")
        content = sanitize_turn_text(resolved.read_text(encoding="utf-8"), max_chars=20_000)
        if not content.strip():
            continue
        heading = _HEADING.search(content)
        title = heading.group(1).strip() if heading else resolved.stem
        safe_title = sanitize_turn_text(title, max_chars=500).strip()
        if safe_title:
            titles.add(safe_title)
    return tuple(sorted(titles, key=lambda item: unicodedata.normalize("NFKC", item).casefold()))


def load_semantic_recall_documents(
    project_dir: Path,
    *,
    data_source_id: str,
    wren_revision_id: str,
    connector_type: str,
    mdl_digest: str | None = None,
    configured_rules: Iterable[Any] = (),
) -> tuple[RecallDocument, ...]:
    """Create immutable, metadata-only recall documents from one Wren revision."""
    project_root = project_dir.expanduser().resolve()
    mdl_path = _project_file(project_root, "target/mdl.json")
    try:
        mdl = json.loads(mdl_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("compiled Wren MDL cannot be read") from exc
    if not isinstance(mdl, dict):
        raise ValueError("compiled Wren MDL must be an object")
    digest = mdl_digest or compute_semantic_digest(project_root, connector_type)

    configured_names_by_content: dict[str, list[str]] = defaultdict(list)
    for index, rule in enumerate(configured_rules):
        if isinstance(rule, str):
            configured_name, configured_content = f"rule_{index + 1}", rule
        elif isinstance(rule, dict):
            configured_name = str(rule.get("name") or f"rule_{index + 1}")
            configured_content = str(rule.get("content", ""))
        else:
            continue
        normalized_content = sanitize_turn_text(
            configured_content, max_chars=20_000
        ).strip()
        safe_name = sanitize_turn_text(configured_name, max_chars=500)
        safe_name = " ".join(safe_name.replace("\r", " ").replace("\n", " ").split())
        if normalized_content and safe_name:
            configured_title = _HEADING.search(normalized_content)
            display_name = (
                configured_title.group(1).strip()
                if _BUSINESS_RULE_ID.fullmatch(configured_name) and configured_title
                else safe_name
            )
            configured_names_by_content[normalized_content].extend(
                (display_name, safe_name)
            )

    documents: list[RecallDocument] = []
    models = mdl.get("models", [])
    if not isinstance(models, list):
        raise ValueError("compiled Wren MDL models must be a list")
    for model in models:
        if not isinstance(model, dict):
            continue
        if model.get("isHidden") or model.get("is_hidden"):
            continue
        name = str(model.get("name", "")).strip()
        if not name:
            continue
        table_reference = model.get("tableReference")
        table_reference = table_reference if isinstance(table_reference, dict) else {}
        table_name = str(table_reference.get("table", "")).strip()
        qualified_table = ".".join(
            str(table_reference.get(part, "")).strip()
            for part in ("catalog", "schema", "table")
            if str(table_reference.get(part, "")).strip()
        )
        properties = model.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        description = str(properties.get("description", "")).strip()
        columns = model.get("columns", [])
        columns = columns if isinstance(columns, list) else []
        terms = {name.casefold()}
        if table_name:
            terms.add(table_name.casefold())
        body_lines = [f"Table: {qualified_table or table_name or name}"]
        if description:
            body_lines.append(f"Description: {description}")
        for column in columns:
            if not isinstance(column, dict) or not str(column.get("name", "")).strip():
                continue
            if column.get("isHidden") or column.get("is_hidden"):
                continue
            column_name = str(column["name"]).strip()
            terms.add(column_name.casefold())
            column_properties = column.get("properties")
            column_properties = column_properties if isinstance(column_properties, dict) else {}
            column_description = str(column_properties.get("description", "")).strip()
            column_type = str(column.get("type", "")).strip()
            column_line = f"Column: {column_name}"
            if column_type:
                column_line += f" ({column_type})"
            if column_description:
                column_line += f" — {column_description}"
            body_lines.append(column_line)
        body = sanitize_turn_text("\n".join(body_lines), max_chars=20_000)
        document_id = hashlib.sha256(
            f"schema\0{data_source_id}\0{wren_revision_id}\0{name}".encode("utf-8")
        ).hexdigest()
        documents.append(
            RecallDocument(
                id=f"schema:{document_id}",
                data_source_id=data_source_id,
                kind=RecallKind.SCHEMA,
                title=name,
                terms=tuple(sorted(terms)),
                body=body,
                connector_type=connector_type,
                wren_revision_id=wren_revision_id,
                mdl_digest=digest,
                review_status="active",
                publication_status="active",
                content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            )
        )

    for collection_name, kind_label in (
        ("views", "view"),
        ("cubes", "cube"),
        ("relationships", "relationship"),
    ):
        collection = mdl.get(collection_name, [])
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, dict):
                continue
            if item.get("isHidden") or item.get("is_hidden"):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            properties = item.get("properties")
            properties = properties if isinstance(properties, dict) else {}
            terms = {name.casefold()}
            body_lines = [f"{kind_label.capitalize()}: {name}"]
            description = str(properties.get("description", "")).strip()
            if description:
                body_lines.append(f"Description: {description}")
            if kind_label == "relationship":
                related_models = item.get("models", [])
                if isinstance(related_models, list):
                    names = tuple(str(value).strip() for value in related_models if str(value).strip())
                    terms.update(value.casefold() for value in names)
                    if names:
                        body_lines.append(f"Related models: {', '.join(names)}")
                join_type = str(item.get("joinType", item.get("join_type", ""))).strip()
                if join_type:
                    body_lines.append(f"Relationship type: {join_type}")
            for member_name in ("dimensions", "measures", "columns"):
                members = item.get(member_name, [])
                if not isinstance(members, list):
                    continue
                for member in members:
                    if not isinstance(member, dict):
                        continue
                    if member.get("isHidden") or member.get("is_hidden"):
                        continue
                    member_id = str(member.get("name", "")).strip()
                    if not member_id:
                        continue
                    terms.add(member_id.casefold())
                    member_properties = member.get("properties")
                    member_properties = member_properties if isinstance(member_properties, dict) else {}
                    member_description = str(member_properties.get("description", "")).strip()
                    body_line = f"{member_name[:-1].capitalize()}: {member_id}"
                    if member_description:
                        body_line += f" — {member_description}"
                    body_lines.append(body_line)
            body = sanitize_turn_text("\n".join(body_lines), max_chars=20_000)
            document_id = hashlib.sha256(
                f"{kind_label}\0{data_source_id}\0{wren_revision_id}\0{name}".encode("utf-8")
            ).hexdigest()
            documents.append(
                RecallDocument(
                    id=f"schema:{document_id}",
                    data_source_id=data_source_id,
                    kind=RecallKind.SCHEMA,
                    title=name,
                    terms=tuple(sorted(terms)),
                    body=body,
                    connector_type=connector_type,
                    wren_revision_id=wren_revision_id,
                    mdl_digest=digest,
                    review_status="active",
                    publication_status="active",
                    content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                )
            )

    rules_root = (project_root / "knowledge" / "rules").resolve()
    if project_root not in rules_root.parents:
        raise ValueError("Wren rules path is outside its revision")
    if rules_root.exists():
        for path in sorted(rules_root.rglob("*.md"), key=lambda item: item.relative_to(project_root).as_posix()):
            resolved = path.resolve()
            if project_root not in resolved.parents or not resolved.is_file():
                raise ValueError("Wren rule path is outside its revision")
            content = sanitize_turn_text(
                resolved.read_text(encoding="utf-8"), max_chars=20_000
            )
            if not content.strip():
                continue
            heading = _HEADING.search(content)
            names_from_config = configured_names_by_content.get(content.strip(), [])
            if not names_from_config and heading is not None:
                legacy_body = re.sub(r"\A#{1,6}\s+[^\r\n]*(?:\r?\n){1,2}", "", content, count=1).strip()
                names_from_config = configured_names_by_content.get(legacy_body, [])
            heading_title = heading.group(1).strip() if heading else ""
            config_title_is_better = bool(
                names_from_config
                and (
                    not heading_title
                    or _BUSINESS_RULE_ID.fullmatch(heading_title)
                )
            )
            title_candidate = (
                names_from_config[0]
                if config_title_is_better
                else heading_title or resolved.stem
            )
            title = sanitize_turn_text(title_candidate, max_chars=500) or "业务规则"
            safe_stem = sanitize_turn_text(resolved.stem, max_chars=500)
            match = _BUSINESS_RULE_ID.fullmatch(resolved.stem)
            rule_id = match.group(1).lower() if match else resolved.relative_to(rules_root).as_posix()
            document_id = rule_id if match else f"rule:{rule_id}"
            documents.append(
                RecallDocument(
                    id=document_id,
                    data_source_id=data_source_id,
                    kind=RecallKind.BUSINESS_RULE,
                    title=title[:500],
                    terms=tuple(
                        dict.fromkeys(
                            value
                            for value in (safe_stem, title, *names_from_config)
                            if value
                        )
                    ),
                    body=content,
                    connector_type=connector_type,
                    wren_revision_id=wren_revision_id,
                    mdl_digest=digest,
                    review_status="active",
                    publication_status="active",
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                )
            )
    return tuple(documents)
