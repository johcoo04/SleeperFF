"""
blog_core.py — turn a folder of markdown files into blog post documents.
========================================================================

The Weekly Blog tab had no authoring path at all: nothing wrote posts, so it
rendered "No featured post yet." forever. Posts are now markdown files in
`blogs/`, read by sync_pipeline.py and shipped to both sinks.

Markdown files rather than the Firebase console, because:

  - they version-control alongside the league data,
  - they survive `--skip-firestore` (the console path needs Firestore alive
    AND a write rule, and firestore.rules currently denies every write),
  - and re-rendering a post is a sync run, not a hand-edit in a browser.

`content` ships as RAW markdown. index.html already loads marked.js and
parses client-side, so nothing here renders HTML and the pipeline needs no
markdown dependency — the base install stays `requests` alone.

Post shape, matching exactly what index.html dereferences:

    {"id", "title", "season" (str), "week" (int), "content",
     "author", "created_at" (ms int), "is_current" (bool)}

`season` is a string and `week` is an int on purpose: the archive filters
compare season against a <select> value (string) and sort weeks numerically.
Getting those backwards silently empties the filters.
"""

import os
import re
from datetime import datetime, timezone

DEFAULT_BLOGS_DIR = "blogs"
DEFAULT_AUTHOR = "Commissioner"

# 2026-w01-anything.md / 2026-week-1.md / 2026_w1.md
FILENAME_PATTERN = re.compile(r"(?P<season>\d{4})[-_]?w(?:eek)?[-_]?(?P<week>\d{1,2})", re.IGNORECASE)

FRONT_MATTER_DELIM = "---"
TRUTHY = {"true", "yes", "1", "on"}


class BlogError(ValueError):
    """A post that can't be parsed. Carries the filename for the warning."""


# ---------------------------------------------------------------------------
# Front matter
# ---------------------------------------------------------------------------

def split_front_matter(text):
    """
    Return (front_matter_dict, body). A file with no leading `---` block is
    all body — that's not an error, it just has to get its season and week
    from the filename instead.

    Deliberately not YAML: the base install is `requests` alone and a post's
    front matter is half a dozen flat `key: value` lines. Values are strings;
    typing happens in parse_post where the target type is known.
    """
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != FRONT_MATTER_DELIM:
        return {}, text.strip()

    front = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == FRONT_MATTER_DELIM:
            return front, "\n".join(lines[index + 1:]).strip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        front[key.strip().lower()] = value.strip().strip('"').strip("'")

    # Opened a front matter block and never closed it — almost certainly a
    # typo'd delimiter, and treating the whole post as front matter would
    # silently publish an empty body.
    raise BlogError("front matter opened with '---' but never closed")


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

def synthesize_created_at(season, week):
    """
    A deterministic ms timestamp from (season, week), used when a post
    declares no `date:`.

    File mtime is the obvious fallback and the wrong one: `git clone` on the
    Pi stamps every file with the checkout time, which would flatten the
    whole archive to "just now" and scramble its order. Season + week is the
    real ordering and it's already in the post.

    Anchored to Sept 1 of the season because created_at is only ever a sort
    key — index.html never displays it.
    """
    return int((datetime(int(season), 9, 1, tzinfo=timezone.utc).timestamp()
                + (int(week) - 1) * 7 * 86400) * 1000)


