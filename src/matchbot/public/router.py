"""Public community showcase page and data endpoint."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import aliased
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from matchbot.branding import (
    BRAND_FONT_STYLESHEET,
    FAVICON_LINK_TAGS,
    build_brand_logo_link,
    build_google_analytics_tags,
    build_meta_tags,
)
from matchbot.db.models import InfraRole, Match, MatchStatus, Post, PostRole, PostStatus, PostType
from matchbot.extraction.keywords import is_vehicle_sale_or_rental_listing, keyword_filter
from matchbot.log_config import log_exception
from matchbot.settings import get_settings

router = APIRouter(prefix="/community", tags=["community"])
logger = logging.getLogger(__name__)

_community_cache: dict = {}
_CACHE_TTL = 30.0  # seconds
_CAMP_DIRECTORY_URL = (
    "https://docs.google.com/spreadsheets/d/1pR6kqBTIYLal2GNN6mJ_ueGygaWLo6R2HB5wQhUlBDw/"
    "edit?gid=1307777311#gid=1307777311"
)


def clear_community_cache() -> None:
    """Evict the in-process community payload cache (used in tests)."""
    _community_cache.clear()


def _community_feedback_url() -> str:
    settings = get_settings()
    if settings.community_feedback_email:
        return (
            f"mailto:{settings.community_feedback_email}"
            "?subject=Matchbot%20Community%20Feedback"
            "&body=How%20would%20you%20improve%20it%3F"
        )
    if settings.community_feedback_url:
        return settings.community_feedback_url
    return "/forms/"


def _opt_out_note() -> str:
    settings = get_settings()
    email = settings.community_feedback_email
    if not email:
        return ""
    return f'&nbsp;&middot;&nbsp;<a href="mailto:{email}">Don&#8217;t want your post here? Email us to opt out</a>'


def _page_footer_html(*, feedback_url: str) -> str:
    return (
        '<div class="page-footer">'
        "Rising Sparks is a grassroots collective, community-built, community-led."
        '&nbsp;&middot;&nbsp;<a href="/community/transparency">Open stats &rarr;</a>'
        f'&nbsp;&middot;&nbsp;<a href="{feedback_url}">Send Feedback &rarr;</a>'
        '&nbsp;&middot;&nbsp;<a href="https://github.com/RisingSparks/community-matchbot" target="_blank" rel="noopener noreferrer">GitHub &rarr;</a>'
        f"{_opt_out_note()}"
        "</div>"
    )


def _google_analytics_tags() -> str:
    return build_google_analytics_tags()


def _page_meta_tags(*, title: str, description: str, path: str, base_url: str) -> str:
    return build_meta_tags(
        title=title,
        description=description,
        path=path,
        base_url=base_url,
    )


async def _load_cross_platforms_map(
    session: AsyncSession,
    post_ids: list[str],
) -> dict[str, list[str]]:
    if not post_ids:
        return {}

    dups = (
        await session.exec(
            select(Post.parent_post_id, Post.platform).where(Post.parent_post_id.in_(post_ids))
        )
    ).all()
    cross_platforms: dict[str, set[str]] = {}
    for parent_post_id, platform in dups:
        if parent_post_id is None:
            continue
        cross_platforms.setdefault(parent_post_id, set()).add(platform)
    return {post_id: sorted(platforms) for post_id, platforms in cross_platforms.items()}


async def _run_with_db_retry[T](
    operation_name: str,
    callback: Callable[[AsyncSession], Awaitable[T]],
    *,
    max_attempts: int = 2,
) -> T:
    from matchbot.db.engine import dispose_engine, get_engine, is_disconnect_error

    for attempt in range(1, max_attempts + 1):
        try:
            async with AsyncSession(get_engine(), expire_on_commit=False) as session:
                return await callback(session)
        except Exception as exc:
            if attempt >= max_attempts or not is_disconnect_error(exc):
                raise
            backoff_seconds = 0.2 * attempt
            logger.warning(
                "Transient DB disconnect in %s (attempt %d/%d). Retrying in %.1fs.",
                operation_name,
                attempt,
                max_attempts,
                backoff_seconds,
            )
            await dispose_engine()
            await asyncio.sleep(backoff_seconds)

    raise RuntimeError(f"Unreachable retry termination for operation {operation_name}.")


# ── Shared navigation & design for new community pages ──────────────────────

_NAV_CSS = (
    """
@import url('"""
    + BRAND_FONT_STYLESHEET
    + """');

:root { 
  --nav-h: 64px;
  --nav-bg: rgba(247,243,233,0.94);
  --nav-border: rgba(74,74,74,0.16);
}
.site-nav {
  position: fixed; bottom: 0; left: 0; right: 0; height: var(--nav-h);
  background: var(--nav-bg); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
  border-top: 1px solid var(--nav-border); z-index: 100;
  padding-bottom: env(safe-area-inset-bottom);
}
.site-nav__inner { display: flex; height: 64px; max-width: 700px; margin: 0 auto; }
.site-nav__logo { display: none; }
.site-nav__cta { display: none; }
.nav-tab {
  flex: 1; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 3px; text-decoration: none; color: #4a4a4a;
  min-height: 44px; transition: color 0.15s;
}
.nav-tab__icon { font-size: 18px; line-height: 1; }
.nav-tab__label { font-family: "Anton", Impact, sans-serif; font-size: 10px; font-weight: 400; letter-spacing: 0.03em; text-transform: uppercase; }
.nav-tab--active { color: #000; }
body { padding-bottom: calc(var(--nav-h) + env(safe-area-inset-bottom)); }
@media (min-width: 680px) {
  body { padding-bottom: 0; padding-top: 58px; }
  .site-nav { top: 0; bottom: auto; height: 58px; border-top: none; border-bottom: 1px solid var(--nav-border); }
  .site-nav__inner { max-width: 1120px; height: 58px; align-items: center; padding: 0 18px; }
  .nav-tab { flex: 0 0 auto; flex-direction: row; gap: 6px; padding: 8px 14px; border-radius: 999px; min-height: auto; }
  .nav-tab__icon { font-size: 15px; }
  .nav-tab__label { font-size: 13px; text-transform: none; letter-spacing: 0; }
  .nav-tab--active { background: rgba(255,146,0,0.16); }
  .site-nav__logo {
    display: inline-flex; align-items: center; gap: 10px; margin-right: auto;
    color: #000; text-decoration: none; padding: 6px 14px 6px 0;
  }
  .site-nav__logo-mark { width: 32px; height: auto; display: block; }
  .site-nav__logo-text { font-family: "Anton", Impact, sans-serif; font-size: 20px; line-height: 1; }
  .site-nav__cta {
    display: inline-flex; align-items: center; justify-content: center;
    margin-left: 10px; padding: 10px 16px; border-radius: 999px;
    background: #ff9200; color: #000; text-decoration: none;
    font-family: "Anton", Impact, sans-serif; font-size: 14px; line-height: 1;
    letter-spacing: 0.02em; white-space: nowrap;
    box-shadow: 0 10px 24px rgba(255,146,0,0.24);
    transition: transform 0.15s, box-shadow 0.15s, background 0.15s;
  }
  .site-nav__cta:hover {
    background: #ff9e19;
    transform: translateY(-1px);
    box-shadow: 0 12px 28px rgba(255,146,0,0.3);
  }
}
"""
)


def _nav_html(active: str) -> str:
    """Build the site navigation bar with the given tab active."""
    tabs = [
        ("home", "/community/", "\u2302", "Home"),
        ("camps", "/community/camps", "\u26fa", "Camps"),
        ("seekers", "/community/seekers", "\u2726", "Seekers"),
        ("gear", "/community/gear", "\u2699", "Gear"),
        ("transparency", "/community/transparency", "\u25ce", "Data"),
    ]
    items = []
    for key, href, icon, label in tabs:
        cls = "nav-tab nav-tab--active" if key == active else "nav-tab"
        aria = ' aria-current="page"' if key == active else ""
        items.append(
            f'<a href="{href}" class="{cls}"{aria}>'
            f'<span class="nav-tab__icon" aria-hidden="true">{icon}</span>'
            f'<span class="nav-tab__label">{label}</span>'
            f"</a>"
        )
    logo = build_brand_logo_link(
        "/community/",
        link_class="site-nav__logo",
        image_class="site-nav__logo-mark",
        text_class="site-nav__logo-text",
    )
    cta = '<a href="/forms/" class="site-nav__cta">Submit</a>'
    return (
        '<nav class="site-nav" aria-label="Site navigation">'
        # f'<div class="site-nav__inner">{logo}{"".join(items)}{cta}</div>'
        f'<div class="site-nav__inner">{"".join(items)}{cta}</div>'
        "</nav>"
    )


_BROWSE_CSS = """
:root {
  --brand-cream: #f7f3e9;
  --brand-charcoal: #4a4a4a;
  --brand-spark: #ff9200;
  --brand-black: #000000;
  --card-bg: rgba(255,255,255,0.82);
  --card-border: rgba(0,0,0,0.12);
  --card-radius-lg: 18px;
  --card-radius-md: 12px;
  --card-shadow: 0 14px 34px rgba(0,0,0,0.08);
  --ink: var(--brand-black);
  --muted: var(--brand-charcoal);
  --sun: var(--brand-spark);
  --sage: var(--brand-black);
}
*, *::before, *::after { box-sizing: border-box; }
body {
  margin: 0; font-family: "Merriweather", Georgia, serif;
  color: var(--ink);
  background:
    radial-gradient(circle at 14% 6%, rgba(255,146,0,0.20) 0%, transparent 32%),
    radial-gradient(circle at 88% 22%, rgba(0,0,0,0.05) 0%, transparent 26%),
    linear-gradient(180deg, #fbf8f0 0%, var(--brand-cream) 72%, #f3ede1 100%);
  min-height: 100vh;
}
.page-wrap { max-width: 1120px; margin: 0 auto; padding: 28px 16px 32px; }
.page-header { margin-bottom: 20px; }
.page-header h1 {
  font-family: "Anton", Impact, sans-serif;
}
.gear-panel-head h2, .empty-state h2, .page-cta strong, .listing-card__title,
.section-label, .filter-chip, .empty-state a, .page-cta a {
  font-family: "Oswald", "Anton", Impact, sans-serif;
}
.page-header h1 { margin: 0 0 6px; font-size: clamp(22px, 5vw, 36px); font-weight: 400; line-height: 1.02; letter-spacing: 0.01em; }
.page-header .sub { margin: 0; font-size: 15px; color: var(--muted); line-height: 1.5; max-width: 58ch; }
.section-label { font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; font-weight: 500; color: var(--sun); margin: 0 0 10px; }
.filter-row {
  display: flex; flex-wrap: wrap; gap: 8px;
  overflow: visible; scrollbar-width: none;
  padding-right: 2px; padding-bottom: 4px; margin-bottom: 20px; -webkit-overflow-scrolling: touch;
}
.filter-row::-webkit-scrollbar { display: none; }
.filter-chip {
  display: inline-flex; align-items: center; padding: 7px 14px;
  background: var(--card-bg); border: 1.5px solid var(--card-border); border-radius: 999px;
  font-size: 13px; font-weight: 500; white-space: nowrap; cursor: pointer;
  min-height: 44px; color: var(--ink); transition: background 0.15s, border-color 0.15s, color 0.15s;
  flex-shrink: 0; user-select: none; -webkit-user-select: none;
  gap: 7px;
}
.filter-chip:hover { border-color: var(--sun); color: var(--ink); }
.filter-chip.active { background: var(--ink); border-color: var(--ink); color: #fff; }
.filter-chip__count { opacity: 0.6; font-size: 11px; }
.card-grid { display: flex; flex-direction: column; gap: 0; }
.listing-card {
  background: transparent; border: none; border-bottom: 1px solid var(--card-border);
  border-radius: 0; padding: 14px 4px;
  display: flex; flex-direction: row; gap: 14px; align-items: flex-start;
}
.listing-card:first-child { border-top: 1px solid var(--card-border); }
.listing-card--compact { --snippet-lines: 3; }
.listing-card--browse { --snippet-lines: 4; }
.listing-card__meta {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding-top: 2px; min-width: 56px; flex-shrink: 0;
}
.platform-badge {
  display: inline-flex; align-items: center; padding: 3px 8px;
  border-radius: 999px; font-size: 11px; font-weight: 700; letter-spacing: 0.02em;
}
.platform-badge--reddit { background: #ff4500; color: #fff; }
.platform-badge--discord { background: #5865f2; color: #fff; }
.platform-badge--facebook { background: #1877f2; color: #fff; }
.platform-badge--manual { background: var(--sage); color: #fff; }
.card-age { font-size: 11px; color: var(--muted); white-space: nowrap; }
.listing-card__body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.listing-card__header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.listing-card__title { margin: 0; font-size: 16px; font-weight: 500; line-height: 1.2; }
.tag-row { display: flex; flex-wrap: wrap; gap: 6px; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.tag--vibe { background: rgba(255,146,0,0.16); color: #8d4f00; }
.tag--contrib { background: rgba(0,0,0,0.08); color: var(--ink); }
.tag--infra { background: rgba(74,74,74,0.12); color: var(--muted); }
.tag--cond { background: rgba(0,0,0,0.06); color: var(--muted); }
.listing-card__text { display: grid; gap: 6px; }
.listing-card__snippet {
  margin: 0; font-size: 13px; line-height: 1.5; color: #4a4448;
  display: -webkit-box; -webkit-line-clamp: var(--snippet-lines, 3); -webkit-box-orient: vertical;
  overflow: hidden;
}
.listing-card__text.is-expanded .listing-card__snippet {
  display: block; -webkit-line-clamp: unset; overflow: visible;
}
.listing-card__toggle {
  justify-self: start; padding: 0; border: 0; background: none;
  color: var(--ink); font: inherit; font-size: 12px; font-weight: 700;
  cursor: pointer; text-decoration: underline; text-underline-offset: 2px;
}
.listing-card__toggle:hover { color: var(--sun); }
.listing-card__toggle:focus-visible {
  outline: 2px solid var(--sun); outline-offset: 3px; border-radius: 4px;
}
.listing-card__footer { display: flex; align-items: flex-start; flex-shrink: 0; padding-top: 2px; }
.source-link { font-size: 12px; color: var(--ink); text-decoration: none; font-weight: 700; white-space: nowrap; }
.source-link:hover { text-decoration: underline; }
.empty-state { text-align: center; padding: 64px 24px; color: var(--muted); }
.empty-state__icon { font-size: 40px; margin-bottom: 16px; }
.empty-state h2 { margin: 0 0 8px; font-size: 20px; color: var(--ink); }
.empty-state p { margin: 0 0 20px; font-size: 15px; line-height: 1.5; max-width: 42ch; margin-left: auto; margin-right: auto; }
.empty-state a { display: inline-block; padding: 10px 20px; background: var(--sun); color: #000; border-radius: 999px; font-weight: 500; text-decoration: none; font-size: 14px; }
.loading-state { text-align: center; padding: 48px 24px; color: var(--muted); font-size: 15px; }
.page-cta {
  margin-top: 32px; background: linear-gradient(135deg, #111111, #333333);
  border-radius: var(--card-radius-lg); padding: 20px 24px;
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; flex-wrap: wrap; color: #f7f3e9;
}
.page-cta p { margin: 0; font-size: 15px; line-height: 1.4; flex: 1; }
.page-cta strong { display: block; font-size: 20px; margin-bottom: 4px; }
.page-cta a {
  display: inline-block; padding: 10px 20px; background: var(--sun); color: #000;
  border-radius: 999px; font-weight: 500; text-decoration: none; font-size: 14px;
  white-space: nowrap; flex-shrink: 0;
}
.page-footer { margin-top: 24px; font-size: 12px; color: var(--muted); text-align: center; line-height: 1.5; }
.page-footer a { color: var(--ink); }
.gear-panels { display: grid; grid-template-columns: minmax(0, 1fr); gap: 28px; }
@media (min-width: 900px) { .gear-panels { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; } }
.gear-panel {
  padding: 16px;
  border: 1px solid rgba(0,0,0,0.12);
  border-radius: 18px;
  background: rgba(255,255,255,0.72);
  scroll-margin-top: 84px;
  min-width: 0;
}
.gear-panel--active {
  border-color: var(--sun);
  box-shadow: 0 12px 26px rgba(255,146,0,0.14);
}
.gear-panel-head { margin: 0 0 16px; }
.gear-panel-head h2 { margin: 0 0 4px; font-size: 22px; font-weight: 400; }
.gear-panel-head p { margin: 0; font-size: 14px; color: var(--muted); }
"""

