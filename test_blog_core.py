"""
Tests for blog_core.py. No network, no filesystem except a tmpdir.

Every case here is a rule the pipeline has to hold, not a restatement of the
code: the season/week types index.html depends on, the ordering that survives
a git clone, and the "one bad post must not break the sync" guarantee.
"""

import os
import shutil
import tempfile
import unittest

import blog_core


class FrontMatter(unittest.TestCase):
    def test_no_front_matter_is_all_body(self):
        front, body = blog_core.split_front_matter("Just a post.")
        self.assertEqual(front, {})
        self.assertEqual(body, "Just a post.")

    def test_keys_are_lowercased_and_quotes_stripped(self):
        front, body = blog_core.split_front_matter('---\nTitle: "Big Week"\n---\nbody')
        self.assertEqual(front["title"], "Big Week")
        self.assertEqual(body, "body")

    def test_colons_in_value_survive(self):
        front, _ = blog_core.split_front_matter("---\ntitle: Week 3: The Reckoning\n---\nb")
        self.assertEqual(front["title"], "Week 3: The Reckoning")

    def test_unterminated_front_matter_raises(self):
        # Silently treating the whole file as front matter would publish an
        # empty post, which is worse than a warning.
        with self.assertRaises(blog_core.BlogError):
            blog_core.split_front_matter("---\ntitle: Oops\nbody with no closing delim")


class ParsePost(unittest.TestCase):
    def test_season_is_a_string_and_week_is_an_int(self):
        # index.html compares season to a <select> value (string) and sorts
        # weeks numerically. Swapping these silently empties the filters.
        post = blog_core.parse_post("2026-w03-recap.md", "body")
        self.assertIsInstance(post["season"], str)
        self.assertIsInstance(post["week"], int)
        self.assertEqual((post["season"], post["week"]), ("2026", 3))

    def test_filename_variants_all_parse(self):
        for name in ("2026-w01-x.md", "2026-week-1.md", "2026_w1.md", "2026w01.md"):
            post = blog_core.parse_post(name, "body")
            self.assertEqual((post["season"], post["week"]), ("2026", 1), name)

    def test_front_matter_beats_filename(self):
        post = blog_core.parse_post("2026-w03-recap.md", "---\nseason: 2025\nweek: 9\n---\nbody")
        self.assertEqual((post["season"], post["week"]), ("2025", 9))

    def test_missing_season_and_week_raises(self):
        with self.assertRaises(blog_core.BlogError):
            blog_core.parse_post("some-recap.md", "body")

    def test_title_falls_back_to_filename(self):
        post = blog_core.parse_post("2026-w03-the-big-one.md", "body")
        self.assertEqual(post["title"], "2026 w03 the big one")

    def test_empty_body_raises(self):
        with self.assertRaises(blog_core.BlogError):
            blog_core.parse_post("2026-w03.md", "---\ntitle: T\n---\n   ")

    def test_week_out_of_range_raises(self):
        with self.assertRaises(blog_core.BlogError):
            blog_core.parse_post("2026-w99.md", "body")

    def test_content_stays_raw_markdown(self):
        # marked.js renders client-side; the pipeline must not pre-render,
        # or the page would escape the resulting HTML as literal text.
        post = blog_core.parse_post("2026-w01.md", "# Heading\n\n**bold**")
        self.assertEqual(post["content"], "# Heading\n\n**bold**")

    def test_author_defaults(self):
        self.assertEqual(blog_core.parse_post("2026-w01.md", "b")["author"], "Commissioner")

    def test_explicit_date_wins_over_synthesis(self):
        post = blog_core.parse_post("2026-w01.md", "---\ndate: 2026-09-16\n---\nb")
        self.assertEqual(post["created_at"], blog_core.parse_date("2026-09-16"))

    def test_bad_date_raises(self):
        with self.assertRaises(blog_core.BlogError):
            blog_core.parse_post("2026-w01.md", "---\ndate: last tuesday\n---\nb")