def parse_date(value):
    """`date: 2026-09-16` (optionally with a time) -> ms epoch, or None."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return int(parsed.timestamp() * 1000)
    raise BlogError(f"unparseable date {value!r} (expected YYYY-MM-DD)")


# ---------------------------------------------------------------------------
# One post
# ---------------------------------------------------------------------------

def parse_post(filename, text):
    """
    Parse one markdown file into a post document. Raises BlogError with a
    reason the caller can print; callers skip the file rather than failing
    the whole sync, so one bad post can't block a week's data.

    Season and week come from front matter when present and the filename
    otherwise, so `2026-w03-title.md` needs no front matter beyond a title.
    """
    stem = os.path.splitext(os.path.basename(filename))[0]
    front, body = split_front_matter(text)

    season = front.get("season")
    week = front.get("week")
    if season is None or week is None:
        match = FILENAME_PATTERN.search(stem)
        if not match:
            raise BlogError(
                "no season/week — add 'season:' and 'week:' to the front matter, "
                "or name the file like 2026-w03-recap.md")
        season = season if season is not None else match.group("season")
        week = week if week is not None else match.group("week")

    try:
        week = int(str(week).lstrip("0") or "0")
        season = str(int(season))
    except ValueError:
        raise BlogError(f"season/week must be numeric, got season={season!r} week={week!r}")
    if not 1 <= week <= 22:
        raise BlogError(f"week {week} is outside 1-22")

    title = front.get("title") or stem.replace("-", " ").replace("_", " ").strip()
    if not title:
        raise BlogError("no title, and the filename doesn't supply one")
    if not body:
        raise BlogError("post body is empty")

    created_at = parse_date(front.get("date")) or synthesize_created_at(season, week)

    return {
        "id": stem,
        "title": title,
        "season": season,
        "week": week,
        "content": body,
        "author": front.get("author") or DEFAULT_AUTHOR,
        "created_at": created_at,
        # Set by mark_featured once the whole set is known; an explicit
        # `featured: true` pins it to this post instead.
        "is_current": str(front.get("featured", "")).strip().lower() in TRUTHY,
    }


def mark_featured(posts):
    """
    Exactly one post carries is_current.

    Deriving "newest" beats a manual flag per file: the flag would need
    clearing on last week's post every single week, and forgetting leaves a
    stale recap featured forever. An explicit `featured: true` still wins,
    for pinning a season wrap-up. If several pin themselves, the newest of
    those wins so the page never shows an older post than it has.
    """
    if not posts:
        return posts
    order = lambda p: (int(p["season"]), p["week"], p["id"])
    pinned = [p for p in posts if p["is_current"]]
    featured = max(pinned or posts, key=order)
    for post in posts:
        post["is_current"] = post is featured
    return posts


# ---------------------------------------------------------------------------
# A folder of posts
# ---------------------------------------------------------------------------

def load_posts(blogs_dir=DEFAULT_BLOGS_DIR, verbose=False):
    """
    Read every .md/.markdown file in blogs_dir. Returns newest-first.

    Files starting with `_` or `.` are ignored, so `_draft-week-4.md` and
    `_README.md` sit in the folder without publishing.

    A missing folder is an empty list, not an error — a deployment with no
    blog is a supported state and the tab handles it. A malformed post is
    warned about and skipped, because a cron sync must still deliver the
    week's scores even if a recap has a typo in it.
    """
    if not os.path.isdir(blogs_dir):
        if verbose:
            print(f"   No blogs directory at '{blogs_dir}' — bundling zero posts.")
        return []

    posts = []
    for name in sorted(os.listdir(blogs_dir)):
        # A leading underscore means "not a post": drafts you're still writing,
        # and the folder's own README, which would otherwise be parsed as a
        # post and warn on every sync forever.
        if not name.lower().endswith((".md", ".markdown")) or name[0] in "._":
            continue
        path = os.path.join(blogs_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                posts.append(parse_post(name, f.read()))
        except (BlogError, OSError) as exc:
            print(f"   WARNING: skipping blog post '{name}': {exc}")

    duplicates = {p["id"] for p in posts if sum(q["id"] == p["id"] for q in posts) > 1}
    if duplicates:
        # Ids become Firestore document ids, so a collision would have one
        # post silently overwrite another.
        raise BlogError(f"duplicate post id(s): {', '.join(sorted(duplicates))}")

    mark_featured(posts)
    posts.sort(key=lambda p: -p["created_at"])
    if verbose and posts:
        featured = next((p["id"] for p in posts if p["is_current"]), None)
        print(f"   Loaded {len(posts)} blog post(s) from '{blogs_dir}' (featured: {featured}).")
    return posts
