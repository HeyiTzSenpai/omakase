"""Tests for nyaa.si RSS search + best-match heuristic."""

from __future__ import annotations

from datetime import datetime, timezone

from omakase.plus.nyaa import NyaaTorrent, _is_likely_batch, _parse_size, find_best

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:nyaa="https://nyaa.si/xmlns/nyaa" xmlns:torrent="http://xmlns.ezrss.it/0.1/">
<channel>
<title>Nyaa - &#34;Dungeon Meshi&#34;</title>
<item>
<title>[SubsPlease] Dungeon Meshi - 01 (1080p) [A8712C3D]</title>
<link>https://nyaa.si/view/12345</link>
<guid>magnet:?xt=urn:btih:A8712C3D&amp;dn=Dungeon+Meshi+01</guid>
<pubDate>Sun, 28 May 2026 12:00:00 -0000</pubDate>
<nyaa:seeders>150</nyaa:seeders>
<nyaa:leechers>20</nyaa:leechers>
<nyaa:downloads>500</nyaa:downloads>
<nyaa:size>1.4 GiB</nyaa:size>
<nyaa:trusted>Yes</nyaa:trusted>
<nyaa:category>Anime - English-translated</nyaa:category>
</item>
<item>
<title>Dungeon Meshi - 01-24 Complete 1080p Batch</title>
<link>https://nyaa.si/view/67890</link>
<guid>magnet:?xt=urn:btih:BATCH9999&amp;dn=Dungeon+Meshi+Batch</guid>
<pubDate>Sun, 28 May 2026 10:00:00 -0000</pubDate>
<nyaa:seeders>300</nyaa:seeders>
<nyaa:leechers>5</nyaa:leechers>
<nyaa:downloads>1000</nyaa:downloads>
<nyaa:size>25.0 GiB</nyaa:size>
<nyaa:trusted>No</nyaa:trusted>
<nyaa:category>Anime - English-translated</nyaa:category>
</item>
<item>
<title>[HorribleSubs] Dungeon Meshi - 01 (720p)</title>
<link>https://nyaa.si/view/11111</link>
<guid>magnet:?xt=urn:btih:HS720P01&amp;dn=Dungeon+Meshi+720p</guid>
<pubDate>Sat, 27 May 2026 08:00:00 -0000</pubDate>
<nyaa:seeders>0</nyaa:seeders>
<nyaa:leechers>3</nyaa:leechers>
<nyaa:downloads>50</nyaa:downloads>
<nyaa:size>350.0 MiB</nyaa:size>
<nyaa:trusted>No</nyaa:trusted>
<nyaa:category>Anime - English-translated</nyaa:category>
</item>
</channel>
</rss>"""


class TestNyaaTorrent:
    def test_from_xml_fields(self):
        t = NyaaTorrent(
            title="[SubsPlease] Dungeon Meshi - 01",
            magnet="magnet:?xt=urn:btih:ABC",
            seeders=100,
            leechers=10,
            size_bytes=1503238528,
            size_display="1.4 GiB",
            pub_date=datetime.now(timezone.utc),
            is_trusted=True,
            is_batch=False,
        )
        assert t.seeders == 100
        assert t.is_trusted is True
        assert t.is_batch is False


class TestParseSize:
    def test_gib(self):
        b, _ = _parse_size("1.4 GiB")
        assert b == int(1.4 * 1024**3)

    def test_mib(self):
        b, _ = _parse_size("350.0 MiB")
        assert b == int(350.0 * 1024**2)

    def test_kib(self):
        b, _ = _parse_size("512 KiB")
        assert b == 512 * 1024

    def test_empty(self):
        b, _ = _parse_size("")
        assert b == 0


class TestIsLikelyBatch:
    def test_batch_keyword(self):
        assert _is_likely_batch("Dungeon Meshi Complete 1080p") is True

    def test_all_episodes(self):
        assert _is_likely_batch("One Piece all episodes 1-1000") is True

    def test_episode_range(self):
        assert _is_likely_batch("Dungeon Meshi - 01-24 1080p") is True

    def test_season_keyword(self):
        assert _is_likely_batch("Attack on Titan Season 1 Complete") is True

    def test_single_episode_is_not_batch(self):
        assert _is_likely_batch("[SubsPlease] Dungeon Meshi - 01 (1080p)") is False


class TestFindBest:
    def _make_torrent(
        self,
        title,
        seeders,
        trusted=False,
        batch=False,
        size_bytes=1_000_000_000,
        size_display="1 GiB",
    ):
        return NyaaTorrent(
            title=title,
            magnet=f"magnet:?xt=urn:btih:{title}",
            seeders=seeders,
            leechers=5,
            size_bytes=size_bytes,
            size_display=size_display,
            pub_date=datetime.now(timezone.utc),
            is_trusted=trusted,
            is_batch=batch,
        )

    def test_prefers_trusted(self):
        t1 = self._make_torrent("Show 01", 100, trusted=True, batch=False)
        t2 = self._make_torrent("Show 01", 500, trusted=False, batch=False)
        best = find_best([t2, t1])
        assert best is not None
        assert best.is_trusted is True

    def test_default_prefers_good_complete_batch_for_title_level_download(self):
        t1 = self._make_torrent("Show 01", 100, trusted=True, batch=True)
        t2 = self._make_torrent("Show 01", 100, trusted=True, batch=False)
        best = find_best([t1, t2])
        assert best is not None
        assert best.is_batch is True

    def test_can_still_prefer_non_batch_for_episode_level_download(self):
        t1 = self._make_torrent("Show 01-12 Complete", 100, trusted=True, batch=True)
        t2 = self._make_torrent("Show 01", 100, trusted=True, batch=False)
        best = find_best([t1, t2], prefer_no_batch=True)
        assert best is not None
        assert best.is_batch is False

    def test_seeders_breaks_tie(self):
        t1 = self._make_torrent("Show 01", 50, trusted=True, batch=False)
        t2 = self._make_torrent("Show 01", 200, trusted=True, batch=False)
        best = find_best([t1, t2])
        assert best is not None
        assert best.seeders == 200

    def test_min_seeders_filters(self):
        t1 = self._make_torrent("Show 01", 0, trusted=False, batch=False)
        best = find_best([t1], min_seeders=1)
        assert best is None

    def test_empty_list(self):
        assert find_best([]) is None

    def test_disable_trusted_pref(self):
        t1 = self._make_torrent("Show 01", 100, trusted=True, batch=False)
        t2 = self._make_torrent("Show 01", 500, trusted=False, batch=False)
        best = find_best([t2, t1], prefer_trusted=False)
        assert best is not None
        assert best.seeders == 500

    def test_prefers_1080p_webdl_over_high_seed_low_quality_raw(self):
        good = self._make_torrent(
            "[SubsPlease] Frieren - 01-28 Complete [1080p][WEB-DL][AAC]",
            80,
            batch=True,
            size_bytes=28_000_000_000,
            size_display="26.1 GiB",
        )
        low_quality = self._make_torrent(
            "[RandomRaw] Frieren CAM RAW 720p",
            900,
            batch=False,
            size_bytes=500_000_000,
            size_display="476.8 MiB",
        )

        best = find_best([low_quality, good])

        assert best is good

    def test_non_cam_bdrip_beats_2160p_cam_with_more_seeders(self):
        good = self._make_torrent(
            "[Judas] The Anime Movie [BDRip][1080p][x265][10bit]",
            25,
            size_bytes=4_500_000_000,
            size_display="4.2 GiB",
        )
        cam = self._make_torrent(
            "[FastSeeder] The Anime Movie 2160p HDCAM",
            300,
            size_bytes=1_200_000_000,
            size_display="1.1 GiB",
        )

        best = find_best([cam, good])

        assert best is good

    def test_quality_beats_trusted_low_resolution(self):
        low_res_trusted = self._make_torrent(
            "[Trusted] Show - 01 [480p]",
            500,
            trusted=True,
            size_bytes=220_000_000,
            size_display="209.8 MiB",
        )
        high_quality = self._make_torrent(
            "[GoodGroup] Show - 01 [1080p][WEB-DL]",
            40,
            trusted=False,
            size_bytes=1_400_000_000,
            size_display="1.3 GiB",
        )

        best = find_best([low_res_trusted, high_quality])

        assert best is high_quality

    def test_uses_seedable_720p_when_no_better_option_exists(self):
        only_seedable = self._make_torrent("[Group] Show - 01 [720p]", 30)

        best = find_best([only_seedable])

        assert best is only_seedable

    def test_rejects_cam_only_result(self):
        cam = self._make_torrent("[FastSeeder] The Anime Movie 1080p HDCAM", 300)

        best = find_best([cam])

        assert best is None