_TAXONOMY_JS = r"""
const VIBE_LABELS = {
  art:'Art', music:'Music', dance:'Dance', fire_arts:'Fire Arts',
  technology:'Tech', build_focused:'Builders', workshop:'Workshop',
  wellness:'Wellness', sober:'Sober-friendly', party:'Party vibes',
  family_friendly:'Family-friendly', queer:'Queer', inclusive:'Inclusive',
  theme_structured:'Themed', theme_open:'Open theme', eco_minded:'Eco-minded',
  radical_self_reliance:'Self-reliant', community_first:'Community-first',
  experiential:'Experiential', performance:'Performance', spiritual:'Spiritual',
  late_night:'Late-night', polyamorous:'Poly-friendly'
};
const CONTRIB_LABELS = {
  build:'Build', art:'Art', art_support:'Art Support', fabrication:'Fabrication',
  kitchen_food:'Kitchen & Food', medic:'Medical', sound:'Sound & Audio',
  lighting:'Lighting', tech:'Tech', camp_admin:'Camp Admin', greeter:'Greeters',
  ranger:'Rangers', fire:'Fire Safety', photography:'Photography',
  video:'Video', performance:'Performance', decor:'Decor'
};
const INFRA_LABELS = {
  power:'Power', shade:'Shade', tools:'Tools', transport:'Transport',
  water:'Water', kitchen_infra:'Kitchen Gear', sound_gear:'Sound Gear',
  lighting_gear:'Lighting', hvac:'HVAC/Cooling', furniture:'Furniture',
  sanitation:'Sanitation', communication:'Comms', safety:'Safety Gear',
  art_supplies:'Art Supplies', fab_gear:'Fab Gear', rigging:'Rigging',
  storage:'Storage', camping:'Camping Gear', vehicles:'Vehicles',
  trailers:'Trailers', generators:'Generators', solar:'Solar',
  fire_safety:'Fire Safety', medical:'Medical', rebar:'Rebar'
};
function esc(str) {
  if (!str) return '';
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(str).replace(/[&<>"']/g, s => map[s]);
}
function humanLabel(dict, slug) {
  // Replace underscores with spaces and capitalize each word
  const label = dict[slug] || slug.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  return esc(label);
}
function fmt(n) { return new Intl.NumberFormat().format(n || 0); }
function timeAgo(iso) {
  const diff = Date.now() - new Date(iso).getTime();
  const days = Math.floor(diff / 86400000), hours = Math.floor(diff / 3600000);
  if (days >= 7) return Math.floor(days / 7) + 'w ago';
  if (days >= 1) return days + 'd ago';
  if (hours >= 1) return hours + 'h ago';
  return 'recently';
}
function platformBadge(p, others) {
  const labels = {reddit:'Reddit', discord:'Discord', facebook:'Facebook', manual:'Form'};
  const cls = ['reddit','discord','facebook','manual'].includes(p) ? p : 'manual';
  let html = '<span class="platform-badge platform-badge--' + cls + '">' + esc(labels[p] || p) + '</span>';
  
  if (others && others.length) {
    others.forEach(o => {
       const oCls = ['reddit','discord','facebook','manual'].includes(o) ? o : 'manual';
       html += '<span class="platform-badge platform-badge--' + oCls + '" style="margin-left:-8px; border:2px solid var(--brand-cream); opacity:0.8" title="Also posted on ' + esc(labels[o] || o) + '"></span>';
    });
  }
  return html;
}
function vibeTags(vibes, max) {
  return (vibes || []).slice(0, max || 3).map(v =>
    '<span class="tag tag--vibe">' + humanLabel(VIBE_LABELS, v) + '</span>'
  ).join('');
}
function contribTags(contribs, max) {
  return (contribs || []).slice(0, max || 2).map(c =>
    '<span class="tag tag--contrib">' + humanLabel(CONTRIB_LABELS, c) + '</span>'
  ).join('');
}
function infraTags(cats, max) {
  return (cats || []).slice(0, max || 3).map(c =>
    '<span class="tag tag--infra">' + humanLabel(INFRA_LABELS, c) + '</span>'
  ).join('');
}
function conditionTag(cond) {
  if (!cond || cond === 'unknown') return '';
  const labels = {new:'New', good:'Good', fair:'Fair', worn:'Worn', needs_repair:'Needs repair'};
  return '<span class="tag tag--cond">' + esc(labels[cond] || cond) + '</span>';
}
function sourceLink(url) {
  if (!url) return '';
  return '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer" class="source-link">Original post \u2192</a>';
}
function snippetBlock(text, mode) {
  const content = esc(text || '');
  if (!content) return '';
  const variant = mode === 'browse' ? 'listing-card--browse' : 'listing-card--compact';
  if ((text || '').length <= 280) {
    return { cardClass: variant, html: '<p class="listing-card__snippet">' + content + '</p>' };
  }
  return {
    cardClass: variant,
    html:
      '<div class="listing-card__text">'
      + '<p class="listing-card__snippet">' + content + '</p>'
      + '<button type="button" class="listing-card__toggle" aria-expanded="false">Show more</button>'
      + '</div>'
  };
}
function renderListingCard(options) {
  const snippet = snippetBlock(options.snippet || '', options.mode || 'browse');
  return '<article class="listing-card ' + snippet.cardClass + '">'
    + '<div class="listing-card__meta">'
    + platformBadge(options.platform, options.crossPlatforms)
    + '<span class="card-age">' + timeAgo(options.occurredAt || options.detectedAt) + '</span>'
    + '</div>'
    + '<div class="listing-card__body">'
    + '<div class="listing-card__header">'
    + '<h3 class="listing-card__title">' + options.title + '</h3>'
    + '<div class="listing-card__footer">' + sourceLink(options.sourceUrl) + '</div>'
    + '</div>'
    + (options.tagHtml || '')
    + (options.detailHtml || '')
    + snippet.html
    + '</div>'
    + '</article>';
}
function emptyState(heading, body) {
  return '<div class="empty-state"><div class="empty-state__icon">\u2726</div><h2>' + esc(heading) + '</h2><p>' + esc(body) + '</p><a href="/forms/">Submit a post \u2192</a></div>';
}
document.addEventListener('click', event => {
  const button = event.target.closest('.listing-card__toggle');
  if (!button) return;
  const block = button.closest('.listing-card__text');
  if (!block) return;
  const expanded = block.classList.toggle('is-expanded');
  button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  button.textContent = expanded ? 'Show less' : 'Show more';
});
"""

_HOME_EXTRA_CSS = """
.mobile-intake-banner {
  margin: 0 0 18px;
  padding: 14px 14px 12px;
  border-radius: 18px;
  background: rgba(17,17,17,0.94);
  color: #f7f3e9;
  box-shadow: 0 16px 30px rgba(0,0,0,0.18);
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
}
.mobile-intake-banner__eyebrow {
  margin: 0 0 6px;
  font-family: "Anton", Impact, sans-serif;
  font-size: 11px;
  line-height: 1;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #ffb347;
}
.mobile-intake-banner__row {
  display: flex; align-items: center; gap: 12px;
}
.mobile-intake-banner__copy {
  flex: 1; min-width: 0;
}
.mobile-intake-banner__copy strong {
  display: block;
  margin: 0 0 3px;
  font-family: "Anton", Impact, sans-serif;
  font-size: 22px;
  line-height: 0.96;
  font-weight: 400;
}
.mobile-intake-banner__copy span {
  display: block;
  font-size: 12px;
  line-height: 1.45;
  color: rgba(247,243,233,0.82);
}
.mobile-intake-banner__cta {
  display: inline-flex; align-items: center; justify-content: center;
  min-height: 48px; padding: 0 18px;
  border-radius: 999px;
  background: #ff9200; color: #000;
  text-decoration: none;
  font-family: "Anton", Impact, sans-serif;
  font-size: 15px; line-height: 1;
  white-space: nowrap; flex-shrink: 0;
  box-shadow: 0 10px 22px rgba(255,146,0,0.28);
}
.hero { margin-bottom: 28px; }
.hero-lockup { display: inline-flex; align-items: center; gap: 14px; margin-bottom: 16px; text-decoration: none; color: inherit; }
.hero-lockup__image { width: 72px; height: auto; display: block; }
.hero-lockup__text { font-family: "Anton", Impact, sans-serif; font-size: clamp(26px, 4vw, 38px); line-height: 0.95; }
.eyebrow { font-family: "Anton", Impact, sans-serif; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: #ff9200; margin: 0 0 8px; }
.hero h1 { margin: 0 0 12px; font-family: "Anton", Impact, sans-serif; font-size: clamp(36px, 8vw, 64px); font-weight: 400; line-height: 0.95; letter-spacing: 0.01em; }
.hero p { margin: 0; font-size: 16px; line-height: 1.8; color: #4a4a4a; max-width: 52ch; }
.hero p + p { margin-top: 12px; }
.sunset-notice {
  margin: 0 0 18px;
  padding: 14px 16px;
  border: 1.5px solid rgba(255, 146, 0, 0.55);
  border-radius: 16px;
  background: rgba(255, 246, 226, 0.95);
  color: #221e21;
  font-size: 14px;
  line-height: 1.6;
}
.sunset-notice p { margin: 0; }
.sunset-notice a { color: #000; font-weight: 700; }
.intro-panel {
  margin-bottom: 28px;
  padding: 18px 16px;
  border-radius: 20px;
  background: rgba(255,255,255,0.82);
  border: 1px solid rgba(0,0,0,0.12);
  box-shadow: 0 14px 28px rgba(0,0,0,0.06);
}
.intro-panel__title {
  margin: 0 0 8px;
  font-family: "Anton", Impact, sans-serif;
  font-size: clamp(24px, 5vw, 34px);
  line-height: 0.98;
  font-weight: 400;
}
.intro-panel__lede {
  margin: 0;
  max-width: 60ch;
  font-size: 15px;
  line-height: 1.7;
  color: #4a4a4a;
}
.intro-steps {
  display: grid;
  gap: 10px;
  margin-top: 16px;
}
.intro-step {
  padding: 14px 14px 12px;
  border-radius: 16px;
  background: #fffdf9;
  border: 1px solid rgba(0,0,0,0.1);
}
.intro-step strong {
  display: block;
  margin: 0 0 5px;
  font-family: "Anton", Impact, sans-serif;
  font-size: 20px;
  line-height: 1;
  font-weight: 400;
}
.intro-step p {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: #4a4a4a;
}
.intro-sources {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}
.intro-source {
  display: inline-flex;
  align-items: center;
  padding: 7px 11px;
  border-radius: 999px;
  background: rgba(255,146,0,0.12);
  border: 1px solid rgba(255,146,0,0.16);
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
  color: #8d4f00;
}
.intro-footnote {
  margin: 14px 0 0;
  font-size: 13px;
  line-height: 1.65;
  color: #5a5458;
}
.intro-feedback {
  margin: 14px 0 0;
  font-size: 13px;
  line-height: 1.65;
  color: #5a5458;
}
.intro-feedback a {
  color: #000;
  font-weight: 700;
}
@media (min-width: 760px) {
  .intro-steps { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
.entry-list { display: grid; gap: 10px; margin-bottom: 28px; }
.entry-card {
  display: flex; align-items: center; gap: 14px; padding: 18px 20px;
  background: rgba(255,255,255,0.82); border: 1.5px solid rgba(0,0,0,0.12);
  border-radius: 18px; text-decoration: none; color: #221e21;
  transition: border-color 0.15s, transform 0.12s, box-shadow 0.15s;
}
.entry-card:hover { border-color: #ff9200; transform: translateY(-2px); box-shadow: 0 16px 28px rgba(0,0,0,0.08); }
.entry-icon {
  font-size: 24px; flex-shrink: 0; width: 46px; height: 46px;
  display: flex; align-items: center; justify-content: center;
  background: rgba(255,146,0,0.15); border-radius: 12px;
}
.entry-body { flex: 1; display: flex; flex-direction: column; gap: 2px; }
.entry-body strong { font-family: "Anton", Impact, sans-serif; font-size: 20px; font-weight: 400; line-height: 1.05; }
.entry-body span { font-size: 13px; color: #4a4a4a; line-height: 1.5; }
.entry-arrow { color: #ff9200; font-size: 18px; flex-shrink: 0; }
.snapshot-section { margin-bottom: 28px; }
.snapshot-note { margin: 10px 0 0; font-size: 14px; line-height: 1.65; color: #4a4a4a; max-width: 58ch; }
.snapshot-groups { display: grid; gap: 14px; margin-top: 14px; }
.snapshot-group {
  background: rgba(255,255,255,0.82); border: 1px solid rgba(0,0,0,0.12);
  border-radius: 16px; padding: 16px 14px;
}
.snapshot-group h2 { margin: 0 0 6px; font-family: "Anton", Impact, sans-serif; font-size: 24px; line-height: 1.02; }
.snapshot-group p { margin: 0; font-size: 14px; line-height: 1.6; color: #4a4a4a; }
.snapshot-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-top: 14px; }
@media (max-width: 520px) { .snapshot-grid { grid-template-columns: 1fr; } }
.snapshot-card {
  background: #fffdfd; border: 1px solid rgba(0,0,0,0.12);
  border-radius: 12px; padding: 16px 12px;
  display: flex; flex-direction: column; align-items: flex-start; gap: 4px; text-align: left;
}
.snapshot-card:hover { border-color: #ff9200; transform: translateY(-1px); }
.snapshot-link {
  color: inherit; text-decoration: none;
  transition: border-color 0.15s, transform 0.12s;
}
.snapshot-num { font-family: "Anton", Impact, sans-serif; font-size: clamp(30px, 4vw, 42px); font-weight: 400; color: #000; line-height: 1; }
.snapshot-lbl { font-size: 13px; color: #221e21; font-weight: 700; line-height: 1.3; }
.snapshot-desc { margin: 0; font-size: 12px; line-height: 1.55; color: #4a4a4a; }
.snapshot-gloss { margin: 12px 0 0; font-size: 12px; line-height: 1.5; color: #4a4a4a; }
.recent-section { margin-bottom: 28px; }
@media (min-width: 680px) {
  .mobile-intake-banner { display: none; }
}
@media (max-width: 420px) {
  .mobile-intake-banner__row { flex-direction: column; align-items: stretch; }
  .mobile-intake-banner__cta { width: 100%; }
}
.see-more-btn {
  display: inline-block; padding: 10px 24px; background: transparent;
  border: 1.5px solid var(--sun); color: var(--ink); border-radius: 999px;
  font-family: "Oswald", "Anton", Impact, sans-serif; font-size: 14px;
  font-weight: 500; cursor: pointer; text-decoration: none;
  transition: background 0.15s, color 0.15s, transform 0.12s;
}
.see-more-btn:hover { background: var(--sun); color: #000; transform: translateY(-1px); }
"""

