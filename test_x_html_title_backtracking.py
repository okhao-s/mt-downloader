import time

import core


X_FREEZE_HTML = """
<html><head>
<meta charset=\"utf-8\">
<meta content=\""" + ("x" * 220000) + "\" property=\"og:title\">\n<title>fallback title</title>\n</head><body></body></html>\n"


def test_extract_title_from_html_avoids_catastrophic_backtracking():
    started = time.time()
    title = core.extract_title_from_html(X_FREEZE_HTML)
    elapsed = time.time() - started
    assert elapsed < 1.0, f"extract_title_from_html too slow: {elapsed:.3f}s"
    assert title == "fallback title" or title == ("x" * 220000)
