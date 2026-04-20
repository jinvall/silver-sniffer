import datetime
import unittest

import analytics_server
import ingest

try:
    import pyarrow as pa
except ImportError:
    pa = None


class IngestAnalyticsTests(unittest.TestCase):
    def setUp(self):
        ingest.ble_last_distance.clear()
        ingest.wifi_last.clear()

    def test_compute_ble_metrics_movement(self):
        d1, m1, c1 = ingest.compute_ble_metrics("AA:BB:CC:DD:EE:FF", -70)
        self.assertEqual(m1, "unknown")
        d2, m2, c2 = ingest.compute_ble_metrics("AA:BB:CC:DD:EE:FF", -65)
        self.assertEqual(m2, "approach")
        d3, m3, c3 = ingest.compute_ble_metrics("AA:BB:CC:DD:EE:FF", -80)
        self.assertEqual(m3, "depart")

    def test_update_wifi_movement_threshold(self):
        msg1 = ingest.update_wifi_movement("11:22:33:44:55:66", -50, 1_000_000)
        self.assertFalse(msg1["moving"])

        msg2 = ingest.update_wifi_movement("11:22:33:44:55:66", -35, 1_200_000)
        self.assertTrue(msg2["moving"])
        self.assertEqual(msg2["rssi_delta"], 15)
        self.assertEqual(msg2["dt_ms"], 200)

    @unittest.skipIf(pa is None, "pyarrow not installed")
    def test_parse_time_range_since(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        table = pa.table(
            {
                "timestamp": [now, now + datetime.timedelta(seconds=10)],
                "bssid": ["A", "B"],
                "rssi": [ -80, -75],
                "channel": [1, 6],
            }
        )

        filtered, min_ts, max_ts = analytics_server.parse_time_range(
            table, {"since": ["5"]}
        )
        self.assertTrue(filtered.num_rows <= table.num_rows)
        self.assertIsInstance(min_ts, int)
        self.assertIsInstance(max_ts, int)

    @unittest.skipIf(pa is None, "pyarrow not installed")
    def test_bucketize_timestamp(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        table = pa.table({"timestamp": [now, now + datetime.timedelta(seconds=5)]})
        bucketed = analytics_server.bucketize_timestamp(table, bucket_seconds=5)
        self.assertEqual(len(bucketed), 2)


if __name__ == "__main__":
    unittest.main()
