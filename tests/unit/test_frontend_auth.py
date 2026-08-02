from frontend.app import parse_gradio_auth_users


def test_parse_gradio_auth_users_empty_returns_none():
    assert parse_gradio_auth_users("") is None
    assert parse_gradio_auth_users("   ") is None


def test_parse_gradio_auth_users_single_pair():
    assert parse_gradio_auth_users("admin:changeme") == [("admin", "changeme")]


def test_parse_gradio_auth_users_multiple_pairs():
    result = parse_gradio_auth_users("admin:changeme,guest:guestpass")
    assert result == [("admin", "changeme"), ("guest", "guestpass")]


def test_parse_gradio_auth_users_strips_whitespace():
    result = parse_gradio_auth_users(" admin : changeme , guest:guestpass ")
    assert result == [("admin", "changeme"), ("guest", "guestpass")]


def test_parse_gradio_auth_users_skips_malformed_entries():
    """An entry missing ':' or with an empty username/password is dropped, not fatal."""
    result = parse_gradio_auth_users("admin:changeme,nocolon,alsoskip:,guest:guestpass")
    assert result == [("admin", "changeme"), ("guest", "guestpass")]


def test_parse_gradio_auth_users_all_malformed_returns_none():
    assert parse_gradio_auth_users("nocolon,:missingusername,trailing:") is None


def test_parse_gradio_auth_users_ignores_empty_segments():
    result = parse_gradio_auth_users("admin:changeme,,")
    assert result == [("admin", "changeme")]


def test_parse_gradio_auth_users_password_can_contain_colon():
    """partition() on the first ':' means passwords may themselves contain ':'."""
    result = parse_gradio_auth_users("admin:pass:word")
    assert result == [("admin", "pass:word")]