_HOME_BODY = """
  <main class="page-wrap">
    <section class="sunset-notice" aria-label="Matchbot sunset notice">
      <p>We learned a lot from Matchbot, but we’re sunsetting it—at least in its current format. Looking for a camp? Visit the <a href="__CAMP_DIRECTORY_URL__" target="_blank" rel="noopener noreferrer">camp directory</a>.</p>
    </section>
    <section class="mobile-intake-banner" aria-label="Submit a post">
      <div class="mobile-intake-banner__eyebrow">Ready To Join?</div>
      <div class="mobile-intake-banner__row">
        <div class="mobile-intake-banner__copy">
          <strong>Submit a post</strong>
          <span>Share what you need, what you can offer, or who you're looking for.</span>
        </div>
        <a href="/forms/" class="mobile-intake-banner__cta">Submit</a>
      </div>
    </section>
    <section class="hero">
      <a href="/community/" class="hero-lockup" aria-label="Rising Sparks home">
        <img src="/media/rising-sparks-logo.png" alt="Rising Sparks" class="hero-lockup__image">
      </a>
      <h1>Find your people.<br>Build the city.</h1>
      <p>The Burner community is spread across Facebook groups, Discord, Reddit, Spark Classifieds, and more. MatchBot is a community-run prototype that helps people find each other faster.</p>
      <p>If you need something, have something to offer, or are looking for a camp, project, collaborator, or piece of gear, this is one place to see what people are doing and add your own post.</p>
    </section>

    <div class="entry-list">
      <a href="/community/camps" class="entry-card">
        <div class="entry-icon">\u26fa</div>
        <div class="entry-body">
          <strong>Looking for a camp or project</strong>
          <span>Browse camps and art projects with openings this season</span>
        </div>
        <span class="entry-arrow" aria-hidden="true">\u2192</span>
      </a>
      <a href="/community/seekers" class="entry-card">
        <div class="entry-icon">\u2726</div>
        <div class="entry-body">
          <strong>Our camp or project needs people</strong>
          <span>Find motivated builders, artists, and contributors looking for a home</span>
        </div>
        <span class="entry-arrow" aria-hidden="true">\u2192</span>
      </a>
      <a href="/community/gear" class="entry-card">
        <div class="entry-icon">\u2699</div>
        <div class="entry-body">
          <strong>Gear &amp; infrastructure exchange</strong>
          <span>Shade, power, tools, transport \u2014 what the community needs and what\u2019s available</span>
        </div>
        <span class="entry-arrow" aria-hidden="true">\u2192</span>
      </a>
    </div>

    <section class="intro-panel" aria-labelledby="how-it-works-title">
      <div class="section-label">How It Works</div>
      <h2 id="how-it-works-title" class="intro-panel__title">Posts in. Matches out.</h2>
      <p class="intro-panel__lede">We'll will gladly drive 100 miles for the right generator, shade structure, or collaborator. The hard part usually isn't willingness. It is finding each other before the opportunity disappears.</p>
      <div class="intro-steps">
        <div class="intro-step">
          <strong>From the places people already use</strong>
          <p>We pick up posts from the platforms people already use. If you want to submit directly, that works too, and it will show up on Spark Classifieds.</p>
        </div>
        <div class="intro-step">
          <strong>Combining the chaos</strong>
          <p>MatchBot pulls community posts into one pool so you can browse what is happening without getting sucked into platform nonsense.</p>
        </div>
        <div class="intro-step">
          <strong>Connect people who should meet</strong>
          <p>We are building toward better introductions between camps, seekers, builders, and infrastructure offers and asks, not just another feed.</p>
        </div>
      </div>
      <div class="intro-sources" aria-label="Current sources">
        <span class="intro-source">Facebook</span>
        <span class="intro-source">Discord</span>
        <span class="intro-source">Reddit</span>
        <span class="intro-source">Direct submissions</span>
        <span class="intro-source">Spark Classifieds</span>
      </div>
      <p class="intro-feedback">Help us build this? Tell us what feels useful, what’s missing, or what feels too automated. <a id="feedback-link" href="__COMMUNITY_FEEDBACK_URL__">Send us feedback.</a></p>
    </section>
    
    <section class="snapshot-section">
      <div class="section-label">In The Pool</div>
      <p class="snapshot-note">A quick read on what people are posting right now. Each card opens the relevant listings or stats view.</p>
      <div class="snapshot-groups" id="snapshot-groups">
        <div class="loading-state">Loading overview\u2026</div>
      </div>
    </section>
    <section class="recent-section">
      <div class="section-label">Recent Posts</div>
      <div class="card-grid recent-posts-grid" id="recent-grid">
        <div class="loading-state">Loading\u2026</div>
      </div>
      <div id="recent-more-container" style="display:none; margin-top:16px; text-align:center;">
        <button id="recent-more-btn" class="see-more-btn">See more recent posts \u2192</button>
      </div>
    </section>
    <div class="page-cta">
      <p><strong>Ready to connect?</strong>Submit a post and let us help find the right match.</p>
      <a href="/forms/">Submit a post \u2192</a>
    </div>
    __PAGE_FOOTER__
  </main>
"""

_HOME_JS = """
let allRecent = [];

function renderRecent(limit = null) {
  const grid = document.getElementById('recent-grid');
  const items = limit ? allRecent.slice(0, limit) : allRecent;
  grid.innerHTML = items.map(({item, type}) => {
    const title = type === 'camp'
      ? esc(item.name || item.title || 'Camp or Project')
      : esc(item.title || (item.contributions && item.contributions.length ? humanLabel(CONTRIB_LABELS, item.contributions[0]) : 'Seeker'));
    return renderListingCard({
      mode: 'compact',
      title,
      snippet: item.snippet,
      platform: item.platform,
      occurredAt: item.occurred_at,
      detectedAt: item.detected_at,
      sourceUrl: item.source_url,
      crossPlatforms: item.cross_platforms,
      tagHtml: '<div class="tag-row">' + vibeTags(item.vibes, 2) + contribTags(item.contributions, 2) + '</div>',
    });
  }).join('');
  
  const container = document.getElementById('recent-more-container');
  if (limit && allRecent.length > limit) {
    container.style.display = 'block';
  } else {
    container.style.display = 'none';
  }
}

document.getElementById('recent-more-btn').addEventListener('click', () => {
  renderRecent(null);
});

async function loadHome() {
  try {
    const [mRes, lRes] = await Promise.all([
      fetch('/community/api/metrics'),
      fetch('/community/api/listings'),
    ]);
    const metrics = await mRes.json();
    const listings = await lRes.json();
    const summary = metrics.summary || {};
    const m = metrics.key_metrics || {};
    const indexed = summary.indexed || 0;
    const campPct = indexed > 0 ? Math.round(((m.active_camps || 0) / indexed) * 100) : 0;
    const seekerPct = indexed > 0 ? Math.round(((m.active_seekers || 0) / indexed) * 100) : 0;
    document.getElementById('snapshot-groups').innerHTML = [
      '<div class="snapshot-group">'
        + '<h2>Camp Connections</h2>'
        + '<p>Who has openings, and who is trying to find a place to contribute.</p>'
        + '<div class="snapshot-grid">'
          + '<a href="/community/camps" class="snapshot-card snapshot-link" aria-label="Browse camps and art projects">'
            + '<span class="snapshot-lbl">Camps &amp; Art Projects</span>'
            + '<span class="snapshot-num">' + fmt(m.active_camps) + '</span>'
            + '<p class="snapshot-desc">' + fmt(campPct) + '% of indexed posts — groups with openings or offerings</p>'
          + '</a>'
          + '<a href="/community/seekers" class="snapshot-card snapshot-link" aria-label="Browse seekers">'
            + '<span class="snapshot-lbl">Seekers</span>'
            + '<span class="snapshot-num">' + fmt(m.active_seekers) + '</span>'
            + '<p class="snapshot-desc">' + fmt(seekerPct) + '% of indexed posts — people looking to join or contribute</p>'
          + '</a>'
        + '</div>'
      + '</div>',
      '<div class="snapshot-group">'
        + '<h2>Infrastructure Exchange</h2>'
        + '<p>Gear, camp admin, and support posts that are active in the pool.</p>'
        + '<div class="snapshot-grid">'
          + '<a href="/community/gear?view=needs#need-panel" class="snapshot-card snapshot-link" aria-label="Browse infrastructure needs">'
            + '<span class="snapshot-lbl">Infra Needs</span>'
            + '<span class="snapshot-num">' + fmt(m.active_infra_seeking) + '</span>'
            + '<p class="snapshot-desc">Indexed or reviewable posts seeking gear, camp admin, or support</p>'
          + '</a>'
          + '<a href="/community/gear?view=offers#offer-panel" class="snapshot-card snapshot-link" aria-label="Browse infrastructure offers">'
            + '<span class="snapshot-lbl">Infra Offers</span>'
            + '<span class="snapshot-num">' + fmt(m.active_infra_offering) + '</span>'
            + '<p class="snapshot-desc">Indexed or reviewable posts offering gear, camp admin, or support</p>'
          + '</a>'
        + '</div>'
      + '</div>'
    ].join('');
    
    allRecent = [
      ...(listings.camps || []).map(c => ({item: c, type: 'camp'})),
      ...(listings.seekers || []).map(s => ({item: s, type: 'seeker'})),
    ].sort((a, b) => new Date(b.item.occurred_at) - new Date(a.item.occurred_at));

    if (!allRecent.length) {
      document.getElementById('recent-grid').innerHTML = emptyState('Nothing recent yet', 'Check back soon \u2014 or submit a post to join the pool.');
      return;
    }
    renderRecent(4);
  } catch(e) {
    document.getElementById('recent-grid').innerHTML = '<p style="color:#6a6264;padding:24px">Could not load listings.</p>';
  }
}
loadHome();
"""

_CAMPS_BODY = """
  <div class="filter-row" id="camp-filters" role="group" aria-label="Filter by vibe or skill"></div>
  <div class="card-grid" id="camp-grid">
    <div class="loading-state">Loading camps and projects\u2026</div>
  </div>
  <div class="page-cta">
    <p><strong>Running a camp or art project?</strong>List your openings and find the people you need.</p>
    <a href="/forms/camp">List your camp \u2192</a>
  </div>
  __PAGE_FOOTER__
"""

_CAMPS_JS = """
let allCamps = [], activeCampFilters = new Set();

function buildCampCard(item) {
  const vibes = item.vibes || [], contribs = item.contributions || [];
  return renderListingCard({
    mode: 'browse',
    title: esc(item.name || item.title || 'Camp or Project'),
    snippet: item.snippet,
    platform: item.platform,
    occurredAt: item.occurred_at,
    detectedAt: item.detected_at,
    sourceUrl: item.source_url,
    crossPlatforms: item.cross_platforms,
    tagHtml: '<div class="tag-row">' + vibeTags(vibes, 3) + contribTags(contribs, 2) + '</div>',
  });
}

function renderCamps() {
  const grid = document.getElementById('camp-grid');
  const filtered = activeCampFilters.size === 0 ? allCamps : allCamps.filter(item => {
    const tags = [...(item.vibes || []), ...(item.contributions || [])];
    return [...activeCampFilters].some(f => tags.includes(f));
  });
  grid.innerHTML = filtered.length
    ? filtered.map(buildCampCard).join('')
    : emptyState('No matches for these filters', 'Try removing a filter or check back as more camps list their openings.');
}

function buildCampFilters(camps) {
  const tagCounts = {};
  camps.forEach(c => {
    [...(c.vibes || []), ...(c.contributions || [])].forEach(t => { tagCounts[t] = (tagCounts[t] || 0) + 1; });
  });
  const sorted = Object.entries(tagCounts).sort((a, b) => b[1] - a[1]).slice(0, 16);
  const row = document.getElementById('camp-filters');
  if (!sorted.length) { row.style.display = 'none'; return; }
  row.innerHTML = sorted.map(([slug, count]) => {
    const label = VIBE_LABELS[slug] ? humanLabel(VIBE_LABELS, slug) : humanLabel(CONTRIB_LABELS, slug);
    return '<button class="filter-chip" data-tag="' + esc(slug) + '" aria-pressed="false">' + label + '<span class="filter-chip__count">(' + count + ')</span></button>';
  }).join('');
  row.querySelectorAll('.filter-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      const tag = btn.dataset.tag;
      if (activeCampFilters.has(tag)) { activeCampFilters.delete(tag); btn.classList.remove('active'); btn.setAttribute('aria-pressed','false'); }
      else { activeCampFilters.add(tag); btn.classList.add('active'); btn.setAttribute('aria-pressed','true'); }
      renderCamps();
    });
  });
}

async function loadCamps() {
  try {
    const res = await fetch('/community/api/listings');
    const data = await res.json();
    allCamps = data.camps || [];
    if (!allCamps.length) {
      document.getElementById('camp-grid').innerHTML = emptyState('No active camp listings right now', 'Check back soon \u2014 or submit a post below.');
      return;
    }
    buildCampFilters(allCamps);
    renderCamps();
  } catch(e) {
    document.getElementById('camp-grid').innerHTML = '<p style="color:#6a6264;padding:24px">Could not load listings.</p>';
  }
}
loadCamps();
"""

_SEEKERS_BODY = """
  <div class="filter-row" id="seeker-filters" role="group" aria-label="Filter by skill or vibe"></div>
  <div class="card-grid" id="seeker-grid">
    <div class="loading-state">Loading seekers\u2026</div>
  </div>
  <div class="page-cta">
    <p><strong>Looking for a camp?</strong>Add yourself to the pool and let us find a connection.</p>
    <a href="/forms/seeker">Submit a post \u2192</a>
  </div>
  __PAGE_FOOTER__
"""

_SEEKERS_JS = """
let allSeekers = [], activeSeekerFilters = new Set();

function buildSeekerCard(item) {
  const vibes = item.vibes || [], contribs = item.contributions || [];
  const lead = item.title || (contribs.length ? humanLabel(CONTRIB_LABELS, contribs[0]) : 'Seeker');
  return renderListingCard({
    mode: 'browse',
    title: lead,
    snippet: item.snippet,
    platform: item.platform,
    occurredAt: item.occurred_at,
    detectedAt: item.detected_at,
    sourceUrl: item.source_url,
    crossPlatforms: item.cross_platforms,
    tagHtml: '<div class="tag-row">' + vibeTags(vibes, 3) + contribTags(contribs, 2) + '</div>',
  });
}

function renderSeekers() {
  const grid = document.getElementById('seeker-grid');
  const filtered = activeSeekerFilters.size === 0 ? allSeekers : allSeekers.filter(item => {
    const tags = [...(item.vibes || []), ...(item.contributions || [])];
    return [...activeSeekerFilters].some(f => tags.includes(f));
  });
  grid.innerHTML = filtered.length
    ? filtered.map(buildSeekerCard).join('')
    : emptyState('No matches for these filters', 'Try removing a filter or check back as more seekers join the pool.');
}

function buildSeekerFilters(seekers) {
  const tagCounts = {};
  seekers.forEach(s => {
    [...(s.vibes || []), ...(s.contributions || [])].forEach(t => { tagCounts[t] = (tagCounts[t] || 0) + 1; });
  });
  const sorted = Object.entries(tagCounts).sort((a, b) => b[1] - a[1]).slice(0, 16);
  const row = document.getElementById('seeker-filters');
  if (!sorted.length) { row.style.display = 'none'; return; }
  row.innerHTML = sorted.map(([slug, count]) => {
    const label = VIBE_LABELS[slug] ? humanLabel(VIBE_LABELS, slug) : humanLabel(CONTRIB_LABELS, slug);
    return '<button class="filter-chip" data-tag="' + esc(slug) + '" aria-pressed="false">' + label + '<span class="filter-chip__count">(' + count + ')</span></button>';
  }).join('');
  row.querySelectorAll('.filter-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      const tag = btn.dataset.tag;
      if (activeSeekerFilters.has(tag)) { activeSeekerFilters.delete(tag); btn.classList.remove('active'); btn.setAttribute('aria-pressed','false'); }
      else { activeSeekerFilters.add(tag); btn.classList.add('active'); btn.setAttribute('aria-pressed','true'); }
      renderSeekers();
    });
  });
}

async function loadSeekers() {
  try {
    const res = await fetch('/community/api/listings');
    const data = await res.json();
    allSeekers = data.seekers || [];
    if (!allSeekers.length) {
      document.getElementById('seeker-grid').innerHTML = emptyState('No active seeker listings right now', 'Check back soon \u2014 or submit a post below.');
      return;
    }
    buildSeekerFilters(allSeekers);
    renderSeekers();
  } catch(e) {
    document.getElementById('seeker-grid').innerHTML = '<p style="color:#6a6264;padding:24px">Could not load listings.</p>';
  }
}
loadSeekers();
"""

_GEAR_BODY = """
  <div class="gear-panels">
    <div class="gear-panel" id="need-panel">
      <div class="gear-panel-head">
        <div class="section-label">Infra Needs</div>
        <h2>What people need</h2>
        <p>Camps and crews looking for gear, structures, or camp admin support.</p>
      </div>
      <div class="filter-row" id="need-filters" role="group" aria-label="Filter gear needs by category"></div>
      <div id="need-grid"><div class="loading-state">Loading needs\u2026</div></div>
    </div>
    <div class="gear-panel" id="offer-panel">
      <div class="gear-panel-head">
        <div class="section-label">Infra Offers</div>
        <h2>What people have</h2>
        <p>Available gear, structures, and equipment to lend, share, or give.</p>
      </div>
      <div class="filter-row" id="offer-filters" role="group" aria-label="Filter gear offers by category"></div>
      <div id="offer-grid"><div class="loading-state">Loading offers\u2026</div></div>
    </div>
  </div>
  <div class="page-cta">
    <p><strong>Have gear to share, or need something?</strong>Post it to the exchange.</p>
    <a href="/forms/infra">Post to exchange \u2192</a>
  </div>
  __PAGE_FOOTER__
"""

