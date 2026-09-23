"""
Tests for league_core — the shared identity + scoring rules.

These exist because the three bugs these rules encode were all found in
production output, and two of them cannot be caught by real data anymore:

  * The username collision is invisible now that everything keys on owner_id.
  * No tie has occurred in 51 weeks of real league play, so the tie-breaker
    averaging path has no live coverage at all.

Run: python -m unittest test_league_core -v      (no network required)
"""

import unittest

import league_core as core


class TestRankingWithTies(unittest.TestCase):
    def test_competition_ranking_jumps_by_tie_count(self):
        ranked = core.rank_week_with_ties([
            {"score": 100.0}, {"score": 90.0}, {"score": 90.0}, {"score": 80.0},
        ])
        self.assertEqual([e["rank"] for e in ranked], [1, 2, 2, 4])

    def test_input_need_not_be_sorted_and_is_not_mutated(self):
        original = [{"score": 80.0}, {"score": 100.0}, {"score": 90.0}]
        ranked = core.rank_week_with_ties(original)
        self.assertEqual([e["score"] for e in ranked], [100.0, 90.0, 80.0])
        self.assertEqual([e["rank"] for e in ranked], [1, 2, 3])
        self.assertTrue(all("rank" not in e for e in original),
                        "caller's dicts must not be mutated")

    def test_all_tied_share_rank_one(self):
        ranked = core.rank_week_with_ties([{"score": 0.0} for _ in range(6)])
        self.assertEqual([e["rank"] for e in ranked], [1] * 6)


class TestTieBreakerAveraging(unittest.TestCase):
    def test_two_way_tie_splits_the_two_ranks_it_spans(self):
        points = core.points_for_rank_fn("2025")
        # Tied at rank 2 => ranks 2 and 3 => (2 + 1) / 2
        self.assertEqual(core.averaged_points_for_tied_group(2, 2, points), 1.5)

    def test_real_week16_six_way_tie_under_2025_rules(self):
        """The scenario that originally motivated the fix: six teams at 0.0.

        2025 pays 2,2,1,1,1,0 across ranks 1-6 => 7 points over 6 teams.
        The old code handed out 2,2,1,1,1,0 by arbitrary sort order instead.
        """
        points = core.points_for_rank_fn("2025")
        entries = [{"owner_id": f"o{i}", "score": 0.0} for i in range(6)]
        scored = core.score_week(entries, points)
        awarded = [e["points_awarded"] for e in scored]
        self.assertEqual(awarded, [round(7 / 6, 3)] * 6)
        self.assertEqual(len(set(awarded)), 1, "tied teams must all get the same value")
        self.assertAlmostEqual(sum(awarded), 7.0, places=2,
                              msg="a tie must not change the total points paid out")

    def test_future_seasons_inherit_the_2025_rule_table(self):
        """Only 2023/2024 use the old table; every later season is "2025+"."""
        for season in ("2025", "2026", "2027", 2026):
            points = core.points_for_rank_fn(season)
            self.assertEqual([points(r) for r in range(1, 7)], [2, 2, 1, 1, 1, 0], season)
        for season in ("2023", "2024", 2023):
            points = core.points_for_rank_fn(season)
            self.assertEqual([points(r) for r in range(1, 7)], [2, 2, 1, 1, 0, 0], season)

    def test_untied_week_pays_the_plain_rule_table(self):
        for season, expected in (("2023", [2, 2, 1, 1, 0, 0]), ("2025", [2, 2, 1, 1, 1, 0])):
            entries = [{"owner_id": f"o{i}", "score": float(100 - i)} for i in range(6)]
            scored = core.score_week(entries, core.points_for_rank_fn(season))
            self.assertEqual([e["points_awarded"] for e in scored], expected, season)

    def test_partial_tie_across_the_points_threshold(self):
        """2023 pays ranks 3-4 but not 5-6, so a tie straddling rank 4/5 splits."""
        points = core.points_for_rank_fn("2023")
        entries = [{"score": 100.0}, {"score": 90.0}, {"score": 50.0},
                   {"score": 50.0}, {"score": 50.0}, {"score": 10.0}]
        scored = core.score_week(entries, points)
        # Three-way tie at rank 3 spans ranks 3,4,5 => (1 + 1 + 0) / 3
        tied = [e["points_awarded"] for e in scored if e["score"] == 50.0]
        self.assertEqual(tied, [round(2 / 3, 3)] * 3)


