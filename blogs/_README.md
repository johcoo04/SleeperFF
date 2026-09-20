# Writing a post

Drop a markdown file in this folder and run the sync. That's the whole
workflow — `sync_pipeline.py` reads every `.md` here, bundles the posts into
`data/league.json`, and writes them to Firestore too, so the Weekly Blog tab
works from either source.

Files starting with `_` (like this one) or `.` are **ignored**, so use
`_draft-week-5.md` for something you're not ready to publish.

## Format

```markdown
---
title: Opening Week Shakeout
season: 2026
week: 1
author: Commissioner
date: 2026-09-16
featured: true
---

Your post, in **markdown**. Headings, lists, links, and tables all work —
index.html renders it with marked.js.
```

Every field is optional except the body:

| Field | Default |
|---|---|
| `title` | the filename, dashes turned into spaces |
| `season` / `week` | read from the filename — `2026-w01-recap.md` |
| `author` | `Commissioner` |
| `date` | derived from season + week, which is all it's used for (sort order) |
| `featured` | the newest post is featured automatically |

So the shortest possible post is a file named `2026-w03-recap.md` containing
nothing but text.

## Gotchas

- **`featured` rarely needs setting.** The newest post is featured on its own,
  so a manual flag is one more thing to remember to clear each week. Set it
  only to pin something — a season wrap-up you want to stay up.
- **A broken post is skipped, not fatal.** The sync warns and keeps going, so
  a typo here can't stop the week's scores from publishing. Check the cron log
  (`~/sync.log` on the Pi) if a post doesn't show up.
- **Deleting a file unpublishes the post**, from the bundle and from Firestore
  both. The folder is the source of truth.
- **The filename becomes the post's id.** Renaming a file and re-syncing
  creates a new post and removes the old one.
