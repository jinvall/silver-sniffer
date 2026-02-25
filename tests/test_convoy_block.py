import time
import pyarrow as pa
import pyarrow.parquet as pq
import analytics_server


def make_parquet(path, timestamps, bssids):
    tbl = pa.table({
        'timestamp': pa.array(timestamps, type=pa.timestamp('s')),
        'bssid': pa.array(bssids, type=pa.string())
    })
    pq.write_table(tbl, path)


def test_convoy_excludes_blocked(tmp_path):
    # write simple wifi parquet where two BSSIDs have identical buckets
    now = int(time.time())
    ts = [now - 60, now - 30, now - 10]

    # AA and BB appear in the same buckets -> convoy should be detected
    timestamps = ts + ts
    bssids = ['AA:AA:AA:AA:AA:AA'] * 3 + ['BB:BB:BB:BB:BB:BB'] * 3

    wifi_path = tmp_path / 'wifi_capture.parquet'
    make_parquet(str(wifi_path), timestamps, bssids)

    # empty BLE parquet
    ble_path = tmp_path / 'ble_capture.parquet'
    tbl = pa.table({'timestamp': pa.array([], type=pa.timestamp('s')), 'addr': pa.array([], type=pa.string())})
    pq.write_table(tbl, str(ble_path))

    # point analytics_server to temp parquet files
    analytics_server.WIFI_PARQUET = str(wifi_path)
    analytics_server.BLE_PARQUET = str(ble_path)

    # ensure no blocked devices
    analytics_server.blocked_set.clear()

    # smaller bucket size ensures each timestamp maps to a distinct bucket
    res = analytics_server.convoy_detection({}, bucket_seconds=5)
    assert 'convoys' in res
    assert len(res['convoys']) >= 1
    # both devices should appear in a member pair
    members = set(','.join(sorted(c['members'])) for c in res['convoys'])
    assert any('AA:AA:AA:AA:AA:AA' in c and 'BB:BB:BB:BB:BB:BB' in c for c in ([','.join(sorted(c['members'])) for c in res['convoys']]) )


def test_wifi_timeline_unique(tmp_path):
    # timeline should report number of distinct BSSIDs per bucket
    now = int(time.time())
    # three rows within same time window: two unique addresses
    ts = [now, now, now]
    bssids = ['A', 'A', 'B']

    wifi_path = tmp_path / 'wifi_capture.parquet'
    # add dummy rssi/channel columns so analytics_server can read the file
    tbl = pa.table({
        'timestamp': pa.array(ts, type=pa.timestamp('s')),
        'bssid': pa.array(bssids, type=pa.string()),
        'rssi': pa.array([0] * len(ts), type=pa.int32()),
        'channel': pa.array([1] * len(ts), type=pa.int32()),
    })
    pq.write_table(tbl, str(wifi_path))

    analytics_server.WIFI_PARQUET = str(wifi_path)
    res = analytics_server.wifi_timeline({}, bucket_seconds=60)
    assert 'buckets' in res
    assert res['buckets']
    assert res['buckets'][0]['count'] == 2
    # timeline should now include the per-bucket bssid list
    assert 'bssids' in res['buckets'][0]
    assert set(res['buckets'][0]['bssids']) == {'A', 'B'}

    # now block AA and verify convoys no longer include it
    analytics_server.blocked_set.add('wifi:AA:AA:AA:AA:AA:AA')
    res2 = analytics_server.convoy_detection({}, bucket_seconds=5)
    # convoys should not include AA
    for c in res2.get('convoys', []):
        assert not any(m.startswith('wifi:AA:AA:AA') for m in c['members'])


# ------------------------------------------------------------------
# additional BLE-specific coverage
# ------------------------------------------------------------------

def make_ble_parquet(path, timestamps, addrs, rssis=None):
    # create a simple parquet table containing BLE rows; if no RSSI list is
    # supplied we default to zeros so analytics functions that expect an rssi
    # column won't break.
    if rssis is None:
        rssis = [0] * len(timestamps)
    tbl = pa.table({
        'timestamp': pa.array(timestamps, type=pa.timestamp('s')),
        'addr': pa.array(addrs, type=pa.string()),
        'rssi': pa.array(rssis, type=pa.int32()),
    })
    pq.write_table(tbl, path)