class Ordering(unittest.TestCase):
    def test_synthesized_timestamps_order_by_season_then_week(self):
        # This is the property that matters: file mtime can't provide it
        # after a git clone, which stamps every post with the checkout time.
        stamps = [blog_core.synthesize_created_at(s, w)
                  for s in (2025, 2026) for w in (1, 5, 17)]
        self.assertEqual(stamps, sorted(stamps))

    def test_synthesis_is_deterministic(self):
        self.assertEqual(blog_core.synthesize_created_at(2026, 4),
                         blog_core.synthesize_created_at(2026, 4))


class Featured(unittest.TestCase):
    def _posts(self, *specs):
        return [blog_core.parse_post(f"{s}-w{w:02d}.md", "body") for s, w in specs]

    def test_newest_post_is_featured_with_no_flags(self):
        posts = blog_core.mark_featured(self._posts((2026, 1), (2026, 3), (2025, 17)))
        featured = [p for p in posts if p["is_current"]]
        self.assertEqual(len(featured), 1)
        self.assertEqual((featured[0]["season"], featured[0]["week"]), ("2026", 3))

    def test_explicit_featured_pins_an_older_post(self):
        posts = self._posts((2026, 3))
        posts.append(blog_core.parse_post("2025-w17.md", "---\nfeatured: true\n---\nbody"))
        blog_core.mark_featured(posts)
        featured = [p for p in posts if p["is_current"]]
        self.assertEqual(len(featured), 1)
        self.assertEqual(featured[0]["season"], "2025")

    def test_exactly_one_is_featured_when_several_pin_themselves(self):
        posts = [blog_core.parse_post(f"2026-w{w:02d}.md", "---\nfeatured: yes\n---\nb")
                 for w in (1, 2, 3)]
        blog_core.mark_featured(posts)
        featured = [p for p in posts if p["is_current"]]
        self.assertEqual(len(featured), 1)
        self.assertEqual(featured[0]["week"], 3)

    def test_empty_set_is_fine(self):
        self.assertEqual(blog_core.mark_featured([]), [])


class LoadPosts(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def write(self, name, text="body"):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_missing_directory_is_empty_not_an_error(self):
        self.assertEqual(blog_core.load_posts(os.path.join(self.dir, "nope")), [])

    def test_underscore_and_dot_files_are_skipped(self):
        self.write("_README.md")
        self.write("_draft-2026-w05.md")
        self.write(".hidden.md")
        self.write("2026-w01.md")
        posts = blog_core.load_posts(self.dir)
        self.assertEqual([p["id"] for p in posts], ["2026-w01"])

    def test_non_markdown_is_ignored(self):
        self.write("notes.txt")
        self.write("2026-w01.md")
        self.assertEqual(len(blog_core.load_posts(self.dir)), 1)

    def test_a_broken_post_is_skipped_not_fatal(self):
        # The week's scores must publish even if a recap has a typo in it.
        self.write("2026-w01.md")
        self.write("no-season-here.md")
        posts = blog_core.load_posts(self.dir)
        self.assertEqual([p["id"] for p in posts], ["2026-w01"])

    def test_returned_newest_first(self):
        for week in (1, 2, 3):
            self.write(f"2026-w{week:02d}.md")
        posts = blog_core.load_posts(self.dir)
        self.assertEqual([p["week"] for p in posts], [3, 2, 1])

    def test_ids_are_unique_so_firestore_cannot_overwrite(self):
        self.write("2026-w01.md")
        self.write("2026-w01.markdown")
        with self.assertRaises(blog_core.BlogError):
            blog_core.load_posts(self.dir)

    def test_every_field_index_html_dereferences_is_present(self):
        self.write("2026-w01.md")
        post = blog_core.load_posts(self.dir)[0]
        for field in ("id", "title", "season", "week", "content",
                      "author", "created_at", "is_current"):
            self.assertIn(field, post)
            self.assertIsNotNone(post[field], field)


if __name__ == "__main__":
    unittest.main()
