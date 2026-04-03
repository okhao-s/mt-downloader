import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

import app as appmod


def test_home_survives_media_proxy_backpressure():
    original_safe_get = appmod.safe_requests_get

    def slow(*args, **kwargs):
        time.sleep(1.5)

        class Resp:
            status_code = 200
            text = '#EXTM3U\n#EXTINF:1,\nseg.ts\n'
            content = b'x'
            headers = {'content-type': 'application/octet-stream'}

            def raise_for_status(self):
                pass

        return Resp()

    appmod.safe_requests_get = slow
    client = TestClient(appmod.app)
    start = time.time()

    try:
        with ThreadPoolExecutor(max_workers=32) as ex:
            futures = [
                ex.submit(lambda i=i: client.get('/api/media', params={'target': f'https://example.com/{i}.ts'}).status_code)
                for i in range(24)
            ]
            time.sleep(0.2)
            home_resp = client.get('/')
            elapsed = time.time() - start
            statuses = [f.result() for f in futures]

        assert home_resp.status_code == 200
        assert elapsed < 1.2, f'home should not be blocked by media backlog, elapsed={elapsed:.2f}s'
        assert all(code == 200 for code in statuses)
    finally:
        appmod.safe_requests_get = original_safe_get
