def test_homepage_links_to_social_profiles_and_public_source(client):
    page = client.get("/")

    assert page.status_code == 200
    assert (
        b'id="bsky-link" '
        b'href="https://bsky.app/profile/did:plc:y2oqhgnbw66jtd5hnbda76nk"'
    ) in page.data
    assert (
        b'id="instagram-link" '
        b'href="https://www.instagram.com/bristolbusbot/"'
    ) in page.data
    assert (
        b'id="github-link" class="github-link"'
    ) in page.data
    assert (
        b'href="https://github.com/bristol-bus-bot/bristolbusbot"'
    ) in page.data

    bluesky_link = page.data.index(b'id="bsky-link"')
    instagram_link = page.data.index(b'id="instagram-link"')
    assert bluesky_link < instagram_link


def test_header_uses_icons_with_text_labels(client):
    page = client.get("/").data
    assert b'id="github-header-link" href="https://github.com/bristol-bus-bot/bristolbusbot"' in page
    assert b'aria-label="Source code on GitHub"' in page
    # Bluesky is an icon now: no handle text, but still a label for screen readers.
    assert b"@bristolbusbot.live</span>" not in page
    assert b'aria-label="Bristol Bus Bot on Bluesky"' in page
    # The layer toggles keep the ids app.js uses, and gain labels.
    assert b'id="toggle-polylines-btn" class="toggle-btn" type="button"' in page
    assert b'aria-label="Show routes"' in page and b'aria-label="Show boundary"' in page
    assert b'id="toggle-boundary-btn"' in page
    # Order of the outside links.
    order = [page.index(b'id="bsky-link"'), page.index(b'id="instagram-link"'),
             page.index(b'id="github-header-link"')]
    assert order == sorted(order)
    # One scrolling row after the logo.
    assert page.count(b'class="header-scroll"') == 1


def test_coffee_link_appears_only_for_a_plain_kofi_address(app, client):
    assert b'id="coffee-link"' not in client.get("/").data
    app.config["BBB"].kofi_url = "https://ko-fi.com/bristolbusbot"
    page = client.get("/").data
    assert b'id="coffee-link" href="https://ko-fi.com/bristolbusbot"' in page
    assert b'aria-label="Buy me a coffee"' in page
    assert page.index(b'id="github-header-link"') < page.index(b'id="coffee-link"')
    for bad in ("http://ko-fi.com/x", "https://ko-fi.com/x?y=1", "https://evil.example/ko-fi.com/x",
                "javascript:alert(1)"):
        app.config["BBB"].kofi_url = bad
        assert b'id="coffee-link"' not in client.get("/").data
