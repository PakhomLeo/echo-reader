from medialab import UrlContext, sanitize_persisted_url


def test_sanitize_persisted_url_keeps_identity_query_and_drops_secrets():
    value = sanitize_persisted_url(
        "https://mp.weixin.qq.com/s?id=42&xsec_token=secret&utm_source=share&lang=zh#part"
    )
    assert value == "https://mp.weixin.qq.com/s?id=42&lang=zh"


def test_sanitize_persisted_url_handles_signed_cdn_keys_case_insensitively():
    value = sanitize_persisted_url(
        "https://cdn.example/file.mp4?X-Amz-Signature=s&X-Amz-Date=d&quality=hd"
    )
    assert value == "https://cdn.example/file.mp4?quality=hd"


def test_sanitize_persisted_url_scrubs_userinfo_poc_token_and_nested_urls():
    value = sanitize_persisted_url(
        "https://user:password@example.test/open?poc_token=secret&next="  # trufflehog:ignore (test fixture)
        "https%3A%2F%2Ftarget.test%2Fwatch%3Fid%3D7%26xsec_token%3Dnested"
    )
    assert value == (
        "https://example.test/open?next="
        "https%3A%2F%2Ftarget.test%2Fwatch%3Fid%3D7"
    )


def test_url_context_keeps_runtime_url_but_canonicalizes_youtu_be_session():
    raw = "https://youtu.be/abc123?si=share&poc_token=secret"
    context = UrlContext.from_url(raw)
    assert context.runtime_url == raw
    assert context.public_url == "https://youtu.be/abc123"
    assert context.identity_url == context.public_url
    assert context.session_url == "https://www.youtube.com/watch?v=abc123"
    assert context.platform == "youtube"
