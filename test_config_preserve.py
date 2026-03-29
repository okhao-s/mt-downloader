import json
import tempfile
from pathlib import Path

import app
import core


def test_set_config_keeps_wecom_secrets_when_blank_inputs_use_keep_sentinel():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.json"
        old_app_path = core.CONFIG_PATH
        core.CONFIG_PATH = config_path
        try:
            core.save_config({
                "wecom_enabled": True,
                "wecom_corp_id": "ww123",
                "wecom_agent_id": "1000002",
                "wecom_secret": "old-secret",
                "wecom_token": "old-token",
                "wecom_encoding_aes_key": "A" * 43,
                "wecom_callback_url": "https://example.com/api/wecom/callback",
            })

            payload = app.ConfigPayload(
                wecom_enabled=True,
                wecom_corp_id="ww123",
                wecom_agent_id="1000002",
                wecom_secret=app.CONFIG_KEEP_SENTINEL,
                wecom_token=app.CONFIG_KEEP_SENTINEL,
                wecom_encoding_aes_key=app.CONFIG_KEEP_SENTINEL,
                wecom_callback_url="https://example.com/api/wecom/callback",
            )
            app.set_config(payload)
            saved = json.loads(config_path.read_text(encoding="utf-8"))

            assert saved["wecom_secret"] == "old-secret"
            assert saved["wecom_token"] == "old-token"
            assert saved["wecom_encoding_aes_key"] == "A" * 43
        finally:
            core.CONFIG_PATH = old_app_path


if __name__ == "__main__":
    test_set_config_keeps_wecom_secrets_when_blank_inputs_use_keep_sentinel()
    print("PASS: test_set_config_keeps_wecom_secrets_when_blank_inputs_use_keep_sentinel")
