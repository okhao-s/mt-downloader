import core


def test_extract_title_from_html_filters_x_failure_page_title():
    html = """
    <html><head>
    <title>JavaScript is not available.</title>
    <meta property=\"og:title\" content=\"JavaScript is not available.\" />
    </head><body></body></html>
    """
    assert core.extract_title_from_html(html) is None
