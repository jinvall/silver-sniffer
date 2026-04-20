import os
import analytics_server


def test_health_status_parquet_flags(tmp_path):
    # point analytics_server to temp parquet locations
    wifi = tmp_path / 'wifi_capture.parquet'
    ble = tmp_path / 'ble_capture.parquet'

    analytics_server.WIFI_PARQUET = str(wifi)
    analytics_server.BLE_PARQUET = str(ble)

    # no files -> parquet flags False
    h = analytics_server.health_status()
    assert isinstance(h, dict)
    assert 'parquet' in h
    assert h['parquet']['wifi_exists'] is False
    assert h['parquet']['ble_exists'] is False

    # create the files and re-check
    open(analytics_server.WIFI_PARQUET, 'wb').close()
    open(analytics_server.BLE_PARQUET, 'wb').close()

    h2 = analytics_server.health_status()
    assert h2['parquet']['wifi_exists'] is True
    assert h2['parquet']['ble_exists'] is True

    # health_status should always include ws/dashboard keys (booleans)
    assert isinstance(h2['ws'], bool)
    assert isinstance(h2['dashboard'], bool)
    assert isinstance(h2['blocked_count'], int)


def test_timeline_handles_missing_parquet(tmp_path):
    analytics_server.WIFI_PARQUET = str(tmp_path / 'missing_wifi.parquet')
    analytics_server.BLE_PARQUET = str(tmp_path / 'missing_ble.parquet')

    wifi = analytics_server.wifi_timeline({}, bucket_seconds=5)
    assert wifi == {"buckets": [], "bucket_seconds": 5}

    ble = analytics_server.ble_timeline({}, bucket_seconds=10)
    assert ble == {"buckets": [], "bucket_seconds": 10}