def test_convoy_includes_ble(tmp_path):
    # create pure-BLE data with two addresses sharing the same buckets
    now = int(time.time())
    ts = [now - 60, now - 30, now - 10]

    timestamps = ts + ts
    addrs = ['AA:AA:AA:AA:AA:AA'] * 3 + ['BB:BB:BB:BB:BB:BB'] * 3

    wifi_path = tmp_path / 'wifi_capture.parquet'
    # empty wifi dataset
    tbl = pa.table({'timestamp': pa.array([], type=pa.timestamp('s')), 'bssid': pa.array([], type=pa.string())})
    pq.write_table(tbl, str(wifi_path))

    ble_path = tmp_path / 'ble_capture.parquet'
    make_ble_parquet(str(ble_path), timestamps, addrs)

    analytics_server.WIFI_PARQUET = str(wifi_path)
    analytics_server.BLE_PARQUET = str(ble_path)

    analytics_server.blocked_set.clear()
    res = analytics_server.convoy_detection({}, bucket_seconds=5)
    # should detect at least one convoy between the two BLE addresses
    assert any(set(c['members']) == {"ble:AA:AA:AA:AA:AA:AA", "ble:BB:BB:BB:BB:BB:BB"} for c in res.get('convoys', []))

    # block the first BLE device and ensure it disappears
    analytics_server.blocked_set.add('ble:AA:AA:AA:AA:AA:AA')
    res2 = analytics_server.convoy_detection({}, bucket_seconds=30)
    for c in res2.get('convoys', []):
        assert not any(m.startswith('ble:AA:AA:AA') for m in c['members'])


def test_ble_timeline(tmp_path):
    # timeline returns aggregated counts per bucket
    now = int(time.time())
    ts = [now - 20, now - 10, now]
    addrs = ['X'] * 3
    ble_path = tmp_path / 'ble_capture.parquet'
    make_ble_parquet(str(ble_path), ts, addrs)

    analytics_server.BLE_PARQUET = str(ble_path)
    res = analytics_server.ble_timeline({}, bucket_seconds=60)
    assert 'buckets' in res
    assert res['buckets']
    # sum of events across all buckets should equal number of rows we wrote
    total = sum(b['count'] for b in res['buckets'])
    assert total == 3


def test_convoy_mixed_wifi_ble(tmp_path):
    # same MAC used for wifi and BLE should form a cross-protocol convoy
    now = int(time.time())
    # use three timestamps so each device satisfies MIN_BUCKETS_FOR_DEVICE
    ts = [now - 60, now - 30, now - 10]

    wifi_path = tmp_path / 'wifi_capture.parquet'
    make_parquet(str(wifi_path), ts, ['AA:AA:AA:AA:AA:AA'] * 3)

    ble_path = tmp_path / 'ble_capture.parquet'
    make_ble_parquet(str(ble_path), ts, ['AA:AA:AA:AA:AA:AA'] * 3)

    analytics_server.WIFI_PARQUET = str(wifi_path)
    analytics_server.BLE_PARQUET = str(ble_path)
    analytics_server.blocked_set.clear()

    res = analytics_server.convoy_detection({}, bucket_seconds=5)
    assert any(set(c['members']) == {'wifi:AA:AA:AA:AA:AA:AA', 'ble:AA:AA:AA:AA:AA:AA'} for c in res.get('convoys', []))


def test_compute_ble_metrics_sequence():
    # verify distance/movement state machine behaves as expected
    from ingest import compute_ble_metrics, ble_last_distance

    ble_last_distance.clear()
    # initial call should be unknown
    d1, m1, c1 = compute_ble_metrics('FF:FF:FF:FF:FF:FF', -60)
    assert m1 == 'unknown'
    assert c1 == 'gray'

    # stronger RSSI → closer → approach
    d2, m2, c2 = compute_ble_metrics('FF:FF:FF:FF:FF:FF', -50)
    assert m2 == 'approach'
    assert c2 == 'green'
    assert d2 < d1

    # weaker RSSI → farther → depart
    d3, m3, c3 = compute_ble_metrics('FF:FF:FF:FF:FF:FF', -70)
    assert m3 == 'depart'
    assert c3 == 'red'
    assert d3 > d2

    # same RSSI as previous call → steady
    d4, m4, c4 = compute_ble_metrics('FF:FF:FF:FF:FF:FF', -70)
    assert m4 == 'steady'
    assert c4 == 'yellow'
    assert d4 == d3