_GEAR_JS = """
function setGearFocus(view) {
  const panels = {
    needs: document.getElementById('need-panel'),
    offers: document.getElementById('offer-panel'),
  };
  Object.values(panels).forEach(panel => panel.classList.remove('gear-panel--active'));
  if (!view || !panels[view]) return;
  panels[view].classList.add('gear-panel--active');
}

function buildGearCard(item) {
  const cats = item.categories || [];
  return renderListingCard({
    mode: 'browse',
    title: esc(item.title || 'Infra listing'),
    snippet: item.snippet,
    platform: item.platform,
    occurredAt: item.occurred_at,
    detectedAt: item.detected_at,
    sourceUrl: item.source_url,
    crossPlatforms: item.cross_platforms,
    tagHtml: '<div class="tag-row">' + infraTags(cats, 3) + conditionTag(item.condition) + '</div>',
    detailHtml: [
      item.infra_offer_type ? '<p style="margin:0;font-size:13px;color:#6a6264">Offer: ' + esc(item.infra_offer_type) + '</p>' : '',
      item.quantity ? '<p style="margin:0;font-size:13px;color:#6a6264">Qty: ' + esc(item.quantity) + '</p>' : '',
      item.pickup_location ? '<p style="margin:0;font-size:13px;color:#6a6264">Pickup: ' + esc(item.pickup_location) + '</p>' : '',
      item.delivery_available !== null && item.delivery_available !== undefined ? '<p style="margin:0;font-size:13px;color:#6a6264">Delivery: ' + (item.delivery_available ? 'yes' : 'no') + '</p>' : '',
      item.dimensions ? '<p style="margin:0;font-size:13px;color:#6a6264">Size: ' + esc(item.dimensions) + '</p>' : '',
      item.parts_included ? '<p style="margin:0;font-size:13px;color:#6a6264">Parts: ' + esc(item.parts_included) + '</p>' : '',
      item.setup_notes ? '<p style="margin:0;font-size:13px;color:#6a6264">Setup: ' + esc(item.setup_notes) + '</p>' : '',
    ].filter(Boolean).join(''),
  });
}

function setupGearPanel(items, filterId, gridId) {
  const tagCounts = {};
  items.forEach(i => (i.categories || []).forEach(t => { tagCounts[t] = (tagCounts[t] || 0) + 1; }));
  const sorted = Object.entries(tagCounts).sort((a, b) => b[1] - a[1]).slice(0, 12);
  const filterRow = document.getElementById(filterId);
  let activeFilters = new Set();

  if (sorted.length) {
    filterRow.innerHTML = sorted.map(([slug, count]) =>
      '<button class="filter-chip" data-tag="' + esc(slug) + '" aria-pressed="false">'
      + humanLabel(INFRA_LABELS, slug)
      + '<span class="filter-chip__count">(' + count + ')</span></button>'
    ).join('');
  } else {
    filterRow.style.display = 'none';
  }

  const renderGrid = () => {
    const filtered = activeFilters.size === 0 ? items : items.filter(i =>
      [...activeFilters].some(f => (i.categories || []).includes(f))
    );
    document.getElementById(gridId).innerHTML = filtered.length
      ? filtered.map(buildGearCard).join('')
      : '<p style="color:#6a6264;text-align:center;padding:24px">Nothing matching these filters.</p>';
  };

  filterRow.querySelectorAll('.filter-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      const tag = btn.dataset.tag;
      if (activeFilters.has(tag)) { activeFilters.delete(tag); btn.classList.remove('active'); btn.setAttribute('aria-pressed','false'); }
      else { activeFilters.add(tag); btn.classList.add('active'); btn.setAttribute('aria-pressed','true'); }
      renderGrid();
    });
  });
  renderGrid();
}

async function loadGear() {
  try {
    const params = new URLSearchParams(window.location.search);
    const view = params.get('view');
    setGearFocus(view);
    const res = await fetch('/community/api/listings');
    const data = await res.json();
    const seeking = data.gear_seeking || [], offering = data.gear_offering || [];
    if (!seeking.length) {
      document.getElementById('need-grid').innerHTML = '<div class="empty-state"><div class="empty-state__icon">\u2726</div><p>Nothing listed here right now \u2014 check back soon.</p></div>';
    } else {
      setupGearPanel(seeking, 'need-filters', 'need-grid');
    }
    if (!offering.length) {
      document.getElementById('offer-grid').innerHTML = '<div class="empty-state"><div class="empty-state__icon">\u2726</div><p>Nothing listed here right now \u2014 check back soon.</p></div>';
    } else {
      setupGearPanel(offering, 'offer-filters', 'offer-grid');
    }
  } catch(e) {
    document.getElementById('need-grid').innerHTML = '<p style="color:#6a6264;padding:24px">Could not load gear listings.</p>';
    document.getElementById('offer-grid').innerHTML = '<p style="color:#6a6264;padding:24px">Could not load gear listings.</p>';
  }
}
loadGear();
"""


def _build_home_page(base_url: str) -> str:
    nav = _nav_html("home")
    feedback_url = _community_feedback_url()
    footer_html = _page_footer_html(feedback_url=feedback_url)
    analytics_tags = _google_analytics_tags()
    meta_tags = _page_meta_tags(
        title="Rising Sparks — Find Your Community",
        description=(
            "Find camps, projects, seekers, and infrastructure signals across the burner "
            "ecosystem with MatchBot."
        ),
        path="/community/",
        base_url=base_url,
    )
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  {meta_tags}\n"
        "  "
        + FAVICON_LINK_TAGS
        + "\n"
        + (f"  {analytics_tags}\n" if analytics_tags else "")
        + "  <style>"
        + _NAV_CSS
        + _BROWSE_CSS
        + _HOME_EXTRA_CSS
        + "</style>\n"
        + "</head>\n<body>\n"
        + nav
        + "\n"
        + _HOME_BODY.replace("__CAMP_DIRECTORY_URL__", _CAMP_DIRECTORY_URL)
        .replace("__COMMUNITY_FEEDBACK_URL__", feedback_url)
        .replace("__PAGE_FOOTER__", footer_html)
        + "<script>\n"
        + _TAXONOMY_JS
        + _HOME_JS
        + "\n</script>\n"
        + "</body>\n</html>"
    )


def _build_camps_page(base_url: str) -> str:
    feedback_url = _community_feedback_url()
    footer_html = _page_footer_html(feedback_url=feedback_url)
    nav = _nav_html("camps")
    analytics_tags = _google_analytics_tags()
    meta_tags = _page_meta_tags(
        title="Camps & Projects — Rising Sparks",
        description="Browse active camps and art projects looking for contributors this season.",
        path="/community/camps",
        base_url=base_url,
    )
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  {meta_tags}\n"
        "  "
        + FAVICON_LINK_TAGS
        + "\n"
        + (f"  {analytics_tags}\n" if analytics_tags else "")
        + "  <style>"
        + _NAV_CSS
        + _BROWSE_CSS
        + "</style>\n"
        + "</head>\n<body>\n"
        + nav
        + "\n"
        + '<main class="page-wrap">\n'
        + '  <div class="page-header"><h1>Camps &amp; projects</h1>'
        + '<p class="sub">Active camps and projects looking for contributors this season.</p></div>\n'
        + _CAMPS_BODY.replace("__PAGE_FOOTER__", footer_html)
        + "</main>\n"
        + "<script>\n"
        + _TAXONOMY_JS
        + _CAMPS_JS
        + "\n</script>\n"
        + "</body>\n</html>"
    )


def _build_seekers_page(base_url: str) -> str:
    feedback_url = _community_feedback_url()
    footer_html = _page_footer_html(feedback_url=feedback_url)
    nav = _nav_html("seekers")
    analytics_tags = _google_analytics_tags()
    meta_tags = _page_meta_tags(
        title="Builders & Seekers — Rising Sparks",
        description="See people looking for their camp, project, collaborators, or next build.",
        path="/community/seekers",
        base_url=base_url,
    )
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  {meta_tags}\n"
        "  "
        + FAVICON_LINK_TAGS
        + "\n"
        + (f"  {analytics_tags}\n" if analytics_tags else "")
        + "  <style>"
        + _NAV_CSS
        + _BROWSE_CSS
        + "</style>\n"
        + "</head>\n<body>\n"
        + nav
        + "\n"
        + '<main class="page-wrap">\n'
        + '  <div class="page-header"><h1>Builders &amp; seekers</h1>'
        + '<p class="sub">People looking for their camp or project this season.</p></div>\n'
        + _SEEKERS_BODY.replace("__PAGE_FOOTER__", footer_html)
        + "</main>\n"
        + "<script>\n"
        + _TAXONOMY_JS
        + _SEEKERS_JS
        + "\n</script>\n"
        + "</body>\n</html>"
    )


def _build_gear_page(base_url: str) -> str:
    feedback_url = _community_feedback_url()
    footer_html = _page_footer_html(feedback_url=feedback_url)
    nav = _nav_html("gear")
    analytics_tags = _google_analytics_tags()
    meta_tags = _page_meta_tags(
        title="Gear Exchange — Rising Sparks",
        description="Browse infrastructure needs and offers for shade, power, tools, transport, and more.",
        path="/community/gear",
        base_url=base_url,
    )
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  {meta_tags}\n"
        "  "
        + FAVICON_LINK_TAGS
        + "\n"
        + (f"  {analytics_tags}\n" if analytics_tags else "")
        + "  <style>"
        + _NAV_CSS
        + _BROWSE_CSS
        + "</style>\n"
        + "</head>\n<body>\n"
        + nav
        + "\n"
        + '<main class="page-wrap">\n'
        + '  <div class="page-header"><h1>Gear exchange</h1>'
        + '<p class="sub">Gear, structures, and equipment \u2014 what the community needs and what\u2019s available.</p></div>\n'
        + _GEAR_BODY.replace("__PAGE_FOOTER__", footer_html)
        + "</main>\n"
        + "<script>\n"
        + _TAXONOMY_JS
        + _GEAR_JS
        + "\n</script>\n"
        + "</body>\n</html>"
    )


# ── Existing community dashboard (moved to /transparency) ────────────────────

