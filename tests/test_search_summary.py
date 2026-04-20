import time
import pyarrow as pa
import pyarrow.parquet as pq
import analytics_server


def test_search_summary_includes_seen_with_and_behavior(tmp_path):
    now = int(time.time())
    wifi_path = tmp_path / 'wifi_capture.parquet'
    wifi_table = pa.table({
        'timestamp': pa.array([now - 30, now - 20], type=pa.timestamp('s')),
        'bssid': pa.array(['AA:AA:AA:AA:AA:AA', 'AA:AA:AA:AA:AA:AA'], type=pa.string()),
        'ssid': pa.array(['TestNet', 'TestNet'], type=pa.string()),
        'rssi': pa.array([-50, -52], type=pa.int32()),
        'channel': pa.array([1, 1], type=pa.int32()),
        'frame_type': pa.array([8, 8], type=pa.int32()),
        'source': pa.array(['00:11:22:33:44:55', '00:11:22:33:44:55'], type=pa.string()),
        'destination': pa.array(['ff:ff:ff:ff:ff:ff', 'ff:ff:ff:ff:ff:ff'], type=pa.string()),
    })
    pq.write_table(wifi_table, str(wifi_path))

    ble_path = tmp_path / 'ble_capture.parquet'
    ble_table = pa.table({
        'timestamp': pa.array([now - 30], type=pa.timestamp('s')),
        'addr': pa.array(['BB:BB:BB:BB:BB:BB'], type=pa.string()),
        'name': pa.array(['BleDevice'], type=pa.string()),
        'rssi': pa.array([-65], type=pa.int32()),
    })
    pq.write_table(ble_table, str(ble_path))

    analytics_server.WIFI_PARQUET = str(wifi_path)
    analytics_server.BLE_PARQUET = str(ble_path)
    analytics_server.blocked_set.clear()

    result = analytics_server.search_capture({'q': ['AA:AA:AA:AA:AA:AA'], 'since': ['3600']})

    assert result['query'] == 'AA:AA:AA:AA:AA:AA'
    assert result['wifi']['count'] == 2
    assert result['ble']['count'] == 0

    summary = result['summary']
    assert summary is not None
    assert summary['total_matches'] == 2
    assert 'frequency_per_hour' in summary
    assert summary['behavior']['field'] in ('frame_type', 'ssid')
    assert summary['behavior']['dominant'] in (8, 'TestNet')
    assert isinstance(summary['seen_with'], list)
    assert any(entry['device'].startswith('ble:BB:BB:BB:BB:BB:BB') for entry in summary['seen_with'])
