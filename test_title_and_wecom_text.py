import app
import core


def test_extract_title_from_html_supports_single_quotes_and_reversed_meta_attrs():
    html = """
    <html><head>
      <meta content='真正标题' property='og:title'>
      <title>站点默认标题 - YouTube</title>
    </head></html>
    """
    assert core.extract_title_from_html(html) == "真正标题"


def test_extract_title_from_html_cleans_platform_suffix_and_whitespace():
    html = "<title>  这是作品标题 ｜ 抖音  </title>"
    assert core.extract_title_from_html(html) == "这是作品标题"


def test_wecom_created_feedback_falls_back_and_shortens_long_source():
    job = {
        "id": "job-created-1",
        "platform": "generic",
        "title": "",
        "output": "",
        "source_url": "https://example.com/" + "a" * 220,
    }
    text = app.build_wecom_job_created_feedback(job)
    assert "[通用链接] 收到任务" in text
    assert "文件：https://example.com/" in text
    assert "…" in text
    assert len(text) <= app.WECOM_MESSAGE_MAX_LEN


def test_wecom_started_feedback_keeps_short_title_natural():
    job = {
        "id": "job-started-1",
        "platform": "douyin",
        "title": "好",
        "output": "好.mp4",
        "status": "downloading",
        "status_text": "开始下载",
    }
    text = app.build_wecom_job_started_feedback(job)
    assert "文件：好" in text
    assert "状态：开始下载" in text


def test_wecom_completion_feedback_truncates_long_title_and_error():
    long_title = "标题" * 80
    long_error = "错误原因" * 80
    job = {
        "id": "job-done-1",
        "platform": "youtube",
        "status": "failed",
        "title": long_title,
        "output": "out.mp4",
        "error": long_error,
        "source_url": "https://example.com/watch?v=123",
    }
    text = app.build_wecom_job_completion_feedback(job)
    assert "[YouTube] 下载失败" in text
    assert "标题：" in text
    assert "文件：out.mp4" in text
    assert "原因：" in text
    assert "…" in text
    assert len(text) <= app.WECOM_MESSAGE_MAX_LEN


def test_resolve_job_display_name_prefers_title_over_output():
    job = {
        "platform": "bilibili",
        "title": "真正标题",
        "output": "乱七八糟输出名.mp4",
        "source_url": "https://example.com/v",
    }
    assert app.resolve_job_display_name(job) == "真正标题"