_COMMUNITY_HTML = (
    """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Rising Sparks Community Dashboard</title>
  """
    + FAVICON_LINK_TAGS
    + """
  <style>
    @import url('"""
    + BRAND_FONT_STYLESHEET
    + """');
    :root {
      --sand: #f7f3e9;
      --dust: #efe7d6;
      --ink: #000000;
      --sun: #ff9200;
      --sage: #000000;
      --paper: #fbf8f0;
      --card: rgba(255, 255, 255, 0.84);
      --muted: #4a4a4a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Merriweather", Georgia, serif;
      color: var(--ink);
      background:
        radial-gradient(circle at 14% 6%, rgba(255, 146, 0, 0.22), transparent 32%),
        radial-gradient(circle at 88% 22%, rgba(0, 0, 0, 0.05), transparent 26%),
        linear-gradient(170deg, var(--sand), var(--paper));
      min-height: 100vh;
    }
    .wrap { max-width: 1120px; margin: 0 auto; padding: 36px 18px 72px; }
    .hero, .panel {
      border: 1px solid rgba(0, 0, 0, 0.12);
      border-radius: 20px;
      background: rgba(255, 255, 255, 0.82);
      padding: 22px;
      box-shadow: 0 14px 32px rgba(0, 0, 0, 0.08);
      margin-bottom: 14px;
    }
    .eyebrow {
      font-family: "Anton", Impact, sans-serif;
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--sage);
    }
    h1, .section-title, .pool-group-title, .disc-card-name, .cta strong, .metric {
      font-family: "Anton", Impact, sans-serif;
      font-weight: 400;
    }
    h1 { margin: 8px 0 10px; font-size: clamp(36px, 6vw, 58px); line-height: 0.95; letter-spacing: 0.01em; }
    .hero-blurb {
      margin: 0;
      max-width: 64ch;
      font-size: 16px;
      line-height: 1.8;
      color: var(--muted);
    }
    .hero-actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 18px;
    }
    .hero-actions a {
      text-decoration: none;
      font-family: "Anton", Impact, sans-serif;
      font-weight: 400;
      border-radius: 999px;
      padding: 10px 18px;
    }
    .hero-actions .hero-primary {
      background: var(--sun);
      color: #000;
    }
    .hero-actions .hero-secondary {
      background: rgba(0, 0, 0, 0.06);
      color: var(--ink);
      border: 1px solid rgba(0, 0, 0, 0.12);
    }
    .hero-cta-note {
      margin: 10px 0 0;
      font-size: 14px;
      color: var(--muted);
      max-width: 52ch;
    }
    .section-title { margin: 8px 0 12px; font-size: 22px; }
    .grid4, .grid2, .grid3 { display: grid; gap: 10px; }
    .grid4 { grid-template-columns: repeat(4, 1fr); }
    .grid2 { grid-template-columns: repeat(2, 1fr); }
    .grid3 { grid-template-columns: repeat(3, 1fr); }
    .card {
      background: var(--card);
      border-radius: 14px;
      border: 1px solid rgba(0, 0, 0, 0.1);
      padding: 12px;
      box-shadow: 0 6px 14px rgba(0, 0, 0, 0.06);
    }
    .kicker {
      font-family: "Anton", Impact, sans-serif;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
    }
    .metric { font-size: clamp(24px, 3vw, 34px); margin: 4px 0; }
    .note { margin: 0; font-size: 13px; color: var(--muted); }
    .pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.03em;
      background: #ece7dc;
      margin-bottom: 6px;
    }
    .timeline { max-height: 420px; overflow: auto; padding-right: 4px; }
    .event-row { border-bottom: 1px dashed rgba(34, 32, 33, 0.12); padding: 9px 0; }
    .event-row:last-child { border-bottom: none; }
    .event-meta { font-size: 12px; color: var(--muted); }
    .event-text { font-size: 14px; line-height: 1.4; margin-top: 2px; }
    .tab-bar {
      display: flex;
      gap: 8px;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }
    .tab-btn {
      border: 1px solid rgba(0, 0, 0, 0.18);
      background: #ffffff;
      color: var(--ink);
      border-radius: 999px;
      padding: 6px 12px;
      font-family: "Anton", Impact, sans-serif;
      font-size: 13px;
      font-weight: 400;
      cursor: pointer;
    }
    .tab-btn[aria-selected="true"] {
      background: rgba(255, 146, 0, 0.14);
      border-color: rgba(255, 146, 0, 0.6);
    }
    .tab-pane { display: none; }
    .tab-pane.active { display: block; }
    .table-wrap {
      overflow-x: auto;
      border-radius: 12px;
      border: 1px solid rgba(0, 0, 0, 0.12);
      background: #ffffff;
    }
    .data-table {
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
      font-size: 13px;
    }
    .data-table th,
    .data-table td {
      text-align: left;
      padding: 10px 12px;
      border-bottom: 1px solid rgba(0, 0, 0, 0.1);
      vertical-align: top;
    }
    .data-table th {
      font-family: "Anton", Impact, sans-serif;
      font-size: 12px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
      background: rgba(255, 146, 0, 0.08);
      position: sticky;
      top: 0;
    }
    .data-table tr:last-child td { border-bottom: none; }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
    }
    .bars { display: flex; flex-direction: column; gap: 10px; }
    .bar-row {
      display: grid;
      grid-template-columns: 120px 1fr 42px;
      gap: 10px;
      align-items: center;
    }
    .bar-label { font-size: 13px; color: #3f3c3d; }
    .bar-track {
      height: 10px;
      border-radius: 999px;
      background: rgba(0, 0, 0, 0.12);
      overflow: hidden;
    }
    .bar-fill {
      height: 10px;
      border-radius: 999px;
      background: linear-gradient(90deg, #000000, #4a4a4a);
    }
    .bar-value { font-size: 12px; color: var(--muted); text-align: right; }
    .cta {
      margin-top: 18px;
      border-radius: 16px;
      background: linear-gradient(135deg, #111111, #343434);
      color: #f8f7f5;
      padding: 18px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
    }
    .cta a {
      text-decoration: none;
      background: var(--sun);
      color: #000;
      font-family: "Anton", Impact, sans-serif;
      font-weight: 400;
      border-radius: 999px;
      padding: 10px 18px;
    }
    .cta-actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    .updated { margin-top: 10px; font-size: 12px; color: var(--muted); }
    .funnel-flow {
      display: flex;
      align-items: stretch;
      margin-top: 18px;
      flex-wrap: wrap;
      gap: 0;
    }
    .funnel-step { flex: 1; min-width: 120px; }
    .funnel-connector {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 0 4px;
      flex-shrink: 0;
      gap: 3px;
    }
    .funnel-rate {
      font-size: 11px;
      font-weight: 700;
      color: var(--sun);
      white-space: nowrap;
    }
    .funnel-chevron { font-size: 24px; color: var(--sun); line-height: 1; }
    .group-desc { margin: 0 0 12px; font-size: 13px; color: var(--muted); }
    .paired-row { margin-bottom: 16px; }
    .paired-label {
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 5px;
      text-transform: capitalize;
    }
    .paired-bar-group {
      display: grid;
      grid-template-columns: 88px 1fr 28px;
      gap: 8px;
      align-items: center;
      margin-bottom: 4px;
    }
    .paired-bar-label { font-size: 11px; color: var(--muted); }
    .bar-demand { background: linear-gradient(90deg, #000000, #4a4a4a); }
    .bar-supply { background: linear-gradient(90deg, #d97700, #ff9200); }
    .card-muted {
      background: rgba(245, 240, 232, 0.5);
      border-color: rgba(0, 0, 0, 0.07);
      box-shadow: none;
    }
    .card-muted .kicker { color: #aaa; }
    .card-muted .metric { font-size: 22px; color: var(--muted); font-weight: 500; }
    .pool-groups {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }
    .pool-group {
      background: rgba(255, 255, 255, 0.72);
      border: 1px solid rgba(0, 0, 0, 0.1);
      border-radius: 18px;
      padding: 14px;
      box-shadow: 0 8px 18px rgba(0, 0, 0, 0.05);
    }
    .pool-group-title {
      margin: 0;
      font-size: 16px;
      color: var(--ink);
    }
    .pool-group-copy {
      margin: 6px 0 12px;
      font-size: 13px;
      color: var(--muted);
      line-height: 1.4;
    }
    .pool-group-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 10px;
    }
    .pool-metric {
      background: var(--card);
      border-radius: 14px;
      border: 1px solid rgba(0, 0, 0, 0.08);
      padding: 12px;
    }
    .pool-metric-muted {
      background: rgba(245, 240, 232, 0.7);
      border-color: rgba(0, 0, 0, 0.07);
    }
    .disc-grid {
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
      max-height: 520px;
      overflow-y: auto;
      padding-right: 4px;
    }
    .disc-card {
      background: var(--card);
      border-radius: 14px;
      border: 1px solid rgba(0, 0, 0, 0.1);
      padding: 12px;
      box-shadow: 0 4px 10px rgba(0, 0, 0, 0.05);
    }
    .disc-card-name {
      font-size: 14px;
      margin: 0 0 6px;
      color: var(--ink);
    }
    .disc-card-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 6px; }
    .disc-tag {
      display: inline-block;
      padding: 2px 7px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.02em;
    }
    .disc-tag-vibe  { background: rgba(0, 0, 0, 0.08); color: var(--ink); }
    .disc-tag-skill { background: rgba(255, 146, 0, 0.14); color: #8d4f00; }
    .disc-tag-infra { background: rgba(74, 74, 74, 0.12); color: var(--muted); }
    .disc-card-snippet { font-size: 12px; color: #5a5658; line-height: 1.4; margin: 0; }
    .disc-card-meta { font-size: 11px; color: var(--muted); margin-top: 6px; }
    .disc-card-meta a { color: var(--ink); }
    @media (max-width: 980px) {
      .grid4 { grid-template-columns: repeat(2, 1fr); }
      .grid3 { grid-template-columns: 1fr; }
      .grid2 { grid-template-columns: 1fr; }
      .pool-groups { grid-template-columns: 1fr; }
    }
    @media (max-width: 620px) {
      .grid4 { grid-template-columns: 1fr; }
      .bar-row { grid-template-columns: 100px 1fr 34px; }
      .funnel-flow { flex-direction: column; }
      .funnel-connector { flex-direction: row; padding: 4px 0; }
      .funnel-chevron { transform: rotate(90deg); }
    }
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="eyebrow">Data Dashboard</div>
      <h1>Find your people. Build the city.</h1>
      <p class="hero-blurb">
        A community matchmaking experiment: surfacing infrastructure
        offers and asks, camp and art-project connections, and the broader
        patterns of what people across the ecosystem are seeking or ready to contribute.
      </p>
      <div class="hero-actions">
        <a id="intake-link" class="hero-primary" href="/forms/">Submit Your Signal</a>
      </div>
      <p class="hero-cta-note">
        Looking for a camp, collaborators, or infrastructure help?
        Share what you need or what you can offer.
      </p>
    </section>

    <section class="panel">
      <h2 class="section-title">Metrics Summary</h2>
      <p class="group-desc">
        A live, anonymized view of how signals become connections across camps,
        art projects, and seekers looking to participate. We use
        <strong>indexed</strong> for signals that are structured and live, and
        <strong>reviewable</strong> for signals still waiting on organizer review.
      </p>
      <div id="summary-funnel" class="funnel-flow"></div>
    </section>

    <section class="panel">
      <h2 class="section-title">Who's in the Pool</h2>
      <p class="group-desc">
        Active indexed posts by role — the supply and demand the bot is working
        with right now.
      </p>
      <div class="pool-groups" id="pool-metrics"></div>
    </section>

    <section class="panel">
      <h2 class="section-title">Infrastructure Exchange Snapshot</h2>
      <p class="group-desc">
        Active infrastructure needs and offers across indexed and reviewable posts.
      </p>
      <div id="infra-paired"></div>
    </section>

    <section class="panel">
      <h2 class="section-title">Alignment Snapshot</h2>
      <p class="group-desc">How extracted camp and seeker signals line up across recent posts.</p>
      <div class="grid2">
      <div>
        <h2 class="section-title">Skills</h2>
        <p class="group-desc">Skills mentioned in camp posts and seeker posts.</p>
        <div id="skills-paired"></div>
      </div>
      <div>
        <h2 class="section-title">Vibes</h2>
        <p class="group-desc">Vibes mentioned in camp posts and seeker posts.</p>
        <div id="vibes-paired"></div>
      </div>
      </div>
    </section>
    
    <section class="panel">
      <h2 class="section-title">Community Pool — Camps &amp; Builders</h2>
      <p class="group-desc">Camps and projects seeking contributors, alongside builders ready to plug in.</p>
      <div class="tab-bar" role="tablist" aria-label="Community pool tabs">
        <button type="button" class="tab-btn" id="disc-tab-camps" data-mentorship-tab="mentorship_camps" aria-selected="true">Camps &amp; Projects</button>
        <button type="button" class="tab-btn" id="disc-tab-seekers" data-mentorship-tab="mentorship_seekers" aria-selected="false">Builders &amp; Seekers</button>
      </div>
      <div class="disc-grid" id="discovery-grid-mentorship"></div>
      <p class="note" id="discovery-count-mentorship" style="margin-top:8px"></p>
    </section>

    <section class="panel">
      <h2 class="section-title">Community Pool — Infra Needs &amp; Offers</h2>
      <p class="group-desc">Gear, structures, and equipment — what people need and what's available to share.</p>
      <div class="tab-bar" role="tablist" aria-label="Infrastructure pool tabs">
        <button type="button" class="tab-btn" id="disc-tab-infra-need" data-infra-tab="infra_seeking" aria-selected="true">Infra Needs</button>
        <button type="button" class="tab-btn" id="disc-tab-infra-offer" data-infra-tab="infra_offering" aria-selected="false">Infra Offers</button>
      </div>
      <div class="disc-grid" id="discovery-grid-infra"></div>
      <p class="note" id="discovery-count-infra" style="margin-top:8px"></p>
    </section>

    <section class="panel">
      <h2 class="section-title">Matched Drill-Down</h2>
      <div class="grid2" style="margin-bottom: 10px;">
        <label class="note">
          Status
          <select id="match-status-filter">
            <option value="all" selected>All statuses</option>
            <option value="proposed">Proposed</option>
            <option value="approved">Approved</option>
            <option value="intro_sent">Intro sent</option>
            <option value="conversation_started">Conversation started</option>
            <option value="accepted_pending">Accepted pending</option>
            <option value="onboarded">Onboarded</option>
            <option value="declined">Declined</option>
            <option value="closed_stale">Closed stale</option>
          </select>
        </label>
        <label class="note">
          Time window
          <select id="match-days-filter">
            <option value="7">Last 7 days</option>
            <option value="30" selected>Last 30 days</option>
            <option value="90">Last 90 days</option>
            <option value="all">All time</option>
          </select>
        </label>
      </div>
      <div class="grid4" id="matched-summary"></div>
      <div class="timeline" id="matched-list"></div>
    </section>
    
    <section class="panel grid2">
      <div>
        <h2 class="section-title">Live Activity</h2>
        <div class="tab-bar" role="tablist" aria-label="Live activity views">
          <button
            type="button"
            class="tab-btn"
            id="live-view-story"
            data-target="live-feed-panel"
            aria-selected="true"
          >
            Story View
          </button>
          <button
            type="button"
            class="tab-btn"
            id="live-view-table"
            data-target="live-feed-table-panel"
            aria-selected="false"
          >
            Data Nerd View
          </button>
        </div>
        <div class="tab-pane active" id="live-feed-panel">
          <div class="timeline" id="live-feed"></div>
        </div>
        <div class="tab-pane" id="live-feed-table-panel">
          <div class="table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Occurred At</th>
                  <th>Type</th>
                  <th>Platform</th>
                  <th>Summary</th>
                  <th>Score</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody id="live-feed-table"></tbody>
            </table>
          </div>
        </div>
      </div>
      <div>
        <h2 class="section-title">Where Signals Come From</h2>
        <div class="bars" id="platform-breakdown"></div>
      </div>
    </section>

    <section class="cta">
      <div>
        <strong>Help us build this?</strong><br>
        Tell us what feels useful, what’s missing, or what feels too automated.
      </div>
      <div class="cta-actions">
        <a id="feedback-link" href="/forms/">Send Feedback to Organizers</a>
      </div>
    </section>
    <div class="updated">
      Rising Sparks is a grassroots collective, community-built, community-led. While we
      collaborate with folks across the ecosystem,
      this is 
    </div>
    <div class="updated" id="updated"></div>
  </main>

  <script>
    const fmt = (n) => new Intl.NumberFormat().format(n || 0);
    const pct = (x) => `${Math.round((x || 0) * 100)}%`;

    function metricCard(label, value, note) {
      return `
        <article class="card">
          <div class="kicker">${label}</div>
          <div class="metric">${fmt(value)}</div>
          <p class="note">${note}</p>
        </article>
      `;
    }

    function mutedCard(label, value, note) {
      return `
        <article class="card card-muted">
          <div class="kicker">${label}</div>
          <div class="metric">${fmt(value)}</div>
          <p class="note">${note}</p>
        </article>
      `;
    }

    function poolMetric(label, value, note, muted = false) {
      return `
        <article class="pool-metric ${muted ? "pool-metric-muted" : ""}">
          <div class="kicker">${label}</div>
          <div class="metric">${fmt(value)}</div>
          <p class="note">${note}</p>
        </article>
      `;
    }

    function poolGroup(title, copy, metrics) {
      return `
        <section class="pool-group">
          <h3 class="pool-group-title">${title}</h3>
          <p class="pool-group-copy">${copy}</p>
          <div class="pool-group-grid">
            ${metrics.join("")}
          </div>
        </section>
      `;
    }

    function funnelStep(label, value, desc) {
      return `
        <article class="card funnel-step">
          <div class="kicker">${label}</div>
          <div class="metric">${fmt(value)}</div>
          <p class="note">${desc}</p>
        </article>
      `;
    }

    function funnelConnector(from, to) {
      const rate = (from > 0) ? `${Math.round((to / from) * 100)}%` : null;
      return `
        <div class="funnel-connector">
          ${rate ? `<div class="funnel-rate">${rate}</div>` : ''}
          <div class="funnel-chevron">›</div>
        </div>
      `;
    }

    function barRow(name, count, max) {
      const w = max > 0 ? Math.max(4, Math.round((count / max) * 100)) : 0;
      return `
        <div class="bar-row">
          <div class="bar-label">${escapeHTML(name)}</div>
          <div class="bar-track"><div class="bar-fill" style="width:${w}%"></div></div>
          <div class="bar-value">${fmt(count)}</div>
        </div>
      `;
    }

    function eventRow(item) {
      return `
        <div class="event-row">
          <div class="pill">${escapeHTML(item.event_type)}</div>
          <div class="event-meta">
            ${new Date(item.occurred_at).toLocaleString()} • ${escapeHTML(item.platform)}
          </div>
          <div class="event-text">${escapeHTML(item.summary)}</div>
        </div>
      `;
    }

    function feedTableRow(item) {
      const score = item.score == null ? "n/a" : item.score.toFixed(3);
      const confidence = item.confidence == null
        ? "n/a"
        : `${Math.round(item.confidence * 100)}%`;
      return `
        <tr>
          <td class="mono">${new Date(item.occurred_at).toLocaleString()}</td>
          <td><span class="pill">${escapeHTML(item.event_type)}</span></td>
          <td>${escapeHTML(item.platform || "n/a")}</td>
          <td>${escapeHTML(item.summary || "")}</td>
          <td class="mono">${score}</td>
          <td class="mono">${confidence}</td>
        </tr>
      `;
    }

    function setLiveActivityTab(targetId) {
      const panes = ["live-feed-panel", "live-feed-table-panel"];
      const buttons = [
        document.getElementById("live-view-story"),
        document.getElementById("live-view-table"),
      ];
      for (const paneId of panes) {
        const pane = document.getElementById(paneId);
        pane.classList.toggle("active", paneId === targetId);
      }
      for (const btn of buttons) {
        const isActive = btn.dataset.target === targetId;
        btn.setAttribute("aria-selected", isActive ? "true" : "false");
      }
    }

    function matchedRow(item) {
      const confidence = item.confidence == null ? "n/a" : `${Math.round(item.confidence * 100)}%`;
      const score = item.score == null ? "n/a" : item.score.toFixed(3);
      const reason = item.match_reason || item.shared_signals || "Low-signal potential match.";
      const seekerLink = item.seeker_source_url
        ? (
          `<a href="${escapeHTML(item.seeker_source_url)}" target="_blank" rel="noopener noreferrer">` +
          `Seeker source</a>`
        )
        : "Seeker source: n/a";
      const campLink = item.camp_source_url
        ? (
          `<a href="${escapeHTML(item.camp_source_url)}" target="_blank" rel="noopener noreferrer">` +
          `Camp source</a>`
        )
        : "Camp source: n/a";

      return `
        <div class="event-row">
          <div class="pill">match_${escapeHTML(item.status)}</div>
          <div class="event-meta">
            ${new Date(item.created_at).toLocaleString()} •
            ${escapeHTML(item.seeker_platform)} → ${escapeHTML(item.camp_platform)}
          </div>
          <div class="event-text">
            Score ${score} • Confidence ${confidence}
          </div>
          <div class="event-text"><strong>Why this might fit:</strong> ${escapeHTML(reason)}</div>
          <div class="event-text"><strong>Seeker:</strong> ${escapeHTML(item.seeker_summary)}</div>
          <div class="event-text"><strong>Camp:</strong> ${escapeHTML(item.camp_summary)}</div>
          <div class="event-meta">${seekerLink} • ${campLink}</div>
        </div>
      `;
    }

    function escapeHTML(str) {
      if (!str) return "";
      const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
      return String(str).replace(/[&<>"']/g, (s) => map[s]);
    }

    function timeAgo(iso) {
      const h = Math.floor((Date.now() - new Date(iso).getTime()) / 3600000);
      return h < 24 ? h + "h ago" : Math.floor(h / 24) + "d ago";
    }

    function tagPills(items, cls) {
      return (items || []).slice(0, 5)
        .map((t) => `<span class="disc-tag ${cls}">${escapeHTML(t.replace(/_/g, " "))}</span>`)
        .join("");
    }

    function discCardMeta(item) {
      const src = item.source_url
        ? ` · <a href="${escapeHTML(item.source_url)}" target="_blank" rel="noopener noreferrer">source</a>`
        : "";
      return `<div class="disc-card-meta">${escapeHTML(item.platform)} · ${timeAgo(item.occurred_at || item.detected_at)}${src}</div>`;
    }

    function discCampCard(item) {
      const name = escapeHTML(item.camp_name || "Anonymous Camp");
      return `
        <article class="disc-card">
          <div class="disc-card-name">${name}</div>
          <div class="disc-card-tags">
            ${tagPills(item.vibes, "disc-tag-vibe")}
            ${tagPills(item.skills, "disc-tag-skill")}
          </div>
          <p class="disc-card-snippet">${escapeHTML(item.snippet)}</p>
          ${discCardMeta(item)}
        </article>
      `;
    }

    function discSeekerCard(item) {
      const intent = escapeHTML(
        item.seeker_intent ? item.seeker_intent.replace(/_/g, " ") : "seeking"
      );
      return `
        <article class="disc-card">
          <div class="disc-card-name">Builder &middot; ${intent}</div>
          <div class="disc-card-tags">
            ${tagPills(item.vibes, "disc-tag-vibe")}
            ${tagPills(item.skills, "disc-tag-skill")}
          </div>
          <p class="disc-card-snippet">${escapeHTML(item.snippet)}</p>
          ${discCardMeta(item)}
        </article>
      `;
    }

    function discInfraCard(item) {
      const qty = item.quantity
        ? `<span class="disc-tag disc-tag-infra">${escapeHTML(item.quantity)}</span>`
        : "";
      const cond = item.condition
        ? `<span class="disc-tag disc-tag-infra">${escapeHTML(item.condition.replace(/_/g, " "))}</span>`
        : "";
      return `
        <article class="disc-card">
          <div class="disc-card-tags">
            ${tagPills(item.infra_categories, "disc-tag-infra")}
            ${qty}${cond}
          </div>
          <p class="disc-card-snippet">${escapeHTML(item.snippet)}</p>
          ${discCardMeta(item)}
        </article>
      `;
    }

    function discoveryCard(item, tab) {
      if (tab === "mentorship_camps") return discCampCard(item);
      if (tab === "mentorship_seekers") return discSeekerCard(item);
      return discInfraCard(item);
    }

    let _discTabMentorship = "mentorship_camps";
    let _discTabInfra = "infra_seeking";

    function setMentorshipTab(tab) {
      _discTabMentorship = tab;
      for (const btn of document.querySelectorAll("[data-mentorship-tab]")) {
        btn.setAttribute("aria-selected", btn.dataset.mentorshipTab === tab ? "true" : "false");
      }
    }

    function setInfraTab(tab) {
      _discTabInfra = tab;
      for (const btn of document.querySelectorAll("[data-infra-tab]")) {
        btn.setAttribute("aria-selected", btn.dataset.infraTab === tab ? "true" : "false");
      }
    }

    async function loadMentorship(tab) {
      setMentorshipTab(tab);
      const res = await fetch(`/community/api/discovery?tab=${tab}&limit=50`);
      const data = await res.json();
      const items = data.items || [];
      document.getElementById("discovery-grid-mentorship").innerHTML = items.length
        ? items.map((item) => discoveryCard(item, tab)).join("")
        : `<p class="note" style="padding:12px 0">No active signals for this category yet.</p>`;
      const countEl = document.getElementById("discovery-count-mentorship");
      if (countEl) countEl.textContent = items.length ? `${items.length} active signal${items.length === 1 ? "" : "s"}` : "";
    }

    async function loadInfra(tab) {
      setInfraTab(tab);
      const res = await fetch(`/community/api/discovery?tab=${tab}&limit=50`);
      const data = await res.json();
      const items = data.items || [];
      document.getElementById("discovery-grid-infra").innerHTML = items.length
        ? items.map((item) => discoveryCard(item, tab)).join("")
        : `<p class="note" style="padding:12px 0">No active signals for this category yet.</p>`;
      const countEl = document.getElementById("discovery-count-infra");
      if (countEl) countEl.textContent = items.length ? `${items.length} active signal${items.length === 1 ? "" : "s"}` : "";
    }

    async function loadMatches() {
      const status = document.getElementById("match-status-filter").value || "all";
      const days = document.getElementById("match-days-filter").value || "30";
      const qs = new URLSearchParams({ status, days, limit: "50" });
      const res = await fetch(`/community/api/matches?${qs.toString()}`);
      const data = await res.json();
      const rows = data.matched || [];
      const byStatus = (data.summary && data.summary.by_status) || {};
      const introSentPlus =
        (byStatus.intro_sent || 0) +
        (byStatus.conversation_started || 0) +
        (byStatus.accepted_pending || 0) +
        (byStatus.onboarded || 0);

      document.getElementById("matched-summary").innerHTML = [
        metricCard(
          "Filtered Total",
          (data.summary && data.summary.total) || 0,
          "Within current filters"
        ),
        metricCard("Proposed", byStatus.proposed || 0, "Potential matches awaiting action"),
        metricCard("Approved", byStatus.approved || 0, "Ready for introductions"),
        metricCard("Intro Sent+", introSentPlus, "Intro sent or further progressed"),
      ].join("");

      document.getElementById("matched-list").innerHTML = rows.length
        ? rows.map((item) => matchedRow(item)).join("")
        : `<p class="note">No matches for current filters.</p>`;
    }

    function renderBars(elId, rows) {
      const el = document.getElementById(elId);
      if (!rows || !rows.length) {
        el.innerHTML = `<p class="note">No data yet.</p>`;
        return;
      }
      const max = Math.max(...rows.map((r) => r.count || 0));
      el.innerHTML = rows.map((r) => barRow(r.name || r.platform, r.count, max)).join("");
    }

    function pairedRow(item, maxVal) {
      const demand = item.demand_count || 0;
      const supply = item.supply_count || 0;
      const dw = maxVal > 0 ? Math.max(demand > 0 ? 4 : 0, Math.round((demand / maxVal) * 100)) : 0;
      const sw = maxVal > 0 ? Math.max(supply > 0 ? 4 : 0, Math.round((supply / maxVal) * 100)) : 0;
      return `
        <div class="paired-row">
          <div class="paired-label">${(item.name || "unknown").replace(/_/g, " ")}</div>
          <div class="paired-bar-group">
            <div class="paired-bar-label">Camp posts</div>
            <div class="bar-track">
              <div class="bar-fill bar-demand" style="width:${dw}%"></div>
            </div>
            <div class="bar-value">${fmt(demand)}</div>
          </div>
          <div class="paired-bar-group">
            <div class="paired-bar-label">Seeker posts</div>
            <div class="bar-track">
              <div class="bar-fill bar-supply" style="width:${sw}%"></div>
            </div>
            <div class="bar-value">${fmt(supply)}</div>
          </div>
        </div>
      `;
    }

    function renderPaired(elId, rows) {
      const el = document.getElementById(elId);
      if (!rows || !rows.length) {
        el.innerHTML = `<p class="note">No data yet.</p>`;
        return;
      }
      const maxVal = Math.max(
        ...rows.map((r) => Math.max(r.demand_count || 0, r.supply_count || 0))
      );
      el.innerHTML = rows.map((r) => pairedRow(r, maxVal)).join("");
    }

    async function load() {
      const res = await fetch("/community/data");
      const data = await res.json();

      const summary = data.summary || {};
      const weekly = data.weekly || {};
      const seen = summary.total_ingested || 0;
      const analyzed = summary.indexed || 0;
      const matched = summary.proposed_matches || 0;
      const introduced = summary.intros_sent || 0;
      document.getElementById("summary-funnel").innerHTML = [
        funnelStep(
          "Posts Detected",
          seen,
          `Raw posts ingested from Reddit, Discord & Facebook — ${
            fmt(weekly.ingested_7d || 0)
          } in the last 7 days`
        ),
        funnelConnector(seen, analyzed),
        funnelStep(
          "Structured & Indexed",
          analyzed,
          `LLM extracted role, vibes, and skills — ${
            fmt(weekly.indexed_7d || 0)
          } in the last 7 days`
        ),
        funnelConnector(analyzed, matched),
        funnelStep(
          "Potential Matches",
          matched,
          `Seeker↔camp pairs the algorithm flagged as a potential fit — ${
            fmt(weekly.matches_7d || 0)
          } in the last 7 days`
        ),
        funnelConnector(matched, introduced),
        funnelStep(
          "Introductions Sent",
          introduced,
          `Human confirmed the match and notified both parties — ${
            fmt(weekly.intros_7d || 0)
          } in the last 7 days`
        ),
      ].join("");

      const m = data.key_metrics || {};
      const camps = m.active_camps || 0;
      const seekers = m.active_seekers || 0;
      const softMatches = m.soft_matches_total || 0;
      const softMatches7d = m.soft_matches_7d || 0;
      const infraSeeking = m.active_infra_seeking || 0;
      const infraOffering = m.active_infra_offering || 0;
      const unclassified = Math.max(0, analyzed - camps - seekers);
      const campPct = analyzed > 0 ? Math.round((camps / analyzed) * 100) : 0;
      const seekerPct = analyzed > 0 ? Math.round((seekers / analyzed) * 100) : 0;
      const unclassifiedPct = analyzed > 0 ? Math.round((unclassified / analyzed) * 100) : 0;
      document.getElementById("pool-metrics").innerHTML = [
        poolGroup(
          "Camp Connections",
          "Who has openings, and who is trying to find a place to contribute.",
          [
            poolMetric(
              "Camps & Art Projects",
              camps,
              `${campPct}% of indexed posts — groups with openings or offerings`
            ),
            poolMetric(
              "Seekers",
              seekers,
              `${seekerPct}% of indexed posts — people looking to join or contribute`
            ),
          ]
        ),
        poolGroup(
          "Infrastructure Exchange",
          "Gear, camp admin, and support signals that are active in the pool.",
          [
            poolMetric(
              "Infra Needs",
              infraSeeking,
              "Indexed or reviewable posts seeking gear, camp admin, or support"
            ),
            poolMetric(
              "Infra Offers",
              infraOffering,
              "Indexed or reviewable posts offering gear, camp admin, or support"
            ),
          ]
        ),
        poolGroup(
          "Matching Queue",
          "Signals that still need a stronger classification or a human pass.",
          [
            poolMetric(
              "Soft Matches",
              softMatches,
              `${fmt(softMatches7d)} in the last 7 days ` +
              `— keyword-only candidates waiting for human review`,
              true
            ),
            poolMetric(
              "Role Unclear",
              unclassified,
              `${unclassifiedPct}% of indexed posts ` +
              `— LLM tried but could not categorize them cleanly`,
              true
            ),
          ]
        ),
      ].join("");

      const feed = data.live_feed || [];
      document.getElementById("live-feed").innerHTML = feed.length
        ? feed.map((item) => eventRow(item)).join("")
        : `<p class="note">No recent activity in the last 7 days.</p>`;
      document.getElementById("live-feed-table").innerHTML = feed.length
        ? feed.map((item) => feedTableRow(item)).join("")
        : `<tr><td colspan="6" class="note">No recent activity in the last 7 days.</td></tr>`;

      renderBars("platform-breakdown", data.platform_breakdown || []);
      renderPaired("skills-paired", (data.demand && data.demand.most_sought_skills) || []);
      renderPaired("vibes-paired", (data.demand && data.demand.most_sought_vibes) || []);
      renderPaired("infra-paired", (data.demand && data.demand.infra_exchange) || []);

      if (data.cta && data.cta.feedback_url) {
        document.getElementById("feedback-link").href = data.cta.feedback_url;
      }

      const backlog = data.backlog || {};
      const backlogText = backlog.oldest_needs_review_age_hours == null
        ? "All current signals are reviewed."
        : `Oldest pending: ${backlog.oldest_needs_review_age_hours}h`;
      const softQueueText = `Soft matches: ${fmt(backlog.soft_matches_count || 0)}`;
      document.getElementById("updated").textContent = (
        `Review queue: ${fmt(backlog.needs_review_count)} • ` +
        `${softQueueText} • ${backlogText} • Updated ${new Date(data.updated_at).toLocaleString()}`
      );

      await loadMatches();
      await loadMentorship(_discTabMentorship);
      await loadInfra(_discTabInfra);
    }

    document.getElementById("match-status-filter").addEventListener("change", loadMatches);
    document.getElementById("match-days-filter").addEventListener("change", loadMatches);
    document.getElementById("live-view-story").addEventListener("click", () => {
      setLiveActivityTab("live-feed-panel");
    });
    document.getElementById("live-view-table").addEventListener("click", () => {
      setLiveActivityTab("live-feed-table-panel");
    });
    for (const btn of document.querySelectorAll("[data-mentorship-tab]")) {
      btn.addEventListener("click", () => loadMentorship(btn.dataset.mentorshipTab));
    }
    for (const btn of document.querySelectorAll("[data-infra-tab]")) {
      btn.addEventListener("click", () => loadInfra(btn.dataset.infraTab));
    }
    load();
    setInterval(load, 60000);
  </script>
</body>
</html>
"""
)


