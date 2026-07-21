from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from matchbot.db import engine as engine_module
from matchbot.db.engine import create_db_and_tables, get_session
from matchbot.db.models import (
    Match,
    MatchStatus,
    Platform,
    Post,
    PostRole,
    PostStatus,
    PostType,
    Profile,
)
from matchbot.public import router as public_router
from matchbot.server import create_app
from matchbot.settings import get_settings


def _setup_sqlite_db(monkeypatch, tmp_path, name: str) -> None:
    db_path = tmp_path / name
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")
    monkeypatch.setenv("DB_PATH", str(db_path))
    get_settings.cache_clear()
    engine_module._engine = None
    asyncio.run(create_db_and_tables())


def _reset_engine() -> None:
    engine_module._engine = None
    get_settings.cache_clear()


def test_community_page_renders(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_render.db")
    try:
        client = TestClient(create_app(enable_scheduler=False))

        # / community/ now serves the new home page
        response = client.get("/community/")
        assert response.status_code == 200
        assert "Rising Sparks" in response.text
        assert "Find your people" in response.text
        assert "Build the city" in response.text
        assert "Looking for a camp or project" in response.text
        assert "Gear" in response.text
        assert "Camp Connections" in response.text
        assert "Infrastructure Exchange" in response.text
        assert "A quick read on what people are posting right now." in response.text
        assert 'href="/community/seekers"' in response.text
        assert 'href="/community/camps"' in response.text
        assert 'href="/community/gear?view=needs#need-panel"' in response.text
        assert 'href="/community/gear?view=offers#offer-panel"' in response.text
        assert "/media/rising-sparks-logo.png" in response.text
        assert 'rel="icon"' in response.text
        assert "/favicon.ico" in response.text
        assert "/site.webmanifest" in response.text
        assert "We learned a lot from Matchbot" in response.text
        assert "we’re sunsetting it—at least in its current format" in response.text
        assert (
            'href="https://docs.google.com/spreadsheets/d/1pR6kqBTIYLal2GNN6mJ_ueGygaWLo6R2HB5wQhUlBDw/edit?gid=1307777311#gid=1307777311"'
            in response.text
        )
        assert 'target="_blank" rel="noopener noreferrer"' in response.text

        gear = client.get("/community/gear")
        assert gear.status_code == 200
        assert 'id="need-panel"' in gear.text
        assert 'id="offer-panel"' in gear.text
        assert "Infra Needs" in gear.text
        assert "Infra Offers" in gear.text

        # Full dashboard content lives at /community/transparency
        trans = client.get("/community/transparency")
        assert trans.status_code == 200
        assert "Rising Sparks" in trans.text
        assert "Potential Matches" in trans.text
        assert "Introductions Sent" in trans.text
        assert "Live Activity" in trans.text
        assert "Skills" in trans.text
        assert "Vibes" in trans.text
        assert "Infrastructure Exchange Snapshot" in trans.text
        assert "Matched Drill-Down" in trans.text
        assert "Metrics Summary" in trans.text
        assert 'rel="icon"' in trans.text
        assert ('property="og:title" content="Open Stats') in trans.text
        assert 'name="twitter:card" content="summary_large_image"' in trans.text
    finally:
        _reset_engine()


def test_root_page_renders_dashboard(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_root_render.db")
    try:
        client = TestClient(create_app(enable_scheduler=False))
        # / now serves the community home page
        response = client.get("/")
        assert response.status_code == 200
        assert "Rising Sparks" in response.text
        assert "Find your people" in response.text
        assert "Build the city" in response.text
        assert 'rel="icon"' in response.text
        assert "/favicon.ico" in response.text
    finally:
        _reset_engine()


def test_community_home_includes_share_meta_tags(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_share_meta.db")
    try:
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get(
            "/community/",
            headers={"host": "community.rising-sparks.org"},
        )
        assert response.status_code == 200
        assert 'property="og:title" content="Rising Sparks — Find Your Community"' in response.text
        assert (
            'property="og:url" content="http://community.rising-sparks.org/community/"'
            in response.text
        )
        assert 'property="og:image"' in response.text
        assert 'name="twitter:card" content="summary_large_image"' in response.text
        assert (
            'rel="canonical" href="http://community.rising-sparks.org/community/"' in response.text
        )
    finally:
        _reset_engine()


def test_community_data_zero_state(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_zero_state.db")
    try:
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/community/data")
        assert response.status_code == 200
        payload = response.json()

        assert payload["summary"]["total_ingested"] == 0
        assert payload["summary"]["indexed"] == 0
        assert payload["summary"]["proposed_matches"] == 0
        assert payload["summary"]["intros_sent"] == 0
        assert payload["backlog"]["needs_review_count"] == 0
        assert payload["backlog"]["soft_matches_count"] == 0
        assert payload["backlog"]["oldest_needs_review_age_hours"] is None

        assert payload["key_metrics"]["active_camps"] == 0
        assert payload["key_metrics"]["active_seekers"] == 0
        assert payload["key_metrics"]["active_infra_seeking"] == 0
        assert payload["key_metrics"]["active_infra_offering"] == 0
        assert payload["key_metrics"]["soft_matches_total"] == 0
        assert payload["key_metrics"]["soft_matches_7d"] == 0
        assert payload["key_metrics"]["intro_to_conversation_rate"] == 0
        assert payload["key_metrics"]["conversation_to_onboarding_rate"] == 0
        assert payload["live_feed"] == []
        assert payload["demand"]["top_contribution_types"] == []
        assert payload["demand"]["top_vibes"] == []
        assert payload["demand"]["most_sought_skills"] == []
        assert payload["demand"]["most_sought_vibes"] == []
        assert payload["demand"]["infra_exchange"] == []
    finally:
        _reset_engine()


def test_favicon_route_serves_ico(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_favicon.db")
    try:
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/favicon.ico")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/x-icon")
    finally:
        _reset_engine()


def test_webmanifest_route_serves_manifest(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_manifest.db")
    try:
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/site.webmanifest")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/manifest+json")
        assert '"icons"' in response.text
    finally:
        _reset_engine()


def test_manifest_icon_routes_serve_pngs(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_manifest_icons.db")
    try:
        client = TestClient(create_app(enable_scheduler=False))

        response_192 = client.get("/android-chrome-192x192.png")
        assert response_192.status_code == 200
        assert response_192.headers["content-type"].startswith("image/png")

        response_512 = client.get("/android-chrome-512x512.png")
        assert response_512.status_code == 200
        assert response_512.headers["content-type"].startswith("image/png")
    finally:
        _reset_engine()


def test_brand_logo_route_serves_png(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_logo.db")
    try:
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/media/rising-sparks-logo.png")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
    finally:
        _reset_engine()


def test_google_analytics_snippet_renders_when_configured(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_ga.db")
    try:
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/community/")
        assert response.status_code == 200
        assert "https://www.googletagmanager.com/gtag/js?id=G-4QW073D00W" in response.text
        assert "gtag('config', 'G-4QW073D00W');" in response.text
    finally:
        _reset_engine()


def test_community_rest_api_endpoints_exist_and_are_nonbreaking(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_api_endpoints.db")
    try:
        client = TestClient(create_app(enable_scheduler=False))

        legacy = client.get("/community/data")
        assert legacy.status_code == 200
        legacy_payload = legacy.json()

        overview = client.get("/community/api/overview")
        metrics = client.get("/community/api/metrics")
        pipeline = client.get("/community/api/pipeline")
        platforms = client.get("/community/api/platforms")
        feed = client.get("/community/api/feed")
        demand = client.get("/community/api/demand")
        matches = client.get("/community/api/matches")
        stories = client.get("/community/api/stories")
        discovery = client.get("/community/api/discovery")

        assert overview.status_code == 200
        assert metrics.status_code == 200
        assert pipeline.status_code == 200
        assert platforms.status_code == 200
        assert feed.status_code == 200
        assert demand.status_code == 200
        assert matches.status_code == 200
        assert stories.status_code == 200
        assert discovery.status_code == 200
        assert "items" in discovery.json()
        assert "tab" in discovery.json()

        assert overview.json()["summary"] == legacy_payload["summary"]
        assert metrics.json()["summary"] == legacy_payload["summary"]
        assert metrics.json()["key_metrics"] == legacy_payload["key_metrics"]
        assert pipeline.json()["pipeline"] == legacy_payload["pipeline"]
        assert platforms.json()["platform_breakdown"] == legacy_payload["platform_breakdown"]
        assert feed.json()["live_feed"] == legacy_payload["live_feed"]
        assert demand.json()["demand"] == legacy_payload["demand"]
        assert stories.json()["stories"] == legacy_payload["stories"]
    finally:
        _reset_engine()


def test_community_rest_api_endpoints_share_cached_payload(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_api_cache.db")
    calls = {"count": 0}

    async def _payload(_session):
        calls["count"] += 1
        return {
            "summary": {"total_ingested": 1},
            "weekly": {"ingested_7d": 1},
            "backlog": {
                "needs_review_count": 0,
                "soft_matches_count": 0,
                "oldest_needs_review_age_hours": None,
            },
            "key_metrics": {"active_camps": 0, "soft_matches_total": 0, "soft_matches_7d": 0},
            "pipeline": [{"stage": "Seen", "count": 1}],
            "platform_breakdown": [{"platform": "reddit", "count": 1, "pct": 100.0}],
            "live_feed": [],
            "demand": {
                "top_contribution_types": [],
                "top_vibes": [],
                "most_sought_skills": [],
                "most_sought_vibes": [],
            },
            "stories": [{"title": "cached"}],
            "updated_at": "cached-ts",
        }

    try:
        monkeypatch.setattr(public_router, "build_public_community_payload", _payload)
        client = TestClient(create_app(enable_scheduler=False))

        overview = client.get("/community/api/overview")
        stories = client.get("/community/api/stories")
        metrics = client.get("/community/api/metrics")

        assert overview.status_code == 200
        assert stories.status_code == 200
        assert metrics.status_code == 200
        assert stories.json()["stories"] == [{"title": "cached"}]
        assert calls["count"] == 1
    finally:
        _reset_engine()


def test_community_matches_api_filters_and_sanitizes(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_matches_api.db")

    now = datetime.now(UTC).replace(tzinfo=None)
    old_match_time = now - timedelta(days=45)
    recent_match_time = now - timedelta(days=2)

    async def _seed() -> None:
        async with get_session() as session:
            seeker_recent = Post(
                platform=Platform.REDDIT,
                platform_post_id="seek_recent",
                platform_author_id="s_recent",
                source_url="https://reddit.com/seek_recent",
                title="Need builders",
                raw_text="Email me at recent@example.com u/RecentUser",
                role=PostRole.SEEKER,
                status=PostStatus.INDEXED,
                contribution_types="build|art",
                detected_at=now - timedelta(days=3),
            )
            camp_recent = Post(
                platform=Platform.DISCORD,
                platform_post_id="camp_recent",
                platform_author_id="c_recent",
                source_url="https://discord.com/channels/x/y",
                title="Camp needs build",
                raw_text="@CampRecent needs build team",
                role=PostRole.CAMP,
                status=PostStatus.INDEXED,
                contribution_types="build|kitchen_food",
                detected_at=now - timedelta(days=3),
            )
            seeker_old = Post(
                platform=Platform.FACEBOOK,
                platform_post_id="seek_old",
                platform_author_id="s_old",
                source_url="https://facebook.com/groups/z/posts/1",
                title="Old seeker",
                raw_text="Old text",
                role=PostRole.SEEKER,
                status=PostStatus.INDEXED,
                contribution_types="art",
                detected_at=now - timedelta(days=50),
            )
            camp_old = Post(
                platform=Platform.REDDIT,
                platform_post_id="camp_old",
                platform_author_id="c_old",
                source_url="https://reddit.com/camp_old",
                title="Old camp",
                raw_text="Old camp text",
                role=PostRole.CAMP,
                status=PostStatus.INDEXED,
                contribution_types="sound_lighting",
                detected_at=now - timedelta(days=50),
            )

            session.add(seeker_recent)
            session.add(camp_recent)
            session.add(seeker_old)
            session.add(camp_old)
            await session.commit()
            await session.refresh(seeker_recent)
            await session.refresh(camp_recent)
            await session.refresh(seeker_old)
            await session.refresh(camp_old)

            session.add(
                Match(
                    seeker_post_id=seeker_recent.id,
                    camp_post_id=camp_recent.id,
                    status=MatchStatus.APPROVED,
                    score=0.91,
                    confidence=0.82,
                    created_at=recent_match_time,
                )
            )
            session.add(
                Match(
                    seeker_post_id=seeker_old.id,
                    camp_post_id=camp_old.id,
                    status=MatchStatus.DECLINED,
                    score=0.33,
                    confidence=0.2,
                    created_at=old_match_time,
                )
            )
            await session.commit()

    try:
        asyncio.run(_seed())
        client = TestClient(create_app(enable_scheduler=False))

        filtered = client.get("/community/api/matches?status=approved&days=30&limit=50")
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert filtered_payload["summary"]["total"] == 1
        assert filtered_payload["summary"]["by_status"]["approved"] == 1
        assert len(filtered_payload["matched"]) == 1
        row = filtered_payload["matched"][0]
        assert row["status"] == MatchStatus.APPROVED
        assert row["shared_signals"] == "build"
        assert row["match_reason"] == "Both mention build as contribution styles."
        assert row["seeker_source_url"] == "https://reddit.com/seek_recent"
        assert row["camp_source_url"] == "https://discord.com/channels/x/y"
        assert "recent@example.com" not in row["seeker_summary"]
        assert "u/RecentUser" not in row["seeker_summary"]

        wide = client.get("/community/api/matches?days=all&limit=2")
        assert wide.status_code == 200
        wide_payload = wide.json()
        assert wide_payload["summary"]["total"] == 2
        assert len(wide_payload["matched"]) == 2
        older = wide_payload["matched"][1]
        assert older["shared_signals"] == "none"
        assert older["match_reason"].startswith("No shared contribution tags were extracted.")
    finally:
        _reset_engine()


def test_community_data_redacts_story_and_feed_identifiers(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_redaction.db")

    async def _seed() -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
        async with get_session() as session:
            seeker = Post(
                platform=Platform.REDDIT,
                platform_post_id="seek_1",
                platform_author_id="t2_real_user",
                author_display_name="SeekerReal",
                source_url="https://reddit.com/r/BurningMan/comments/seek_1/",
                source_community="BurningMan",
                title="Need a camp",
                raw_text=(
                    "I am @SeekerReal and u/SeekerReal. Email me at real@example.com "
                    "or visit https://example.com/help"
                ),
                role=PostRole.SEEKER,
                status=PostStatus.INDEXED,
                detected_at=now - timedelta(hours=2),
                contribution_types="build|art",
                vibes="inclusive|build_focused",
            )
            camp = Post(
                platform=Platform.REDDIT,
                platform_post_id="camp_1",
                platform_author_id="t2_camp_user",
                author_display_name="CampReal",
                source_url="https://reddit.com/r/BurningMan/comments/camp_1/",
                source_community="BurningMan",
                title="Camp offering spots",
                raw_text="We need builders and artists.",
                role=PostRole.CAMP,
                status=PostStatus.INDEXED,
                detected_at=now - timedelta(hours=1),
                contribution_types="build|kitchen_food",
                vibes="inclusive",
            )
            needs_review = Post(
                platform=Platform.DISCORD,
                platform_post_id="review_1",
                platform_author_id="discord_user",
                author_display_name="DiscordUser",
                source_url="",
                source_community="discord",
                title="Pending review",
                raw_text="Needs moderation",
                role=PostRole.SEEKER,
                status=PostStatus.NEEDS_REVIEW,
                detected_at=now - timedelta(hours=3),
                contribution_types="art",
                vibes="cozy",
            )
            soft_match = Post(
                platform=Platform.REDDIT,
                platform_post_id="soft_1",
                platform_author_id="soft_user",
                author_display_name="SoftUser",
                source_url="https://reddit.com/r/BurningMan/comments/soft_1/",
                source_community="BurningMan",
                title="Any camp recs?",
                raw_text="Any camp recs for someone into fire spinning?",
                role=PostRole.SEEKER,
                status=PostStatus.NEEDS_REVIEW,
                detected_at=now - timedelta(hours=4),
                extraction_method="keyword_soft",
            )
            raw_post = Post(
                platform=Platform.FACEBOOK,
                platform_post_id="raw_1",
                platform_author_id="fb_user",
                author_display_name="RawUser",
                source_url="",
                source_community="facebook",
                title="Raw post",
                raw_text="Should not show in feed",
                role=PostRole.SEEKER,
                status=PostStatus.RAW,
                detected_at=now - timedelta(minutes=20),
            )
            camp_profile = Profile(
                role=PostRole.CAMP,
                platform=Platform.REDDIT,
                platform_author_id="camp_profile_1",
                is_active=True,
            )
            seeker_profile = Profile(
                role=PostRole.SEEKER,
                platform=Platform.DISCORD,
                platform_author_id="seeker_profile_1",
                is_active=True,
            )
            inactive_profile = Profile(
                role=PostRole.CAMP,
                platform=Platform.REDDIT,
                platform_author_id="camp_profile_2",
                is_active=False,
            )

            session.add(seeker)
            session.add(camp)
            session.add(needs_review)
            session.add(soft_match)
            session.add(raw_post)
            session.add(camp_profile)
            session.add(seeker_profile)
            session.add(inactive_profile)
            await session.commit()
            await session.refresh(seeker)
            await session.refresh(camp)

            match = Match(
                seeker_post_id=seeker.id,
                camp_post_id=camp.id,
                status=MatchStatus.INTRO_SENT,
                score=0.84,
                confidence=0.78,
                created_at=now - timedelta(hours=1),
                intro_sent_at=now - timedelta(minutes=30),
            )
            session.add(match)
            await session.commit()

    try:
        asyncio.run(_seed())
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/community/data")
        assert response.status_code == 200
        payload = response.json()

        assert payload["summary"]["total_ingested"] == 5
        assert payload["summary"]["indexed"] == 2
        assert payload["summary"]["proposed_matches"] == 1
        assert payload["summary"]["intros_sent"] == 1
        assert payload["backlog"]["needs_review_count"] == 2
        assert payload["backlog"]["soft_matches_count"] == 1

        key_metrics = payload["key_metrics"]
        assert key_metrics["active_camps"] == 1
        assert key_metrics["active_seekers"] == 1
        assert key_metrics["soft_matches_total"] == 1
        assert key_metrics["soft_matches_7d"] == 1
        assert key_metrics["match_attempts_total"] == 1
        assert key_metrics["intro_sent_total"] == 1
        assert key_metrics["conversation_started_total"] == 0
        assert key_metrics["onboarded_total"] == 0

        demand = payload["demand"]
        assert demand["top_contribution_types"][0]["name"] == "art"
        assert demand["top_contribution_types"][0]["count"] == 2
        assert demand["most_sought_skills"][0]["name"] == "kitchen_food"
        assert demand["most_sought_skills"][0]["demand_count"] == 1
        assert demand["most_sought_skills"][0]["supply_count"] == 0
        assert demand["most_sought_vibes"][0]["name"] == "inclusive"
        assert demand["most_sought_vibes"][0]["demand_count"] == 1
        assert demand["most_sought_vibes"][0]["supply_count"] == 1

        feed = payload["live_feed"]
        assert len(feed) >= 2
        assert all(
            feed[i]["occurred_at"] >= feed[i + 1]["occurred_at"] for i in range(len(feed) - 1)
        )
        assert all(event["event_type"] != "post_raw" for event in feed)
        assert all(event["event_type"] != "post_indexed" for event in feed)
        assert any(event["event_type"] == "post_needs_review" for event in feed)

        feed_blob = " ".join((event["summary"] or "") for event in feed)
        assert "real@example.com" not in feed_blob
        assert "u/SeekerReal" not in feed_blob
        assert "@SeekerReal" not in feed_blob
        assert "https://example.com/help" not in feed_blob

        story_blob = " ".join(
            [
                payload["stories"][0]["problem"],
                payload["stories"][0]["intervention"],
                payload["stories"][0]["outcome"],
                payload["stories"][0]["confidence_note"],
            ]
        )
        assert "real@example.com" not in story_blob
        assert "u/SeekerReal" not in story_blob
        assert "@SeekerReal" not in story_blob
        assert "https://example.com/help" not in story_blob
        assert "[contact]" in story_blob or "[link]" in story_blob
    finally:
        _reset_engine()


def test_community_platform_breakdown_labels_intake_form_as_submission_form(
    monkeypatch, tmp_path
) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_submission_form_platforms.db")

    async def _seed() -> None:
        async with get_session() as session:
            session.add(
                Post(
                    platform=Platform.MANUAL,
                    platform_post_id="intake_1",
                    platform_author_id="form_user",
                    source_community="intake_form",
                    title="Form submission",
                    raw_text="Submitted through the intake form",
                    status=PostStatus.RAW,
                )
            )
            session.add(
                Post(
                    platform=Platform.REDDIT,
                    platform_post_id="reddit_1",
                    platform_author_id="reddit_user",
                    source_community="BurningMan",
                    title="Reddit post",
                    raw_text="Scraped from reddit",
                    status=PostStatus.RAW,
                )
            )
            await session.commit()

    try:
        asyncio.run(_seed())
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/community/api/platforms")
        assert response.status_code == 200

        rows = response.json()["platform_breakdown"]
        assert {"platform": "submission form", "count": 1, "pct": 50.0} in rows
        assert {"platform": "reddit", "count": 1, "pct": 50.0} in rows
        assert all(row["platform"] != "manual" for row in rows)
    finally:
        _reset_engine()


def test_live_feed_excludes_post_indexed_events(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_no_post_indexed_feed_events.db")

    now = datetime.now(UTC).replace(tzinfo=None)

    async def _seed() -> None:
        async with get_session() as session:
            seeker = Post(
                platform=Platform.REDDIT,
                platform_post_id="seek_no_feed",
                platform_author_id="t2_seek_no_feed",
                title="Need build help",
                raw_text="Looking for help",
                role=PostRole.SEEKER,
                status=PostStatus.INDEXED,
                source_created_at=now - timedelta(days=2),
                detected_at=now - timedelta(days=1),
            )
            camp = Post(
                platform=Platform.DISCORD,
                platform_post_id="camp_no_feed",
                platform_author_id="discord_camp_no_feed",
                title="Camp seeking builders",
                raw_text="We have open spots",
                role=PostRole.CAMP,
                status=PostStatus.NEEDS_REVIEW,
                detected_at=now - timedelta(hours=12),
            )
            session.add(seeker)
            session.add(camp)
            await session.commit()
            await session.refresh(seeker)
            await session.refresh(camp)

            session.add(
                Match(
                    seeker_post_id=seeker.id,
                    camp_post_id=camp.id,
                    status=MatchStatus.APPROVED,
                    score=0.76,
                    confidence=0.65,
                    created_at=now - timedelta(hours=2),
                )
            )
            await session.commit()

    try:
        asyncio.run(_seed())
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/community/data")
        assert response.status_code == 200
        payload = response.json()

        feed = payload["live_feed"]
        assert len(feed) == 2
        assert any(item["event_type"] == "post_needs_review" for item in feed)
        assert any(item["event_type"] == "match_approved" for item in feed)
        assert all(item["event_type"] != "post_indexed" for item in feed)
    finally:
        _reset_engine()


def test_live_feed_excludes_old_source_posts_imported_recently(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_live_feed_old_source_post.db")

    now = datetime.now(UTC).replace(tzinfo=None)

    async def _seed() -> None:
        async with get_session() as session:
            old_source_post = Post(
                platform=Platform.FACEBOOK,
                platform_post_id="fb_old_source_new_import",
                platform_author_id="fb_old_source",
                title="Older Facebook post imported recently",
                raw_text="Imported now, but the original source post is weeks old.",
                role=PostRole.CAMP,
                status=PostStatus.NEEDS_REVIEW,
                source_created_at=now - timedelta(days=21),
                detected_at=now - timedelta(hours=11),
            )
            recent_needs_review = Post(
                platform=Platform.REDDIT,
                platform_post_id="reddit_recent_review",
                platform_author_id="reddit_recent",
                title="Recent post pending review",
                raw_text="This one should stay in the live feed.",
                role=PostRole.SEEKER,
                status=PostStatus.NEEDS_REVIEW,
                source_created_at=now - timedelta(days=1),
                detected_at=now - timedelta(hours=20),
            )
            session.add_all([old_source_post, recent_needs_review])
            await session.commit()

    try:
        asyncio.run(_seed())
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/community/data")
        assert response.status_code == 200
        payload = response.json()

        feed = payload["live_feed"]
        post_events = [item for item in feed if item["event_type"] == "post_needs_review"]
        assert len(post_events) == 1
        assert post_events[0]["summary"] == "This one should stay in the live feed."
        assert (
            post_events[0]["occurred_at"]
            == (now - timedelta(days=1)).replace(tzinfo=UTC).isoformat()
        )
    finally:
        _reset_engine()


def test_community_order_book_for_skills_and_vibes(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_order_book.db")

    now = datetime.now(UTC).replace(tzinfo=None)

    async def _seed() -> None:
        async with get_session() as session:
            session.add_all(
                [
                    Post(
                        platform=Platform.REDDIT,
                        platform_post_id="camp_1",
                        platform_author_id="camp_1",
                        title="Camp needs kitchen_food and build",
                        raw_text="Seeking kitchen_food and build folks",
                        role=PostRole.CAMP,
                        post_type=PostType.MENTORSHIP,
                        status=PostStatus.INDEXED,
                        contribution_types="kitchen_food|build",
                        vibes="inclusive|art",
                        detected_at=now - timedelta(days=1),
                    ),
                    Post(
                        platform=Platform.DISCORD,
                        platform_post_id="camp_2",
                        platform_author_id="camp_2",
                        title="Camp needs kitchen_food and art",
                        raw_text="Seeking kitchen_food and art folks",
                        role=PostRole.CAMP,
                        post_type=PostType.MENTORSHIP,
                        status=PostStatus.INDEXED,
                        contribution_types="kitchen_food|art",
                        vibes="inclusive|music",
                        detected_at=now - timedelta(days=1),
                    ),
                    Post(
                        platform=Platform.REDDIT,
                        platform_post_id="seeker_1",
                        platform_author_id="seeker_1",
                        title="I can build",
                        raw_text="Offering build and art help",
                        role=PostRole.SEEKER,
                        post_type=PostType.MENTORSHIP,
                        status=PostStatus.INDEXED,
                        contribution_types="build|art",
                        vibes="inclusive|music",
                        detected_at=now - timedelta(days=1),
                    ),
                    Post(
                        platform=Platform.FACEBOOK,
                        platform_post_id="seeker_2",
                        platform_author_id="seeker_2",
                        title="I can teach",
                        raw_text="Offering teaching support",
                        role=PostRole.SEEKER,
                        post_type=PostType.MENTORSHIP,
                        status=PostStatus.NEEDS_REVIEW,
                        contribution_types="teaching",
                        vibes="sober",
                        detected_at=now - timedelta(hours=12),
                    ),
                    Post(
                        platform=Platform.REDDIT,
                        platform_post_id="infra_camp",
                        platform_author_id="infra_camp",
                        title="Need generator",
                        raw_text="Seeking power gear",
                        role=PostRole.CAMP,
                        post_type=PostType.INFRASTRUCTURE,
                        status=PostStatus.INDEXED,
                        infra_role="seeking",
                        infra_categories="power",
                        contribution_types="camp_admin",
                        vibes="party",
                        detected_at=now - timedelta(hours=8),
                    ),
                ]
            )
            await session.commit()

    try:
        asyncio.run(_seed())
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/community/data")
        assert response.status_code == 200
        demand = response.json()["demand"]

        skills = demand["most_sought_skills"]
        assert [row["name"] for row in skills[:3]] == ["kitchen_food", "art", "build"]
        assert skills[0]["demand_count"] == 2
        assert skills[0]["supply_count"] == 0
        assert skills[0]["net_gap"] == 2
        assert skills[0]["fill_ratio"] == 0
        assert skills[1]["demand_count"] == 1
        assert skills[1]["supply_count"] == 1
        assert skills[1]["fill_ratio"] == 1.0

        vibes = demand["most_sought_vibes"]
        assert [row["name"] for row in vibes[:3]] == ["inclusive", "art", "music"]
        assert vibes[0]["demand_count"] == 2
        assert vibes[0]["supply_count"] == 1
        assert vibes[0]["net_gap"] == 1
        assert vibes[0]["fill_ratio"] == 0.5
        assert all(row["name"] != "party" for row in vibes)

        infra = demand["infra_exchange"]
        assert infra[0]["name"] == "power"
        assert infra[0]["demand_count"] == 1
        assert infra[0]["supply_count"] == 0
    finally:
        _reset_engine()


def test_community_data_retries_on_disconnect(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_retry_disconnect.db")
    calls = {"count": 0}

    async def _flaky_payload(_session):
        calls["count"] += 1
        if calls["count"] == 1:
            raise DBAPIError(
                statement="SELECT 1",
                params={},
                orig=Exception(
                    "ConnectionDoesNotExistError: connection was closed in the middle of operation"
                ),
            )
        return {"summary": {}, "weekly": {}, "backlog": {}, "updated_at": "ok"}

    try:
        monkeypatch.setattr(public_router, "build_public_community_payload", _flaky_payload)
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/community/data")
        assert response.status_code == 200
        assert response.json()["updated_at"] == "ok"
        assert calls["count"] == 2
    finally:
        _reset_engine()


def test_community_data_infers_missing_infra_roles(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_infra_role_inference.db")
    now = datetime.now(UTC).replace(tzinfo=None)

    async def _seed() -> None:
        async with get_session() as session:
            session.add_all(
                [
                    Post(
                        platform=Platform.REDDIT,
                        platform_post_id="infra_need",
                        platform_author_id="infra_need",
                        title="Need a generator for playa",
                        raw_text="Looking to borrow a generator and some tools.",
                        post_type=PostType.INFRASTRUCTURE,
                        status=PostStatus.INDEXED,
                        infra_categories="power|tools",
                        detected_at=now - timedelta(hours=3),
                    ),
                    Post(
                        platform=Platform.DISCORD,
                        platform_post_id="infra_offer",
                        platform_author_id="infra_offer",
                        title="Offering shade hardware",
                        raw_text="We can lend spare shade and tools.",
                        post_type=PostType.INFRASTRUCTURE,
                        status=PostStatus.NEEDS_REVIEW,
                        infra_categories="shade|tools",
                        detected_at=now - timedelta(hours=2),
                    ),
                ]
            )
            await session.commit()

    try:
        asyncio.run(_seed())
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/community/data")
        assert response.status_code == 200
        payload = response.json()

        assert payload["key_metrics"]["active_infra_seeking"] == 1
        assert payload["key_metrics"]["active_infra_offering"] == 1

        infra = payload["demand"]["infra_exchange"]
        assert [row["name"] for row in infra[:3]] == ["power", "tools"]
        assert infra[0]["demand_count"] == 1
        assert infra[0]["supply_count"] == 0
        assert infra[1]["demand_count"] == 1
        assert infra[1]["supply_count"] == 1
    finally:
        _reset_engine()


def test_community_page_has_discovery_section(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_discovery_page.db")
    try:
        client = TestClient(create_app(enable_scheduler=False))
        # The dashboard content (including Community Pool) lives at /community/transparency
        response = client.get("/community/transparency")
        assert response.status_code == 200
        assert "Community Pool" in response.text
        assert "Camps &amp; Projects" in response.text
        assert "Builders &amp; Seekers" in response.text
        assert "Infra Needs" in response.text
        assert "Infra Offers" in response.text
        assert "discovery-grid" in response.text
    finally:
        _reset_engine()


def test_community_discovery_api_empty(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_discovery_empty.db")
    try:
        client = TestClient(create_app(enable_scheduler=False))
        for tab in ("mentorship_camps", "mentorship_seekers", "infra_seeking", "infra_offering"):
            resp = client.get(f"/community/api/discovery?tab={tab}")
            assert resp.status_code == 200
            payload = resp.json()
            assert payload["tab"] == tab
            assert payload["items"] == []
            assert payload["count"] == 0
    finally:
        _reset_engine()


def test_community_discovery_api_unknown_tab_falls_back(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_discovery_fallback.db")
    try:
        client = TestClient(create_app(enable_scheduler=False))
        resp = client.get("/community/api/discovery?tab=bogus")
        assert resp.status_code == 200
        assert resp.json()["tab"] == "mentorship_camps"
    finally:
        _reset_engine()


def test_community_discovery_api_returns_camps_and_seekers(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_discovery_data.db")

    now = datetime.now(UTC).replace(tzinfo=None)

    async def _seed() -> None:
        async with get_session() as session:
            session.add_all(
                [
                    Post(
                        platform=Platform.REDDIT,
                        platform_post_id="disc_camp_1",
                        platform_author_id="camp_a",
                        source_url="https://reddit.com/camp_a",
                        title="Camp Solaris needs builders",
                        raw_text="Camp Solaris has open spots for builders and artists.",
                        role=PostRole.CAMP,
                        post_type=PostType.MENTORSHIP,
                        status=PostStatus.INDEXED,
                        camp_name="Camp Solaris",
                        vibes="inclusive|art",
                        contribution_types="build|art",
                        detected_at=now - timedelta(hours=5),
                    ),
                    Post(
                        platform=Platform.DISCORD,
                        platform_post_id="disc_seek_1",
                        platform_author_id="seek_a",
                        source_url="",
                        title="Looking to join a camp",
                        raw_text="Experienced builder seeking a creative camp.",
                        role=PostRole.SEEKER,
                        post_type=PostType.MENTORSHIP,
                        status=PostStatus.INDEXED,
                        vibes="inclusive",
                        contribution_types="build",
                        seeker_intent="join_camp",
                        detected_at=now - timedelta(hours=3),
                    ),
                    Post(
                        platform=Platform.REDDIT,
                        platform_post_id="disc_infra_seek",
                        platform_author_id="infra_s",
                        source_url="https://reddit.com/infra_s",
                        title="Need a generator",
                        raw_text="Looking to borrow a generator for the event.",
                        post_type=PostType.INFRASTRUCTURE,
                        status=PostStatus.INDEXED,
                        infra_role="seeking",
                        infra_categories="power",
                        quantity="1 unit",
                        detected_at=now - timedelta(hours=2),
                    ),
                    Post(
                        platform=Platform.REDDIT,
                        platform_post_id="disc_infra_offer",
                        platform_author_id="infra_o",
                        source_url="https://reddit.com/infra_o",
                        title="Offering shade hardware",
                        raw_text="We have spare shade structures to lend.",
                        post_type=PostType.INFRASTRUCTURE,
                        status=PostStatus.INDEXED,
                        infra_role="offering",
                        infra_categories="shade",
                        condition="good",
                        detected_at=now - timedelta(hours=1),
                    ),
                    # Should NOT appear in mentorship_camps (infra type)
                    Post(
                        platform=Platform.REDDIT,
                        platform_post_id="disc_infra_camp",
                        platform_author_id="infra_camp",
                        title="Infra camp should not show",
                        raw_text="Infra camp post",
                        role=PostRole.CAMP,
                        post_type=PostType.INFRASTRUCTURE,
                        status=PostStatus.INDEXED,
                        detected_at=now - timedelta(hours=4),
                    ),
                ]
            )
            await session.commit()

    try:
        asyncio.run(_seed())
        client = TestClient(create_app(enable_scheduler=False))

        camps = client.get("/community/api/discovery?tab=mentorship_camps")
        assert camps.status_code == 200
        camps_data = camps.json()
        assert camps_data["count"] == 1
        assert camps_data["items"][0]["camp_name"] == "Camp Solaris"
        assert "build" in camps_data["items"][0]["skills"]
        assert "inclusive" in camps_data["items"][0]["vibes"]
        assert camps_data["items"][0]["source_url"] == "https://reddit.com/camp_a"

        seekers = client.get("/community/api/discovery?tab=mentorship_seekers")
        assert seekers.status_code == 200
        seekers_data = seekers.json()
        assert seekers_data["count"] == 1
        assert seekers_data["items"][0]["seeker_intent"] == "join_camp"
        assert "build" in seekers_data["items"][0]["skills"]

        infra_need = client.get("/community/api/discovery?tab=infra_seeking")
        assert infra_need.status_code == 200
        infra_need_data = infra_need.json()
        assert infra_need_data["count"] == 1
        assert "power" in infra_need_data["items"][0]["infra_categories"]
        assert infra_need_data["items"][0]["quantity"] == "1 unit"

        infra_offer = client.get("/community/api/discovery?tab=infra_offering")
        assert infra_offer.status_code == 200
        infra_offer_data = infra_offer.json()
        assert infra_offer_data["count"] == 1
        assert "shade" in infra_offer_data["items"][0]["infra_categories"]
        assert infra_offer_data["items"][0]["condition"] == "good"
    finally:
        _reset_engine()


def test_community_discovery_prefers_source_created_at_for_order_and_age(
    monkeypatch, tmp_path
) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_discovery_source_created_at.db")

    now = datetime.now(UTC).replace(tzinfo=None)

    async def _seed() -> None:
        async with get_session() as session:
            session.add_all(
                [
                    Post(
                        platform=Platform.FACEBOOK,
                        platform_post_id="disc_fb_old_source_new_import",
                        platform_author_id="camp_old",
                        title="Older source camp post imported late",
                        raw_text="Posted weeks ago, imported recently.",
                        role=PostRole.CAMP,
                        post_type=PostType.MENTORSHIP,
                        status=PostStatus.INDEXED,
                        camp_name="Pepperland",
                        source_created_at=now - timedelta(days=21),
                        detected_at=now - timedelta(hours=11),
                    ),
                    Post(
                        platform=Platform.REDDIT,
                        platform_post_id="disc_reddit_recent",
                        platform_author_id="camp_recent",
                        title="More recent camp post",
                        raw_text="Posted recently and should sort first.",
                        role=PostRole.CAMP,
                        post_type=PostType.MENTORSHIP,
                        status=PostStatus.INDEXED,
                        camp_name="Recent Camp",
                        source_created_at=now - timedelta(days=2),
                        detected_at=now - timedelta(days=1),
                    ),
                ]
            )
            await session.commit()

    try:
        asyncio.run(_seed())
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/community/api/discovery?tab=mentorship_camps")
        assert response.status_code == 200
        payload = response.json()

        camps = payload["items"]
        assert [item["camp_name"] for item in camps[:2]] == ["Recent Camp", "Pepperland"]
        assert camps[0]["occurred_at"] == (now - timedelta(days=2)).replace(tzinfo=UTC).isoformat()
        assert camps[0]["detected_at"] == (now - timedelta(days=1)).replace(tzinfo=UTC).isoformat()
        assert camps[1]["occurred_at"] == (now - timedelta(days=21)).replace(tzinfo=UTC).isoformat()
        assert (
            camps[1]["detected_at"] == (now - timedelta(hours=11)).replace(tzinfo=UTC).isoformat()
        )
    finally:
        _reset_engine()


def test_community_listings_prefers_source_created_at_for_order_and_age(
    monkeypatch, tmp_path
) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_listings_source_created_at.db")

    now = datetime.now(UTC).replace(tzinfo=None)

    async def _seed() -> None:
        async with get_session() as session:
            session.add_all(
                [
                    Post(
                        platform=Platform.FACEBOOK,
                        platform_post_id="fb_new_import_old_post",
                        platform_author_id="camp_old",
                        title="Older source post imported late",
                        raw_text="Imported today, posted days ago.",
                        role=PostRole.CAMP,
                        post_type=PostType.MENTORSHIP,
                        status=PostStatus.INDEXED,
                        source_created_at=now - timedelta(days=5),
                        detected_at=now - timedelta(minutes=10),
                    ),
                    Post(
                        platform=Platform.REDDIT,
                        platform_post_id="reddit_recent_post",
                        platform_author_id="camp_recent",
                        title="More recent source post",
                        raw_text="Posted more recently, even if imported earlier.",
                        role=PostRole.CAMP,
                        post_type=PostType.MENTORSHIP,
                        status=PostStatus.INDEXED,
                        source_created_at=now - timedelta(days=1),
                        detected_at=now - timedelta(hours=3),
                    ),
                    Post(
                        platform=Platform.DISCORD,
                        platform_post_id="discord_no_source_timestamp",
                        platform_author_id="seeker_detected_only",
                        title="Detected-only seeker post",
                        raw_text="No source timestamp available.",
                        role=PostRole.SEEKER,
                        post_type=PostType.MENTORSHIP,
                        status=PostStatus.INDEXED,
                        detected_at=now - timedelta(hours=2),
                    ),
                ]
            )
            await session.commit()

    try:
        asyncio.run(_seed())
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/community/api/listings")
        assert response.status_code == 200
        payload = response.json()

        camps = payload["camps"]
        assert [item["snippet"] for item in camps[:2]] == [
            "Posted more recently, even if imported earlier.",
            "Imported today, posted days ago.",
        ]
        assert camps[0]["occurred_at"] == (now - timedelta(days=1)).replace(tzinfo=UTC).isoformat()
        assert camps[0]["detected_at"] == (now - timedelta(hours=3)).replace(tzinfo=UTC).isoformat()
        assert camps[1]["occurred_at"] == (now - timedelta(days=5)).replace(tzinfo=UTC).isoformat()
        assert (
            camps[1]["detected_at"] == (now - timedelta(minutes=10)).replace(tzinfo=UTC).isoformat()
        )

        seekers = payload["seekers"]
        assert (
            seekers[0]["occurred_at"] == (now - timedelta(hours=2)).replace(tzinfo=UTC).isoformat()
        )
        assert (
            seekers[0]["detected_at"] == (now - timedelta(hours=2)).replace(tzinfo=UTC).isoformat()
        )
    finally:
        _reset_engine()


def test_community_listings_include_active_infra_needs_review_and_inferred_roles(
    monkeypatch, tmp_path
) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_listings_infra_active.db")

    now = datetime.now(UTC).replace(tzinfo=None)

    async def _seed() -> None:
        async with get_session() as session:
            session.add_all(
                [
                    Post(
                        platform=Platform.REDDIT,
                        platform_post_id="gear_need_inferred",
                        platform_author_id="needer",
                        source_url="https://reddit.com/gear_need_inferred",
                        title="Need a generator and power cables",
                        raw_text="Looking to borrow a generator for camp build.",
                        post_type=PostType.INFRASTRUCTURE,
                        status=PostStatus.INDEXED,
                        infra_categories="power",
                        detected_at=now - timedelta(hours=3),
                    ),
                    Post(
                        platform=Platform.DISCORD,
                        platform_post_id="gear_offer_review",
                        platform_author_id="offerer",
                        source_url="https://discord.com/channels/test/gear_offer_review",
                        title="Offering shade cloth and spare tools",
                        raw_text="We can lend shade hardware and tools this week.",
                        post_type=PostType.INFRASTRUCTURE,
                        status=PostStatus.NEEDS_REVIEW,
                        infra_role="offering",
                        infra_categories="shade|tools",
                        condition="good",
                        detected_at=now - timedelta(hours=2),
                    ),
                    Post(
                        platform=Platform.FACEBOOK,
                        platform_post_id="stale_rv_sale",
                        platform_author_id="rv_seller",
                        source_url="https://facebook.com/stale_rv_sale",
                        title="RV for sale",
                        raw_text="Camp-ready RV sleeps 4. Delivery available.",
                        post_type=PostType.INFRASTRUCTURE,
                        status=PostStatus.INDEXED,
                        infra_role="offering",
                        infra_offer_type="sell",
                        infra_categories="vehicles",
                        detected_at=now - timedelta(hours=1),
                    ),
                ]
            )
            await session.commit()

    try:
        asyncio.run(_seed())
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/community/api/listings")
        assert response.status_code == 200
        payload = response.json()

        assert payload["gear_seeking"]
        assert payload["gear_offering"]
        assert payload["gear_seeking"][0]["source_url"] == "https://reddit.com/gear_need_inferred"
        assert "power" in payload["gear_seeking"][0]["categories"]
        assert payload["gear_offering"][0]["source_url"] == (
            "https://discord.com/channels/test/gear_offer_review"
        )
        assert all(
            item["source_url"] != "https://facebook.com/stale_rv_sale"
            for item in payload["gear_offering"]
        )
        assert "shade" in payload["gear_offering"][0]["categories"]
        assert payload["gear_offering"][0]["condition"] == "good"
    finally:
        _reset_engine()


def test_community_listings_keep_longer_snippets_for_browse_cards(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_listings_long_snippets.db")

    now = datetime.now(UTC).replace(tzinfo=None)
    long_body = (
        "The Campfire Talks team is hoping to produce a session on Integrating your Burning Man "
        "Experience and is interested in connecting with Burners who have professional background "
        "in coaching, facilitation, therapy, or reflective group work. We want enough room in the "
        "community card preview to make the post understandable before someone clicks through."
    )

    async def _seed() -> None:
        async with get_session() as session:
            session.add(
                Post(
                    platform=Platform.REDDIT,
                    platform_post_id="reddit_long_snippet",
                    platform_author_id="camp_long_snippet",
                    title="Long browse card content",
                    raw_text=long_body,
                    role=PostRole.CAMP,
                    post_type=PostType.MENTORSHIP,
                    status=PostStatus.INDEXED,
                    detected_at=now - timedelta(hours=1),
                )
            )
            await session.commit()

    try:
        asyncio.run(_seed())
        client = TestClient(create_app(enable_scheduler=False))
        response = client.get("/community/api/listings")
        assert response.status_code == 200
        payload = response.json()

        snippet = payload["camps"][0]["snippet"]
        assert len(snippet) > 220
        assert "Integrating your Burning Man Experience" in snippet
        assert "professional background in coaching" in snippet
    finally:
        _reset_engine()


@pytest.mark.asyncio
async def test_load_cross_platforms_map_batches_lookup() -> None:
    calls: list[object] = []

    class FakeResult:
        def all(self) -> list[tuple[str, str]]:
            return [
                ("parent-1", Platform.REDDIT),
                ("parent-1", Platform.DISCORD),
                ("parent-2", Platform.FACEBOOK),
            ]

    class FakeSession:
        async def exec(self, stmt):
            calls.append(stmt)
            return FakeResult()

    result = await public_router._load_cross_platforms_map(
        FakeSession(),
        ["parent-1", "parent-2"],
    )

    assert len(calls) == 1
    assert result == {
        "parent-1": [Platform.DISCORD, Platform.REDDIT],
        "parent-2": [Platform.FACEBOOK],
    }


def test_community_discovery_api_blocks_javascript_source_urls(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_discovery_js_url.db")

    now = datetime.now(UTC).replace(tzinfo=None)

    async def _seed() -> None:
        async with get_session() as session:
            session.add_all(
                [
                    Post(
                        platform=Platform.REDDIT,
                        platform_post_id="disc_js_url",
                        platform_author_id="evil",
                        source_url="javascript:alert('xss')",
                        title="Malicious camp",
                        raw_text="Legit looking text",
                        role=PostRole.CAMP,
                        post_type=PostType.MENTORSHIP,
                        status=PostStatus.INDEXED,
                        detected_at=now - timedelta(hours=1),
                    ),
                    Post(
                        platform=Platform.REDDIT,
                        platform_post_id="disc_https_url",
                        platform_author_id="legit",
                        source_url="https://reddit.com/r/ok",
                        title="Legit camp",
                        raw_text="Fine post",
                        role=PostRole.CAMP,
                        post_type=PostType.MENTORSHIP,
                        status=PostStatus.INDEXED,
                        detected_at=now - timedelta(hours=2),
                    ),
                ]
            )
            await session.commit()

    try:
        asyncio.run(_seed())
        client = TestClient(create_app(enable_scheduler=False))
        resp = client.get("/community/api/discovery?tab=mentorship_camps")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2
        # identify by snippet (raw_text is used as snippet)
        js_item = next(i for i in items if "Legit looking" in (i["snippet"] or ""))
        https_item = next(i for i in items if "Fine post" in (i["snippet"] or ""))
        assert js_item["source_url"] is None
        assert https_item["source_url"] == "https://reddit.com/r/ok"
    finally:
        _reset_engine()


def test_community_discovery_api_sanitizes_snippets(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_discovery_sanitize.db")

    now = datetime.now(UTC).replace(tzinfo=None)

    async def _seed() -> None:
        async with get_session() as session:
            session.add(
                Post(
                    platform=Platform.REDDIT,
                    platform_post_id="disc_pii",
                    platform_author_id="pii_user",
                    source_url="https://reddit.com/pii",
                    title="Seeker with PII",
                    raw_text="Email me at secret@example.com or u/MyUsername for details.",
                    role=PostRole.SEEKER,
                    post_type=PostType.MENTORSHIP,
                    status=PostStatus.INDEXED,
                    detected_at=now - timedelta(hours=1),
                )
            )
            await session.commit()

    try:
        asyncio.run(_seed())
        client = TestClient(create_app(enable_scheduler=False))
        resp = client.get("/community/api/discovery?tab=mentorship_seekers")
        assert resp.status_code == 200
        snippet = resp.json()["items"][0]["snippet"]
        assert "secret@example.com" not in snippet
        assert "u/MyUsername" not in snippet
    finally:
        _reset_engine()


def test_community_data_does_not_retry_non_disconnect(monkeypatch, tmp_path) -> None:
    _setup_sqlite_db(monkeypatch, tmp_path, "community_no_retry_non_disconnect.db")
    calls = {"count": 0}

    async def _boom(_session):
        calls["count"] += 1
        raise RuntimeError("not a disconnect")

    try:
        monkeypatch.setattr(public_router, "build_public_community_payload", _boom)
        client = TestClient(create_app(enable_scheduler=False), raise_server_exceptions=False)
        response = client.get("/community/data")
        assert response.status_code == 500
        assert calls["count"] == 1
    finally:
        _reset_engine()
