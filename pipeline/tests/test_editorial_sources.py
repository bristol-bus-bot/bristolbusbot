from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discover_editorial_news as discovery
from editorial_sources import parse_feed, relevant_result

FEED = 'https://news.firstbus.co.uk/feed/rss'


def feed(link='https://news.firstbus.co.uk/news/bristol', date='Mon, 14 Sep 2026 10:00:00 Z'):
    return f'''<rss><channel><item><title>Bristol buses</title>
    <description><![CDATA[<p>New buses for Bristol &amp; Bath.</p><script>ignore me</script>]]></description>
    <link>{link}</link><pubDate>{date}</pubDate></item></channel></rss>'''.encode()


def test_rss_proposal_keeps_source_date_and_operator_scope():
    results = parse_feed(feed(), FEED)
    assert results[0]['description'] == 'New buses for Bristol & Bath.'
    candidate = discovery.select_candidate({'results': results}, {}, now=datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert candidate['item']['source']['publisher'] == 'First Bus'
    assert candidate['item']['scope'] == {'operators': ['FBRI']}
    assert candidate['item']['expires_at'] == '2026-09-21T10:00:00Z'
    assert candidate['id'].startswith('local-')


@pytest.mark.parametrize('url', ['https://evil.example/news', 'http://news.firstbus.co.uk/news/test',
                                      'https://news.firstbus.co.uk/news/a\nB=bad'])
def test_feed_rejects_untrusted_or_multiline_links(url):
    assert parse_feed(feed(link=url), FEED) == []


def test_feed_requires_real_publication_date_and_bounded_xml():
    assert parse_feed(feed(date='yesterday'), FEED) == []
    with pytest.raises(ValueError):
        parse_feed(b'<!DOCTYPE rss [<!ENTITY x "test">]><rss/>', FEED)
    with pytest.raises(ValueError):
        parse_feed(b'x' * (2 * 1024 * 1024 + 1), FEED)


def test_filters_unrelated_regional_pr_but_keeps_local_and_national_policy():
    assert relevant_result({'title': 'New era for Liverpool buses', 'description': 'Liverpool gains local control'}) is False
    assert relevant_result({'title': 'First Bus opens Glasgow depot', 'source_url': FEED}) is False
    assert relevant_result({'title': 'Bristol gets new buses', 'source_url': FEED}) is True
    assert relevant_result({'title': 'New bus fare cap across England'}) is True


def test_discovery_survives_one_failed_source_and_prefers_local(monkeypatch):
    monkeypatch.setattr(discovery, 'fetch_search', lambda: {'results': [{
        'title': 'England bus funding', 'public_timestamp': '2026-09-15T10:00:00Z'}]})
    def fetch(url):
        if url == FEED:
            return parse_feed(feed(), FEED)
        raise OSError('source temporarily unavailable')
    monkeypatch.setattr(discovery, 'fetch_feed', fetch)
    assert discovery.discover_sources()['results'][0]['title'] == 'Bristol buses'
    monkeypatch.setattr(discovery, 'fetch_search', lambda: (_ for _ in ()).throw(discovery.NewsDiscoveryError('offline')))
    monkeypatch.setattr(discovery, 'fetch_feed', lambda url: (_ for _ in ()).throw(OSError('offline')))
    with pytest.raises(discovery.NewsDiscoveryError, match='all official'):
        discovery.discover_sources()
