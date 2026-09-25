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

## Images

Two ways, both plain markdown.

**Hosted elsewhere** — paste any public URL. Nothing to set up:

```markdown
![Danny's lineup](https://i.imgur.com/example.png)
```

**Hosted here** — drop the file in `blogs/images/` and reference it relative
to the site root:

```markdown
![Week 2 carnage](images/danny-week2.png)

*Danny, moments before the 104.58.*
```

`sync.sh` copies `blogs/images/` into the web root on every run, so publishing
a picture is the same as publishing a post: commit it, push, and the Pi picks
it up. Italic text on the line after an image is styled as a caption.

Images are capped at the panel width automatically, so a 4000px phone photo
is fine. They do live in git, though, so resize anything enormous before
committing — the whole league is only ~190 KB of JSON and it'd be a shame for
the repo to be 90% screenshots.

Note the path is `images/...`, not `blogs/images/...` — the file is served
from the site root, not from the folder it's authored in.