class TestOwnerIdentity(unittest.TestCase):
    """The real league returns no `username` for any owner — rule 1."""

    USERS = [
        {"user_id": "u1", "display_name": "Alpha", "metadata": {"team_name": "Team A"}},
        {"user_id": "u2", "display_name": "Beta", "metadata": {}},
    ]
    ROSTERS = [{"roster_id": 1, "owner_id": "u1"}, {"roster_id": 2, "owner_id": "u2"}]

    def test_owners_stay_distinct_with_no_usernames(self):
        mapping = core.build_owner_map(self.USERS, self.ROSTERS)
        owner_ids = {info["owner_id"] for info in mapping.values()}
        self.assertEqual(owner_ids, {"u1", "u2"},
                         "missing usernames must not collapse owners together")

    def test_no_owner_id_is_ever_a_display_name(self):
        mapping = core.build_owner_map(self.USERS, self.ROSTERS)
        for info in mapping.values():
            self.assertNotIn(info["owner_id"], ("Alpha", "Beta", "Team A"))

    def test_abandoned_roster_gets_a_stable_unique_key(self):
        mapping = core.build_owner_map([], [{"roster_id": 7, "owner_id": None}])
        self.assertEqual(mapping[7]["owner_id"], "roster_7")

    def test_rename_across_seasons_keeps_one_identity(self):
        """The real case: SillyG00SE69 (2023) -> SillyG00SE13 (2024+), same user_id."""
        old = core.build_owner_map(
            [{"user_id": "u1", "display_name": "SillyG00SE69", "metadata": {}}],
            [{"roster_id": 1, "owner_id": "u1"}])
        new = core.build_owner_map(
            [{"user_id": "u1", "display_name": "SillyG00SE13", "metadata": {}}],
            [{"roster_id": 4, "owner_id": "u1"}])
        self.assertEqual(old[1]["owner_id"], new[4]["owner_id"])
        self.assertNotEqual(old[1]["display_name"], new[4]["display_name"])


class TestComputeWeek(unittest.TestCase):
    OWNER_MAP = {
        i: {"owner_id": f"u{i}", "display_name": f"Owner {i}", "team_name": f"Team {i}"}
        for i in range(1, 5)
    }

    def _matchups(self, scores):
        return [
            {"roster_id": i, "points": score, "matchup_id": 1 if i <= 2 else 2}
            for i, score in enumerate(scores, start=1)
        ]

    def test_empty_week_returns_none(self):
        self.assertIsNone(core.compute_week([], self.OWNER_MAP, core.points_for_rank_fn("2025")))

    def test_ranks_span_whole_league_not_matchup_pairs(self):
        week = core.compute_week(self._matchups([100.0, 90.0, 80.0, 70.0]),
                                 self.OWNER_MAP, core.points_for_rank_fn("2025"))
        self.assertEqual([r["weekly_rank"] for r in week["results"]], [1, 2, 3, 4])
        self.assertEqual(week["high_score"]["points"], 100.0)
        self.assertEqual(week["low_score"]["points"], 70.0)
        self.assertEqual(week["spread"], 30.0)
        self.assertEqual(week["league_average"], 85.0)

    def test_h2h_comes_from_matchup_id_while_rank_does_not(self):
        week = core.compute_week(self._matchups([100.0, 90.0, 80.0, 70.0]),
                                 self.OWNER_MAP, core.points_for_rank_fn("2025"))
        by_owner = {r["owner_id"]: r for r in week["results"]}
        # u3 lost the league-wide rank race but won its own matchup vs u4.
        self.assertEqual(by_owner["u3"]["weekly_rank"], 3)
        self.assertTrue(by_owner["u3"]["h2h_win"])
        self.assertEqual(by_owner["u3"]["opponent_owner_id"], "u4")
        self.assertFalse(by_owner["u4"]["h2h_win"])

    def test_null_points_are_treated_as_zero(self):
        matchups = [{"roster_id": 1, "points": None, "matchup_id": 1},
                    {"roster_id": 2, "points": 50.0, "matchup_id": 1}]
        week = core.compute_week(matchups, self.OWNER_MAP, core.points_for_rank_fn("2025"))
        self.assertEqual(week["low_score"]["points"], 0.0)

    def test_bye_week_leaves_no_opponent(self):
        matchups = [{"roster_id": 1, "points": 100.0, "matchup_id": None},
                    {"roster_id": 2, "points": 50.0, "matchup_id": None}]
        week = core.compute_week(matchups, self.OWNER_MAP, core.points_for_rank_fn("2025"))
        for result in week["results"]:
            self.assertIsNone(result["h2h_win"])
            self.assertIsNone(result["opponent_owner_id"])

    def test_tied_week_shares_rank_and_points(self):
        week = core.compute_week(self._matchups([90.0, 90.0, 80.0, 70.0]),
                                 self.OWNER_MAP, core.points_for_rank_fn("2025"))
        tied = [r for r in week["results"] if r["weekly_score"] == 90.0]
        self.assertEqual({r["weekly_rank"] for r in tied}, {1})
        self.assertEqual({r["points_awarded"] for r in tied}, {2.0})


