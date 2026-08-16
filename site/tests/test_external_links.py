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