def _render_transparency_page(base_url: str) -> str:
    meta_tags = build_meta_tags(
        title="Open Stats \u2014 Rising Sparks",
        description=(
            "See live Rising Sparks activity across camps, art projects, and infrastructure "
            "signals, plus human-reviewed matching and community demand trends."
        ),
        path="/community/transparency",
        base_url=base_url,
    )
    analytics_tags = _google_analytics_tags()
    html = _COMMUNITY_HTML.replace("<title>Rising Sparks Community Dashboard</title>", meta_tags, 1)
    if analytics_tags:
        html = html.replace("</head>", f"  {analytics_tags}\n</head>", 1)
    # Inject nav CSS and HTML into the existing page
    html = html.replace("  <style>", "  <style>" + _NAV_CSS, 1)
    nav = _nav_html("transparency")
    html = html.replace("<body>", "<body>\n  " + nav, 1)
    return html


@router.get("/", response_class=HTMLResponse)
async def community_home(request: Request) -> str:
    return _build_home_page(str(request.base_url))


@router.get("/camps", response_class=HTMLResponse)
async def community_camps(request: Request) -> str:
    return _build_camps_page(str(request.base_url))


@router.get("/seekers", response_class=HTMLResponse)
async def community_seekers(request: Request) -> str:
    return _build_seekers_page(str(request.base_url))


@router.get("/gear", response_class=HTMLResponse)
async def community_gear(request: Request) -> str:
    return _build_gear_page(str(request.base_url))


@router.get("/transparency", response_class=HTMLResponse)
async def community_transparency(request: Request) -> str:
    return _render_transparency_page(str(request.base_url))


async def _get_cached_community_payload() -> dict[str, Any]:
    """Return community payload, rebuilding at most once per _CACHE_TTL seconds."""
    import time

    now = time.monotonic()
    ts = _community_cache.get("ts")
    if ts is not None and now - ts < _CACHE_TTL:
        return _community_cache["data"]
    try:
        result = await _run_with_db_retry("community_data", build_public_community_payload)
    except Exception as exc:
        log_exception(logger, "Failed to build community payload: %s", exc)
        raise
    _community_cache["ts"] = time.monotonic()
    _community_cache["data"] = result
    return result


@router.get("/data")
async def community_data() -> dict[str, Any]:
    return await _get_cached_community_payload()


@router.get("/api/overview")
async def community_api_overview() -> dict[str, Any]:
    payload = await _get_cached_community_payload()
    return {
        "summary": payload["summary"],
        "weekly": payload["weekly"],
        "backlog": payload["backlog"],
        "updated_at": payload["updated_at"],
    }


@router.get("/api/metrics")
async def community_api_metrics() -> dict[str, Any]:
    payload = await _get_cached_community_payload()
    return {
        "summary": payload["summary"],
        "key_metrics": payload["key_metrics"],
        "updated_at": payload["updated_at"],
    }


@router.get("/api/pipeline")
async def community_api_pipeline() -> dict[str, Any]:
    payload = await _get_cached_community_payload()
    return {
        "pipeline": payload["pipeline"],
        "updated_at": payload["updated_at"],
    }


@router.get("/api/platforms")
async def community_api_platforms() -> dict[str, Any]:
    payload = await _get_cached_community_payload()
    return {
        "platform_breakdown": payload["platform_breakdown"],
        "updated_at": payload["updated_at"],
    }


@router.get("/api/feed")
async def community_api_feed() -> dict[str, Any]:
    payload = await _get_cached_community_payload()
    return {
        "live_feed": payload["live_feed"],
        "updated_at": payload["updated_at"],
    }


@router.get("/api/demand")
async def community_api_demand() -> dict[str, Any]:
    payload = await _get_cached_community_payload()
    return {
        "demand": payload["demand"],
        "updated_at": payload["updated_at"],
    }


