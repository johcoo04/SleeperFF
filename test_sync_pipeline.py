"""
Tests for the cross-season aggregation in sync_pipeline.py. No network.

The counterfactual totals are the reason this file exists: they rescore every
week under a payout table the league wasn't using at the time, and a mistake
there is invisible in the UI — the numbers would just be quietly wrong.
"""

import unittest

import sync_pipeline as sp


def week(scores):
    """One week of results in the shape build_career_and_trends consumes."""
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    results, rank, prev = [], 0, None
    for i, (owner, score) in enumerate(ranked, start=1):
        if score != prev:
            rank, prev = i, score
        results.append({
            "owner_id": owner, "display_name": owner.title(), "team_name": owner,
            "weekly_score": score, "weekly_rank": rank, "points_awarded": 0,
            "opponent_owner_id": None, "opponent_display_name": None, "h2h_win": None,
        })
    top, bottom = ranked[0], ranked[-1]
    return {
        "league_average": sum(scores.values()) / len(scores),
        "high_score": {"owner": top[0], "team": top[0], "points": top[1]},
        "low_score": {"owner": bottom[0], "team": bottom[0], "points": bottom[1]},
        "spread": top[1] - bottom[1],
        "results": results,
    }


SIX = ["a", "b", "c", "d", "e", "f"]


def season(year, weeks):
    return {"year": str(year), "weeks_completed": len(weeks), "standings": [],
            "weeks": {str(i): w for i, w in enumerate(weeks, start=1)}}


def distinct_week(offset=0):
    return week({o: 200 - i * 10 - offset for i, o in enumerate(SIX)})


class Counterfactuals(unittest.TestCase):
    def setUp(self):
        docs = {"2023": season(2023, [distinct_week(i) for i in range(3)]),
                "2025": season(2025, [distinct_week(i) for i in range(2)])}
        self.career, _ = sp.build_career_and_trends(docs)
        self.owners = {o["owner_id"]: o for o in self.career["owners"]}
        self.weeks = 5

    def test_old_rules_pay_exactly_six_points_a_week(self):
        total = sum(o["career_points_old_rules"] for o in self.career["owners"])
        self.assertAlmostEqual(total, 6 * self.weeks)

    def test_new_rules_pay_exactly_seven_points_a_week(self):
        total = sum(o["career_points_new_rules"] for o in self.career["owners"])
        self.assertAlmostEqual(total, 7 * self.weeks)

    def test_new_rules_never_pay_an_owner_less_than_old_rules(self):
        # 2025+ widened the 1-point tier and shrank the zero tier, so no
        # finishing position pays less under it. A regression that swapped the
        # two tables would surface here.
        for o in self.career["owners"]:
            self.assertGreaterEqual(o["career_points_new_rules"], o["career_points_old_rules"],
                                    o["owner_id"])

    def test_counterfactuals_ignore_which_era_the_week_was_actually_in(self):
        # Same field every week, so each owner's per-week counterfactual is
        # constant regardless of the season the week belongs to.
        for o in self.career["owners"]:
            self.assertAlmostEqual(o["career_points_old_rules"] % self.weeks, 0,
                                   msg=o["owner_id"])


class TierCounts(unittest.TestCase):
    def setUp(self):
        docs = {"2025": season(2025, [distinct_week(i) for i in range(4)])}
        self.career, _ = sp.build_career_and_trends(docs)
        self.owners = {o["owner_id"]: o for o in self.career["owners"]}

    def test_tiers_account_for_every_week_played(self):
        for o in self.career["owners"]:
            self.assertEqual(
                o["two_point_weeks"] + o["one_point_weeks"] + o["zero_point_weeks"],
                o["weeks_played"], o["owner_id"])

    def test_exactly_one_owner_finishes_last_each_week(self):
        self.assertEqual(sum(o["last_place_weeks"] for o in self.career["owners"]), 4)

    def test_last_place_rate_is_a_percentage_of_weeks_played(self):
        worst = self.owners["f"]
        self.assertEqual(worst["last_place_weeks"], 4)
        self.assertAlmostEqual(worst["last_place_rate"], 100.0)
        self.assertAlmostEqual(self.owners["a"]["last_place_rate"], 0.0)

    def test_last_place_follows_field_size_not_a_hardcoded_six(self):
        # A short-handed week must still produce exactly one last-place finish.
        docs = {"2025": season(2025, [week({"a": 150, "b": 140, "c": 130})])}
        career, _ = sp.build_career_and_trends(docs)
        self.assertEqual(sum(o["last_place_weeks"] for o in career["owners"]), 1)
        self.assertEqual({o["owner_id"] for o in career["owners"] if o["last_place_weeks"]}, {"c"})


class TiedWeeks(unittest.TestCase):
    """
    A tied group splits the average of the ranks it spans, which is the only
    way these totals go non-integral. Two roundings then apply: score_week to
    3dp, and the career field to 2dp. Each owner can therefore be off by up to
    0.005, so a 6-owner league total can drift by up to 0.03 from the table's
    exact value.

    That drift is accepted rather than engineered away — it is at most a
    hundredth of a point on a display field, and no tie has occurred in 53
    weeks of league history. The tolerance is asserted explicitly so that if
    it ever grows, a test says so instead of the number quietly sliding.
    """

    OWNER_ROUNDING_DRIFT = 0.03

    def test_a_tied_week_still_pays_the_table_total(self):
        docs = {"2025": season(2025, [week({"a": 150, "b": 150, "c": 150,
                                            "d": 100, "e": 100, "f": 100})])}
        career, _ = sp.build_career_and_trends(docs)
        self.assertAlmostEqual(sum(o["career_points_old_rules"] for o in career["owners"]),
                               6, delta=self.OWNER_ROUNDING_DRIFT)
        self.assertAlmostEqual(sum(o["career_points_new_rules"] for o in career["owners"]),
                               7, delta=self.OWNER_ROUNDING_DRIFT)

    def test_an_untied_week_is_exact(self):
        # The rounding drift above must be confined to ties; a normal week
        # has to hit the table total on the nose.
        docs = {"2025": season(2025, [distinct_week()])}
        career, _ = sp.build_career_and_trends(docs)
        self.assertEqual(sum(o["career_points_old_rules"] for o in career["owners"]), 6)
        self.assertEqual(sum(o["career_points_new_rules"] for o in career["owners"]), 7)


if __name__ == "__main__":
    unittest.main()
