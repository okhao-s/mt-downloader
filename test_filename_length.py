from pathlib import Path

import app
import core


def test_allocate_output_name_truncates_utf8_long_titles_to_safe_filename_length():
    title = "分享26个字母歌英语儿歌词本动画整套合集" * 20 + ".mp4"
    output = app.allocate_output_name(title, download_dir=Path("/tmp"))

    assert output.endswith(".mp4")
    assert len(output.encode("utf-8")) <= 240
    assert "/" not in output


if __name__ == "__main__":
    test_allocate_output_name_truncates_utf8_long_titles_to_safe_filename_length()
    print("PASS: test_allocate_output_name_truncates_utf8_long_titles_to_safe_filename_length")
