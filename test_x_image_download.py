import app
import core


def test_resolve_download_mode_uses_image_for_image_media_type():
    assert app.resolve_download_mode("x", None, media_type="image") == "image"


def test_get_download_subdir_uses_image_x_for_x_images():
    path = app.get_download_subdir("https://x.com/user/status/123", media_type="image")
    assert str(path).endswith("/downloads/image/x")


def test_build_image_output_name_adds_index_and_ext():
    name = app.build_image_output_name("示例标题", 1, 3, "https://pbs.twimg.com/media/abc123?format=png&name=small")
    assert name.endswith(".png")
    assert "示例标题 - 2" in name


def test_discover_stream_marks_x_photo_post_as_image():
    original = core.extract_info_with_ytdlp
    try:
        core.extract_info_with_ytdlp = lambda *args, **kwargs: {
            "title": "图文帖",
            "thumbnail": "https://pbs.twimg.com/media/cover?format=jpg&name=small",
            "thumbnails": [
                {"url": "https://pbs.twimg.com/media/img1?format=jpg&name=orig", "id": "orig", "width": 1200, "height": 800},
                {"url": "https://pbs.twimg.com/media/img2?format=png&name=large", "id": "large", "width": 2000, "height": 1000},
            ],
        }
        info = core.discover_stream("https://x.com/user/status/123")
    finally:
        core.extract_info_with_ytdlp = original

    assert info["media_type"] == "image"
    assert len(info["images"]) == 2
    assert info["streams"] == []


if __name__ == "__main__":
    test_resolve_download_mode_uses_image_for_image_media_type()
    test_get_download_subdir_uses_image_x_for_x_images()
    test_build_image_output_name_adds_index_and_ext()
    test_discover_stream_marks_x_photo_post_as_image()
    print("PASS: test_x_image_download")