class TestLiveWeekCapping(unittest.TestCase):
    """Rule 3 — /state/nfl decides what's complete, never an empty-list check."""

    def setUp(self):
        self._real_get = core.sleeper_get
        self.addCleanup(lambda: setattr(core, "sleeper_get", self._real_get))

    def _fake_state(self, state):
        def fake_get(path, *args, **kwargs):
            self.assertEqual(path, "/state/nfl")
            return state
        core.sleeper_get = fake_get

    def test_live_season_is_capped_to_the_week_before_current(self):
        self._fake_state({"season": "2025", "week": 5})
        self.assertEqual(core.determine_effective_max_week("2025", 17), 4)

    def test_past_season_is_fetched_in_full(self):
        self._fake_state({"season": "2026", "week": 2})
        self.assertEqual(core.determine_effective_max_week("2025", 17), 17)

    def test_week_one_of_a_live_season_yields_nothing_complete(self):
        self._fake_state({"season": "2025", "week": 1})
        self.assertEqual(core.determine_effective_max_week("2025", 17), 0)

    def test_requested_max_is_never_exceeded(self):
        self._fake_state({"season": "2025", "week": 15})
        self.assertEqual(core.determine_effective_max_week("2025", 10), 10)

    def test_unreachable_state_falls_back_instead_of_guessing(self):
        import requests

        def boom(path, *args, **kwargs):
            raise requests.RequestException("network down")
        core.sleeper_get = boom
        self.assertEqual(core.determine_effective_max_week("2025", 17), 17)

    def test_garbage_state_falls_back(self):
        self._fake_state({"season": "2025", "week": None})
        self.assertEqual(core.determine_effective_max_week("2025", 17), 17)


if __name__ == "__main__":
    unittest.main()


class OwnerNameOverrides(unittest.TestCase):
    """
    The league calls each other Jack/Danny/Joe/..., not by Sleeper handle.
    Overrides live in league_data.json and are applied in build_owner_map so
    every output shows the same name.
    """

    USERS = [{"user_id": "u1", "display_name": "Gatorsby90", "metadata": {"team_name": "Team Kyle"}}]
    ROSTERS = [{"roster_id": 1, "owner_id": "u1"}]

    def test_override_replaces_the_sleeper_handle(self):
        m = core.build_owner_map(self.USERS, self.ROSTERS, {"u1": "Kyle"})
        self.assertEqual(m[1]["display_name"], "Kyle")

    def test_owner_id_is_untouched_by_an_override(self):
        # Rule 1: the override is cosmetic and must never become the key.
        m = core.build_owner_map(self.USERS, self.ROSTERS, {"u1": "Kyle"})
        self.assertEqual(m[1]["owner_id"], "u1")

    def test_team_name_is_left_alone(self):
        # team_name is the team's name, not the person's.
        m = core.build_owner_map(self.USERS, self.ROSTERS, {"u1": "Kyle"})
        self.assertEqual(m[1]["team_name"], "Team Kyle")

    def test_no_overrides_keeps_sleeper_behaviour(self):
        self.assertEqual(core.build_owner_map(self.USERS, self.ROSTERS)[1]["display_name"],
                         "Gatorsby90")
        self.assertEqual(core.build_owner_map(self.USERS, self.ROSTERS, {})[1]["display_name"],
                         "Gatorsby90")

    def test_an_unlisted_owner_falls_back_to_sleeper(self):
        m = core.build_owner_map(self.USERS, self.ROSTERS, {"someone-else": "Nobody"})
        self.assertEqual(m[1]["display_name"], "Gatorsby90")

    def test_override_survives_a_rename_because_it_keys_on_owner_id(self):
        # The same owner under both handles resolves to one name.
        for handle in ("SillyG00SE69", "SillyG00SE13"):
            users = [{"user_id": "u9", "display_name": handle, "metadata": {}}]
            m = core.build_owner_map(users, [{"roster_id": 3, "owner_id": "u9"}], {"u9": "Chase"})
            self.assertEqual(m[3]["display_name"], "Chase", handle)

    def test_config_reader_stringifies_ids(self):
        self.assertEqual(core.owner_names({"owner_names": {123: "Jack"}}), {"123": "Jack"})
        self.assertEqual(core.owner_names({}), {})
