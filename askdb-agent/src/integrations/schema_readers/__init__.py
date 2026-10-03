from __future__ import annotations

from typing import Any

from integrations.wren_connectors import SUPPORTED_DATABASE_CONNECTORS
from integrations.wren_metadata_schema import WrenMetadataSchemaReader


class SchemaReaderRegistry:
    """Selects a metadata-only reader for an enabled Wren database connector."""

    @classmethod
    def create(
        cls,
        connector_type: str,
        connection_config: dict[str, Any],
        secrets: dict[str, str] | None = None,
    ) -> Any:
        if connector_type not in SUPPORTED_DATABASE_CONNECTORS:
            raise ValueError("Wren 数据源类型不受支持。")
        return WrenMetadataSchemaReader(connector_type, connection_config, secrets)
