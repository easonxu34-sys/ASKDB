import json
import importlib.util
import unittest
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from domain.chart_artifact import QueryResultArtifact

streaming_path = Path(__file__).resolve().parents[1] / "src" / "api" / "streaming.py"
streaming_spec = importlib.util.spec_from_file_location("askdb_streaming", streaming_path)
streaming_module = importlib.util.module_from_spec(streaming_spec)
assert streaming_spec is not None and streaming_spec.loader is not None
streaming_spec.loader.exec_module(streaming_module)
encode_sse = streaming_module.encode_sse


class QueryArtifactSerializationTests(unittest.TestCase):
    def test_json_safe_values_keep_temporal_and_decimal_precision(self):
        artifact = QueryResultArtifact(
            result_id="result-1",
            sql="SELECT ...",
            columns=("day", "clock", "created", "elapsed", "revenue", "large_id", "invalid", "payload"),
            column_types=(
                "date32[day]",
                "time64[us]",
                "timestamp[us, tz=UTC]",
                "duration[us]",
                "decimal128(24, 8)",
                "int64",
                "double",
                "struct<items: list<item: binary>>",
            ),
            rows=(
                {
                    "day": date(2026, 10, 4),
                    "clock": time(9, 30, 1, 123000),
                    "created": datetime(2026, 10, 4, 1, 30, tzinfo=timezone.utc),
                    "elapsed": timedelta(days=1, hours=2, minutes=3, seconds=4),
                    "revenue": Decimal("123456789012345678.12345678"),
                    "large_id": 9_007_199_254_740_993,
                    "invalid": float("nan"),
                    "payload": {"items": [b"\x00\xff"]},
                },
            ),
            row_count=1,
            truncated=False,
        )

        payload = {"output": {"data": artifact.to_dict()}}
        frame = encode_sse("result", payload)
        encoded = json.loads(frame.splitlines()[1].removeprefix("data: "))["output"]["data"]
        row = encoded["rows"][0]

        self.assertEqual(row["day"], "2026-10-04")
        self.assertEqual(row["clock"], "09:30:01.123000")
        self.assertEqual(row["created"], "2026-10-04T01:30:00+00:00")
        self.assertEqual(row["elapsed"], "1 day, 2:03:04")
        self.assertEqual(row["revenue"], "123456789012345678.12345678")
        self.assertEqual(row["large_id"], "9007199254740993")
        self.assertIsNone(row["invalid"])
        self.assertEqual(row["payload"], {"items": ["hex:00ff"]})
        self.assertIsInstance(artifact.rows[0]["revenue"], Decimal)


if __name__ == "__main__":
    unittest.main()