@router.get("/api/matches")
async def community_api_matches(
    status: str = Query(default="all"),
    days: str = Query(default="30"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    return await _run_with_db_retry(
        "community_api_matches",
        lambda session: _build_matches_payload(
            session,
            status=status,
            days=days,
            limit=limit,
        ),
    )


@router.get("/api/stories")
async def community_api_stories() -> dict[str, Any]:
    payload = await _get_cached_community_payload()
    return {
        "stories": payload["stories"],
        "updated_at": payload["updated_at"],
    }


@router.get("/api/discovery")
async def community_api_discovery(
    tab: str = Query(default="mentorship_camps"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    return await _run_with_db_retry(
        "community_api_discovery",
        lambda session: _build_discovery_payload(session, tab=tab, limit=limit),
    )


async def _build_discovery_payload(
    session: AsyncSession,
    *,
    tab: str,
    limit: int,
) -> dict[str, Any]:
    allowed_tabs = {"mentorship_camps", "mentorship_seekers", "infra_seeking", "infra_offering"}
    tab_value = tab if tab in allowed_tabs else "mentorship_camps"

    stmt = select(Post)
    is_infra_tab = tab_value in ("infra_seeking", "infra_offering")
    if tab_value == "mentorship_camps":
        stmt = stmt.where(
            Post.status == PostStatus.INDEXED,
            Post.role == PostRole.CAMP,
            or_(Post.post_type == PostType.MENTORSHIP, Post.post_type.is_(None)),
        )
    elif tab_value == "mentorship_seekers":
        stmt = stmt.where(
            Post.status == PostStatus.INDEXED,
            Post.role == PostRole.SEEKER,
            or_(Post.post_type == PostType.MENTORSHIP, Post.post_type.is_(None)),
        )
    else:  # infra_seeking or infra_offering — no infra_role filter; infer in Python
        stmt = stmt.where(
            Post.status.in_({PostStatus.INDEXED, PostStatus.NEEDS_REVIEW}),
            Post.post_type == PostType.INFRASTRUCTURE,
        )
    occurred_at_expr = func.coalesce(Post.source_created_at, Post.detected_at)
    # Fetch extra rows for infra tabs so Python-side role inference still yields `limit` results
    fetch_limit = limit * 4 if is_infra_tab else limit
    stmt = stmt.order_by(occurred_at_expr.desc(), Post.detected_at.desc()).limit(fetch_limit)

    all_posts = (await session.exec(stmt)).all()

    # For infra tabs, filter by inferred role (covers posts where infra_role is NULL)
    if is_infra_tab:
        target_role = InfraRole.SEEKING if tab_value == "infra_seeking" else InfraRole.OFFERING
        posts = [
            p
            for p in all_posts
            if _infer_infra_role(p.post_type, p.infra_role, p.title, p.raw_text) == target_role
        ][:limit]
    else:
        posts = list(all_posts)

    now = datetime.now(UTC)
    items: list[dict[str, Any]] = []
    for post in posts:
        item: dict[str, Any] = {
            "post_id": post.id,
            "platform": post.platform,
            "occurred_at": (post.source_created_at or post.detected_at)
            .replace(tzinfo=UTC)
            .isoformat(),
            "detected_at": post.detected_at.replace(tzinfo=UTC).isoformat(),
            "title": post.effective_title,
            "snippet": _sanitize_text(post.raw_text or post.title, max_len=160),
            "source_url": _safe_url(post.source_url),
        }
        if tab_value in ("mentorship_camps", "mentorship_seekers"):
            item["camp_name"] = post.camp_name
            item["vibes"] = [v for v in (post.vibes or "").split("|") if v]
            item["skills"] = [v for v in (post.contribution_types or "").split("|") if v]
            if tab_value == "mentorship_seekers":
                item["seeker_intent"] = post.seeker_intent
        else:
            item["infra_categories"] = [v for v in (post.infra_categories or "").split("|") if v]
            item["quantity"] = post.quantity
            item["infra_offer_type"] = post.infra_offer_type
            item["pickup_location"] = post.pickup_location
            item["delivery_available"] = post.delivery_available
            item["dimensions"] = post.dimensions
            item["parts_included"] = post.parts_included
            item["setup_notes"] = post.setup_notes
            item["condition"] = post.condition
            item["dates_needed"] = post.dates_needed
        items.append(item)

    return {
        "items": items,
        "tab": tab_value,
        "count": len(items),
        "updated_at": now.isoformat(),
    }


@router.get("/api/listings")
async def community_api_listings() -> dict[str, Any]:
    """Return active listings for the browse pages (camps, seekers, gear)."""
    return await _run_with_db_retry("community_api_listings", _build_listings_payload)


async def _build_listings_payload(session: AsyncSession) -> dict[str, Any]:
    now = datetime.now(UTC)
    occurred_at_expr = func.coalesce(Post.source_created_at, Post.detected_at)

    try:
        camps_rows = (
            await session.exec(
                select(Post)
                .where(
                    Post.status == PostStatus.INDEXED,
                    Post.role == PostRole.CAMP,
                    Post.post_type == PostType.MENTORSHIP,
                )
                .order_by(occurred_at_expr.desc(), Post.detected_at.desc())
                .limit(60)
            )
        ).all()

        seekers_rows = (
            await session.exec(
                select(Post)
                .where(
                    Post.status == PostStatus.INDEXED,
                    Post.role == PostRole.SEEKER,
                    Post.post_type == PostType.MENTORSHIP,
                )
                .order_by(occurred_at_expr.desc(), Post.detected_at.desc())
                .limit(60)
            )
        ).all()

        active_infra_rows = (
            await session.exec(
                select(Post)
                .where(
                    Post.status.in_({PostStatus.INDEXED, PostStatus.NEEDS_REVIEW}),
                    Post.post_type == PostType.INFRASTRUCTURE,
                )
                .order_by(occurred_at_expr.desc(), Post.detected_at.desc())
                .limit(200)
            )
        ).all()

    except Exception as exc:
        log_exception(exc, "_build_listings_payload")
        return {
            "camps": [],
            "seekers": [],
            "gear_seeking": [],
            "gear_offering": [],
            "updated_at": now.isoformat(),
            "error": "Failed to load community listings.",
        }

    cross_platforms_by_post_id = await _load_cross_platforms_map(
        session,
        [post.id for post in camps_rows + seekers_rows + active_infra_rows],
    )

    async def _camp_card(post: Post) -> dict[str, Any]:
        occurred_at = (post.source_created_at or post.detected_at).replace(tzinfo=UTC).isoformat()
        return {
            "id": post.id,
            "name": post.camp_name or post.author_display_name or "Camp or Project",
            "title": post.effective_title,
            "vibes": post.vibes_list(),
            "contributions": post.contribution_types_list(),
            "snippet": _sanitize_text(post.raw_text or post.title or "", max_len=900),
            "platform": post.platform,
            "source_url": post.source_url or "",
            "occurred_at": occurred_at,
            "detected_at": post.detected_at.replace(tzinfo=UTC).isoformat(),
            "cross_platforms": cross_platforms_by_post_id.get(post.id, []),
        }

    async def _seeker_card(post: Post) -> dict[str, Any]:
        occurred_at = (post.source_created_at or post.detected_at).replace(tzinfo=UTC).isoformat()
        return {
            "id": post.id,
            "title": post.effective_title,
            "vibes": post.vibes_list(),
            "contributions": post.contribution_types_list(),
            "snippet": _sanitize_text(post.raw_text or post.title or "", max_len=900),
            "platform": post.platform,
            "source_url": post.source_url or "",
            "occurred_at": occurred_at,
            "detected_at": post.detected_at.replace(tzinfo=UTC).isoformat(),
            "cross_platforms": cross_platforms_by_post_id.get(post.id, []),
        }

    async def _gear_card(post: Post) -> dict[str, Any]:
        occurred_at = (post.source_created_at or post.detected_at).replace(tzinfo=UTC).isoformat()
        detail_parts = []
        if post.infra_offer_type:
            detail_parts.append(f"Offer: {post.infra_offer_type}")
        if post.quantity:
            detail_parts.append(f"Qty: {post.quantity}")
        if post.pickup_location:
            detail_parts.append(f"Pickup: {post.pickup_location}")
        if post.delivery_available is not None:
            detail_parts.append(f"Delivery: {'yes' if post.delivery_available else 'no'}")
        if post.dimensions:
            detail_parts.append(f"Size: {post.dimensions}")
        if post.parts_included:
            detail_parts.append(f"Parts: {post.parts_included}")
        if post.setup_notes:
            detail_parts.append(f"Setup: {post.setup_notes}")
        return {
            "id": post.id,
            "infra_role": post.infra_role or "",
            "title": post.effective_title,
            "categories": _split_pipe_values(post.infra_categories),
            "quantity": post.quantity or "",
            "infra_offer_type": post.infra_offer_type or "",
            "pickup_location": post.pickup_location or "",
            "delivery_available": post.delivery_available,
            "dimensions": post.dimensions or "",
            "parts_included": post.parts_included or "",
            "setup_notes": post.setup_notes or "",
            "condition": post.condition or "",
            "snippet": _sanitize_text(post.raw_text or post.title or "", max_len=900),
            "platform": post.platform,
            "source_url": post.source_url or "",
            "occurred_at": occurred_at,
            "detected_at": post.detected_at.replace(tzinfo=UTC).isoformat(),
            "cross_platforms": cross_platforms_by_post_id.get(post.id, []),
            "detail_line": " · ".join(detail_parts),
        }

    gear_seeking_rows: list[Post] = []
    gear_offering_rows: list[Post] = []
    for post in active_infra_rows:
        effective_infra_role = _infer_infra_role(
            post.post_type,
            post.infra_role,
            post.title,
            post.raw_text,
        )
        if effective_infra_role == InfraRole.SEEKING and len(gear_seeking_rows) < 60:
            gear_seeking_rows.append(post)
        elif effective_infra_role == InfraRole.OFFERING and len(gear_offering_rows) < 60:
            gear_offering_rows.append(post)
        if len(gear_seeking_rows) >= 60 and len(gear_offering_rows) >= 60:
            break

    return {
        "camps": [await _camp_card(p) for p in camps_rows],
        "seekers": [await _seeker_card(p) for p in seekers_rows],
        "gear_seeking": [await _gear_card(p) for p in gear_seeking_rows],
        "gear_offering": [await _gear_card(p) for p in gear_offering_rows],
        "updated_at": now.isoformat(),
    }


async def _build_matches_payload(
    session: AsyncSession,
    *,
    status: str,
    days: str,
    limit: int,
) -> dict[str, Any]:
    now = datetime.now(UTC).replace(tzinfo=None)
    allowed_statuses = {
        "all",
        MatchStatus.PROPOSED,
        MatchStatus.APPROVED,
        MatchStatus.INTRO_SENT,
        MatchStatus.CONVERSATION_STARTED,
        MatchStatus.ACCEPTED_PENDING,
        MatchStatus.ONBOARDED,
        MatchStatus.DECLINED,
        MatchStatus.CLOSED_STALE,
    }
    window_days = {"7": 7, "30": 30, "90": 90, "all": None}

    status_value = status if status in allowed_statuses else "all"
    days_value = days if days in window_days else "30"
    since = (
        None if window_days[days_value] is None else now - timedelta(days=window_days[days_value])
    )

    where_clauses = []
    if status_value != "all":
        where_clauses.append(Match.status == status_value)
    if since is not None:
        where_clauses.append(Match.created_at >= since)

    by_status_stmt = select(Match.status, func.count()).group_by(Match.status)
    if where_clauses:
        by_status_stmt = by_status_stmt.where(*where_clauses)
    by_status_rows = (await session.exec(by_status_stmt)).all()
    by_status = {status_name: count for status_name, count in by_status_rows}
    total = sum(count for _status_name, count in by_status_rows)

    seeker_post = aliased(Post)
    camp_post = aliased(Post)
    matches_stmt = (
        select(Match, seeker_post, camp_post)
        .join(seeker_post, Match.seeker_post_id == seeker_post.id)
        .join(camp_post, Match.camp_post_id == camp_post.id)
        .order_by(Match.created_at.desc())
        .limit(limit)
    )
    if where_clauses:
        matches_stmt = matches_stmt.where(*where_clauses)

    match_rows = (await session.exec(matches_stmt)).all()

    rows: list[dict[str, Any]] = []
    for match, seeker, camp in match_rows:
        overlap = sorted(
            set(seeker.contribution_types_list()).intersection(camp.contribution_types_list())
        )
        match_reason = _build_match_reason(match, seeker, camp)
        rows.append(
            {
                "match_id": match.id,
                "created_at": match.created_at.replace(tzinfo=UTC).isoformat(),
                "status": match.status,
                "score": round(match.score, 3),
                "confidence": round(match.confidence, 3) if match.confidence is not None else None,
                "seeker_platform": seeker.platform,
                "camp_platform": camp.platform,
                "seeker_summary": _sanitize_text(seeker.raw_text or seeker.title, max_len=120),
                "camp_summary": _sanitize_text(camp.raw_text or camp.title, max_len=120),
                "shared_signals": ", ".join(overlap[:3]) if overlap else "none",
                "match_reason": match_reason,
                "seeker_source_url": seeker.source_url,
                "camp_source_url": camp.source_url,
            }
        )

    return {
        "matched": rows,
        "summary": {
            "total": total,
            "by_status": by_status,
        },
        "updated_at": now.replace(tzinfo=UTC).isoformat(),
    }


async def build_public_community_payload(session: AsyncSession) -> dict[str, Any]:
    now = datetime.now(UTC)
    now_naive = now.replace(tzinfo=None)
    seven_days_ago = now_naive - timedelta(days=7)

    intro_terminal = {
        MatchStatus.INTRO_SENT,
        MatchStatus.CONVERSATION_STARTED,
        MatchStatus.ACCEPTED_PENDING,
        MatchStatus.ONBOARDED,
    }
    conversation_terminal = {
        MatchStatus.CONVERSATION_STARTED,
        MatchStatus.ACCEPTED_PENDING,
        MatchStatus.ONBOARDED,
    }

    def _count_when(condition: Any) -> Any:
        return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)

    (
        total_ingested,
        indexed_count,
        ingested_7d,
        indexed_7d,
        needs_review_count,
        oldest_needs_review,
        structured_count,
        active_camps,
        active_seekers,
        active_infra_seeking,
        active_infra_offering,
        soft_matches_total,
        soft_matches_7d,
    ) = (
        await session.exec(
            select(
                func.count().label("total_ingested"),
                _count_when(Post.status == PostStatus.INDEXED).label("indexed_count"),
                _count_when(Post.detected_at >= seven_days_ago).label("ingested_7d"),
                _count_when(
                    and_(
                        Post.status == PostStatus.INDEXED,
                        Post.detected_at >= seven_days_ago,
                    )
                ).label("indexed_7d"),
                _count_when(Post.status == PostStatus.NEEDS_REVIEW).label("needs_review_count"),
                func.min(
                    case(
                        (Post.status == PostStatus.NEEDS_REVIEW, Post.detected_at),
                        else_=None,
                    )
                ).label("oldest_needs_review"),
                _count_when(Post.status != PostStatus.RAW).label("structured_count"),
                _count_when(
                    and_(
                        Post.status == PostStatus.INDEXED,
                        Post.role == PostRole.CAMP,
                    )
                ).label("active_camps"),
                _count_when(
                    and_(
                        Post.status == PostStatus.INDEXED,
                        Post.role == PostRole.SEEKER,
                    )
                ).label("active_seekers"),
                _count_when(
                    and_(
                        Post.status.in_({PostStatus.INDEXED, PostStatus.NEEDS_REVIEW}),
                        Post.post_type == PostType.INFRASTRUCTURE,
                        Post.infra_role == "seeking",
                    )
                ).label("active_infra_seeking"),
                _count_when(
                    and_(
                        Post.status.in_({PostStatus.INDEXED, PostStatus.NEEDS_REVIEW}),
                        Post.post_type == PostType.INFRASTRUCTURE,
                        Post.infra_role == "offering",
                    )
                ).label("active_infra_offering"),
                _count_when(Post.extraction_method == "keyword_soft").label("soft_matches_total"),
                _count_when(
                    and_(
                        Post.extraction_method == "keyword_soft",
                        Post.detected_at >= seven_days_ago,
                    )
                ).label("soft_matches_7d"),
            )
        )
    ).one()

    platform_rows = (
        await session.exec(
            select(Post.platform, Post.source_community, func.count()).group_by(
                Post.platform, Post.source_community
            )
        )
    ).all()

    (
        proposed_matches,
        intros_sent,
        matches_7d,
        intros_7d,
        conversation_started_total,
        onboarded_total,
    ) = (
        await session.exec(
            select(
                func.count().label("proposed_matches"),
                _count_when(Match.status.in_(intro_terminal)).label("intros_sent"),
                _count_when(Match.created_at >= seven_days_ago).label("matches_7d"),
                _count_when(
                    and_(
                        Match.intro_sent_at.is_not(None),
                        Match.intro_sent_at >= seven_days_ago,
                    )
                ).label("intros_7d"),
                _count_when(Match.status.in_(conversation_terminal)).label(
                    "conversation_started_total"
                ),
                _count_when(Match.status == MatchStatus.ONBOARDED).label("onboarded_total"),
            )
        )
    ).one()

    total_ingested = int(total_ingested or 0)
    indexed_count = int(indexed_count or 0)
    ingested_7d = int(ingested_7d or 0)
    indexed_7d = int(indexed_7d or 0)
    needs_review_count = int(needs_review_count or 0)
    structured_count = int(structured_count or 0)
    active_camps = int(active_camps or 0)
    active_seekers = int(active_seekers or 0)
    active_infra_seeking = int(active_infra_seeking or 0)
    active_infra_offering = int(active_infra_offering or 0)
    soft_matches_total = int(soft_matches_total or 0)
    soft_matches_7d = int(soft_matches_7d or 0)
    proposed_matches = int(proposed_matches or 0)
    intros_sent = int(intros_sent or 0)
    matches_7d = int(matches_7d or 0)
    intros_7d = int(intros_7d or 0)
    conversation_started_total = int(conversation_started_total or 0)
    onboarded_total = int(onboarded_total or 0)

    platform_breakdown = _platform_breakdown_from_rows(platform_rows, total_ingested)

    oldest_needs_review_age_hours: float | None = None
    if oldest_needs_review is not None:
        oldest_needs_review_age_hours = round(
            (now_naive - oldest_needs_review).total_seconds() / 3600,
            1,
        )

    intro_to_conversation_rate = (
        conversation_started_total / intros_sent if intros_sent > 0 else 0.0
    )
    conversation_to_onboarding_rate = (
        onboarded_total / conversation_started_total if conversation_started_total > 0 else 0.0
    )

    live_feed_posts = (
        await session.exec(
            select(Post).where(
                Post.status == PostStatus.NEEDS_REVIEW,
                Post.detected_at >= seven_days_ago,
            )
        )
    ).all()
    live_feed_matches = (
        await session.exec(
            select(Match).where(
                or_(
                    Match.created_at >= seven_days_ago,
                    Match.intro_sent_at >= seven_days_ago,
                )
            )
        )
    ).all()

    demand_rows = (
        await session.exec(
            select(
                Post.role,
                Post.status,
                Post.post_type,
                Post.contribution_types,
                Post.vibes,
                Post.infra_role,
                Post.infra_categories,
                Post.title,
                Post.raw_text,
            ).where(Post.status.in_({PostStatus.INDEXED, PostStatus.NEEDS_REVIEW}))
        )
    ).all()

    story_matches = (
        await session.exec(select(Match).order_by(Match.created_at.desc()).limit(200))
    ).all()
    story_post_ids = {m.seeker_post_id for m in story_matches}
    story_post_ids.update(m.camp_post_id for m in story_matches)
    story_posts: list[Post] = []
    if story_post_ids:
        story_posts.extend(
            (await session.exec(select(Post).where(Post.id.in_(story_post_ids)))).all()
        )
    story_posts.extend(
        (
            await session.exec(
                select(Post)
                .where(Post.status == PostStatus.INDEXED)
                .order_by(Post.detected_at.desc())
                .limit(50)
            )
        ).all()
    )
    deduped_story_posts = {post.id: post for post in story_posts}

    stories = _build_stories(list(deduped_story_posts.values()), story_matches)
    live_feed = _build_live_feed(live_feed_posts, live_feed_matches, seven_days_ago)
    demand = _build_demand_rows(demand_rows)
    active_infra_seeking, active_infra_offering = _count_active_infra_roles(demand_rows)
    feedback_url = _community_feedback_url()

    return {
        "summary": {
            "total_ingested": total_ingested,
            "indexed": indexed_count,
            "proposed_matches": proposed_matches,
            "intros_sent": intros_sent,
        },
        "weekly": {
            "ingested_7d": ingested_7d,
            "indexed_7d": indexed_7d,
            "matches_7d": matches_7d,
            "intros_7d": intros_7d,
        },
        "backlog": {
            "needs_review_count": needs_review_count,
            "soft_matches_count": soft_matches_total,
            "oldest_needs_review_age_hours": oldest_needs_review_age_hours,
        },
        "key_metrics": {
            "active_camps": active_camps,
            "active_seekers": active_seekers,
            "active_infra_seeking": active_infra_seeking,
            "active_infra_offering": active_infra_offering,
            "soft_matches_total": soft_matches_total,
            "soft_matches_7d": soft_matches_7d,
            "match_attempts_total": proposed_matches,
            "intro_sent_total": intros_sent,
            "conversation_started_total": conversation_started_total,
            "onboarded_total": onboarded_total,
            "intro_to_conversation_rate": round(intro_to_conversation_rate, 4),
            "conversation_to_onboarding_rate": round(conversation_to_onboarding_rate, 4),
        },
        "platform_breakdown": platform_breakdown,
        "platform_mix": platform_breakdown,
        "pipeline": [
            {"stage": "Seen", "count": total_ingested},
            {"stage": "Analyzed", "count": structured_count},
            {"stage": "Matched", "count": proposed_matches},
            {"stage": "Introduced", "count": intros_sent},
        ],
        "live_feed": live_feed,
        "demand": demand,
        "stories": stories,
        "cta": {"feedback_url": feedback_url},
        "updated_at": now.isoformat(),
    }


def _platform_breakdown(posts: list[Post]) -> list[dict[str, Any]]:
    platform_counts = Counter(p.platform for p in posts)
    total_ingested = len(posts)
    rows: list[dict[str, Any]] = []
    for platform, count in sorted(platform_counts.items(), key=lambda x: x[1], reverse=True):
        pct = round((count / total_ingested) * 100, 1) if total_ingested else 0.0
        rows.append({"platform": platform, "count": count, "pct": pct})
    return rows


def _platform_label(platform: str, source_community: str | None) -> str:
    if platform == "manual" and source_community == "intake_form":
        return "submission form"
    return platform


def _platform_breakdown_from_rows(
    rows: list[tuple[str, str | None, int]],
    total_ingested: int,
) -> list[dict[str, Any]]:
    aggregated: Counter[str] = Counter()
    for platform, source_community, count in rows:
        aggregated[_platform_label(platform, source_community)] += count

    formatted: list[dict[str, Any]] = []
    for platform, count in sorted(aggregated.items(), key=lambda x: x[1], reverse=True):
        pct = round((count / total_ingested) * 100, 1) if total_ingested else 0.0
        formatted.append({"platform": platform, "count": count, "pct": pct})
    return formatted


def _build_live_feed(
    posts: list[Post],
    matches: list[Match],
    since: datetime,
) -> list[dict[str, Any]]:
    feed: list[dict[str, Any]] = []

    for post in posts:
        effective_occurred_at = post.source_created_at or post.detected_at
        if effective_occurred_at < since or post.status != PostStatus.NEEDS_REVIEW:
            continue
        summary = _sanitize_text(post.raw_text or post.title, max_len=120)
        occurred_at = effective_occurred_at.replace(tzinfo=UTC).isoformat()
        feed.append(
            {
                "event_type": "post_needs_review",
                "occurred_at": occurred_at,
                "platform": post.platform,
                "summary": summary,
            }
        )

    for match in matches:
        if match.created_at >= since:
            feed.append(
                {
                    "event_type": f"match_{match.status}",
                    "occurred_at": match.created_at.replace(tzinfo=UTC).isoformat(),
                    "platform": match.intro_platform or "multi",
                    "summary": _match_summary(match),
                    "score": round(match.score, 3),
                    "confidence": (
                        round(match.confidence, 3) if match.confidence is not None else None
                    ),
                }
            )
        if match.intro_sent_at is not None and match.intro_sent_at >= since:
            feed.append(
                {
                    "event_type": "intro_sent",
                    "occurred_at": match.intro_sent_at.replace(tzinfo=UTC).isoformat(),
                    "platform": match.intro_platform or "multi",
                    "summary": "Human-facilitated introduction sent.",
                    "score": round(match.score, 3),
                    "confidence": (
                        round(match.confidence, 3) if match.confidence is not None else None
                    ),
                }
            )

    feed.sort(key=lambda row: row["occurred_at"], reverse=True)
    return feed[:20]


def _split_pipe_values(raw: str | None) -> list[str]:
    return [value for value in (raw or "").split("|") if value]


def _infer_infra_role(
    post_type: str | None,
    infra_role: str | None,
    title: str | None,
    raw_text: str | None,
) -> str | None:
    if post_type != PostType.INFRASTRUCTURE:
        return None
    if is_vehicle_sale_or_rental_listing(title or "", raw_text or ""):
        return None
    if infra_role in {"seeking", "offering"}:
        return infra_role

    kw_result = keyword_filter(title or "", raw_text or "")
    if kw_result.post_type == PostType.INFRASTRUCTURE:
        return kw_result.infra_role
    return None


def _count_active_infra_roles(
    rows: list[tuple[str | None, str, str | None, str, str, str | None, str, str, str]],
) -> tuple[int, int]:
    active_infra_seeking = 0
    active_infra_offering = 0

    for (
        _role,
        _status,
        post_type,
        _contribution_types,
        _vibes,
        infra_role,
        _infra_categories,
        title,
        raw_text,
    ) in rows:
        effective_infra_role = _infer_infra_role(post_type, infra_role, title, raw_text)
        if effective_infra_role == "seeking":
            active_infra_seeking += 1
        elif effective_infra_role == "offering":
            active_infra_offering += 1

    return active_infra_seeking, active_infra_offering


def _build_demand_rows(
    rows: list[tuple[str | None, str, str | None, str, str, str | None, str, str, str]],
) -> dict[str, list[dict[str, Any]]]:
    contrib_counts: Counter[str] = Counter()
    vibe_counts: Counter[str] = Counter()
    camp_contrib_counts: Counter[str] = Counter()
    seeker_contrib_counts: Counter[str] = Counter()
    camp_vibe_counts: Counter[str] = Counter()
    seeker_vibe_counts: Counter[str] = Counter()
    infra_demand_counts: Counter[str] = Counter()
    infra_supply_counts: Counter[str] = Counter()

    for (
        role,
        _status,
        post_type,
        contribution_types,
        vibes,
        infra_role,
        infra_categories,
        title,
        raw_text,
    ) in rows:
        contribution_values = _split_pipe_values(contribution_types)
        vibe_values = _split_pipe_values(vibes)
        infra_values = _split_pipe_values(infra_categories)

        if role == PostRole.SEEKER:
            contrib_counts.update(contribution_values)
            vibe_counts.update(vibe_values)

        if post_type == PostType.INFRASTRUCTURE:
            effective_infra_role = _infer_infra_role(post_type, infra_role, title, raw_text)
            if effective_infra_role == "seeking":
                infra_demand_counts.update(infra_values)
            elif effective_infra_role == "offering":
                infra_supply_counts.update(infra_values)
            continue

        if post_type not in {None, PostType.MENTORSHIP}:
            continue

        if role == PostRole.CAMP:
            camp_contrib_counts.update(contribution_values)
            camp_vibe_counts.update(vibe_values)
        elif role == PostRole.SEEKER:
            seeker_contrib_counts.update(contribution_values)
            seeker_vibe_counts.update(vibe_values)

    top_contrib = [{"name": name, "count": count} for name, count in contrib_counts.most_common(10)]
    top_vibes = [{"name": name, "count": count} for name, count in vibe_counts.most_common(10)]

    return {
        "top_contribution_types": top_contrib,
        "top_vibes": top_vibes,
        "most_sought_skills": _build_order_book_rows(camp_contrib_counts, seeker_contrib_counts),
        "most_sought_vibes": _build_order_book_rows(camp_vibe_counts, seeker_vibe_counts),
        "infra_exchange": _build_order_book_rows(infra_demand_counts, infra_supply_counts),
    }


def _build_order_book_rows(
    demand_counts: Counter[str],
    supply_counts: Counter[str],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows = []
    for name, demand_count in demand_counts.items():
        supply_count = supply_counts.get(name, 0)
        net_gap = demand_count - supply_count
        fill_ratio = (supply_count / demand_count) if demand_count > 0 else 0.0
        rows.append(
            {
                "name": name,
                "demand_count": demand_count,
                "supply_count": supply_count,
                "net_gap": net_gap,
                "fill_ratio": round(fill_ratio, 3),
            }
        )

    rows.sort(key=lambda row: (-row["demand_count"], -row["net_gap"], row["name"]))
    return rows[:limit]


def _build_stories(posts: list[Post], matches: list[Match]) -> list[dict[str, str]]:
    post_by_id = {p.id: p for p in posts}
    story_rows: list[dict[str, str]] = []
    sorted_matches = sorted(matches, key=lambda m: m.created_at, reverse=True)

    for idx, match in enumerate(sorted_matches, start=1):
        seeker = post_by_id.get(match.seeker_post_id)
        camp = post_by_id.get(match.camp_post_id)
        if seeker is None or camp is None:
            continue

        match_reason = _build_match_reason(match, seeker, camp)
        story_rows.append(
            {
                "title": f"Potential Connection {idx}",
                "problem": _sanitize_text(seeker.raw_text or seeker.title, max_len=140),
                "intervention": f"Potential fit: {match_reason}",
                "outcome": _outcome_label(match.status),
                "confidence_note": _confidence_note(match),
            }
        )
        if len(story_rows) >= 3:
            break

    if story_rows:
        return story_rows

    indexed_posts = [p for p in posts if p.status == PostStatus.INDEXED]
    for idx, post in enumerate(
        sorted(indexed_posts, key=lambda p: p.detected_at, reverse=True),
        start=1,
    ):
        story_rows.append(
            {
                "title": f"Active Signal {idx}",
                "problem": _sanitize_text(post.raw_text or post.title, max_len=140),
                "intervention": "Analyzed for alignment with the builder community.",
                "outcome": "Awaiting a likely counterpart.",
                "confidence_note": "Anonymized community signal.",
            }
        )
        if len(story_rows) >= 3:
            break

    return story_rows


def _match_summary(match: Match) -> str:
    if match.status == MatchStatus.PROPOSED:
        return "Potential match added to review queue."
    if match.status == MatchStatus.APPROVED:
        return "Potential match approved for intro."
    if match.status in {
        MatchStatus.INTRO_SENT,
        MatchStatus.CONVERSATION_STARTED,
        MatchStatus.ACCEPTED_PENDING,
        MatchStatus.ONBOARDED,
    }:
        return "Potential match moved toward direct connection."
    if match.status == MatchStatus.DECLINED:
        return "Potential match declined after review."
    return "Potential match updated."


def _outcome_label(status: str) -> str:
    if status == MatchStatus.INTRO_SENT:
        return "Handshake facilitated."
    if status == MatchStatus.CONVERSATION_STARTED:
        return "They’re talking."
    if status in {MatchStatus.ACCEPTED_PENDING, MatchStatus.ONBOARDED}:
        return "Aligned for the playa."
    if status == MatchStatus.APPROVED:
        return "Verified for intro."
    return "Awaiting human review."


def _confidence_note(match: Match) -> str:
    if match.confidence is not None:
        return f"Deterministic match score: {round(match.confidence * 100)}%."
    if match.score:
        return f"Deterministic match score: {round(match.score * 100)}%."
    return "Candidate generated from extracted tags."


def _build_match_reason(match: Match, seeker: Post, camp: Post) -> str:
    reasons: list[str] = []
    shared_contrib = sorted(
        set(seeker.contribution_types_list()).intersection(camp.contribution_types_list())
    )
    shared_vibes = sorted(set(seeker.vibes_list()).intersection(camp.vibes_list()))

    if shared_contrib:
        reasons.append(f"Both mention {_human_list(shared_contrib[:3])} as contribution styles.")
    else:
        reasons.append("No shared contribution tags were extracted.")

    if shared_vibes:
        reasons.append(f"They share a {_human_list(shared_vibes[:2])} vibe.")

    if seeker.year is not None and camp.year is not None and seeker.year == camp.year:
        reasons.append(f"Both are tagged for Burning Man {seeker.year}.")
    elif seeker.year is not None and camp.year is not None and seeker.year != camp.year:
        reasons.append("The extracted burn years do not match.")

    breakdown = match.score_breakdown_dict() or {}
    recency = _parse_float(breakdown.get("recency"))
    if recency is not None:
        if recency >= 0.8:
            reasons.append("Both posts are recent.")
        elif recency >= 0.5:
            reasons.append("At least one post is moderately recent.")
        else:
            reasons.append("One or both posts may be older.")

    return " ".join(reasons[:3])


def _human_list(items: list[str]) -> str:
    human = [_human_token(item) for item in items if item]
    if not human:
        return "related signals"
    if len(human) == 1:
        return human[0]
    if len(human) == 2:
        return f"{human[0]} and {human[1]}"
    return f"{', '.join(human[:-1])}, and {human[-1]}"


def _human_token(value: str) -> str:
    return value.replace("_", " ").strip()


def _parse_float(value: Any) -> float | None:
    if isinstance(value, (float, int)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _safe_url(url: str | None) -> str | None:
    """Return url only if it uses http or https; otherwise return None."""
    if not url:
        return None
    parsed = urlparse(url)
    return url if parsed.scheme in ("http", "https") else None


def _sanitize_text(text: str, *, max_len: int = 160) -> str:
    compact = " ".join((text or "").split())
    compact = re.sub(r"https?://\S+", "[link]", compact)
    compact = re.sub(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b", "[contact]", compact)
    compact = re.sub(r"\bu/[A-Za-z0-9_-]+\b", "u/[redacted]", compact)
    compact = re.sub(r"@[A-Za-z0-9_]{2,}", "@[redacted]", compact)
    compact = re.sub(r"\bt2_[A-Za-z0-9_]+\b", "[user]", compact)
    if len(compact) > max_len:
        return compact[: max_len - 1].rstrip() + "…"
    return compact
