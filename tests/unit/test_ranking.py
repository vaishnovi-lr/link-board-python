"""Unit tests for app/ranking.py — pure logic, no database, no network."""
import math

import pytest

from app.ranking import (
    DECAY_SECONDS,
    DEFAULT_FORMULA,
    FORMULAS,
    REDDIT_EPOCH,
    RankingError,
    age_worth_of,
    explain,
    find_cycles,
    flatten,
    hn_hot,
    hot,
    is_order_stable,
    nest,
    net_score,
    rank,
    score,
    thread_order,
    tree_stats,
)


# ---------------------------------------------------------------------------
# net_score
# ---------------------------------------------------------------------------

class TestNetScore:
    def test_positive(self):
        assert net_score(10, 3) == 7

    def test_zero(self):
        assert net_score(5, 5) == 0

    def test_negative(self):
        assert net_score(2, 9) == -7

    def test_string_coercion(self):
        assert net_score("10", "3") == 7


# ---------------------------------------------------------------------------
# hot (Reddit formula)
# ---------------------------------------------------------------------------

class TestHot:
    def test_positive_score(self):
        """A post with ups > downs gets a positive log component."""
        h = hot(10, 0, REDDIT_EPOCH + 45000)
        assert h == pytest.approx(1 + 1.0, abs=0.01)

    def test_zero_votes(self):
        """Zero net score means log10(1) = 0, only the time component."""
        h = hot(0, 0, REDDIT_EPOCH)
        assert h == pytest.approx(0.0, abs=0.001)

    def test_negative_score(self):
        """More downs than ups produces a negative sign * log component."""
        h = hot(2, 9, REDDIT_EPOCH)
        # net = -7, sign=-1, log10(7) ≈ 0.845
        assert h < 0

    def test_single_upvote(self):
        """A post with 1 up and 0 downs: net=1, log10(1)=0."""
        h = hot(1, 0, REDDIT_EPOCH)
        assert h == pytest.approx(0.0, abs=0.001)

    def test_decay_constant_must_be_positive(self):
        with pytest.raises(RankingError):
            hot(1, 0, REDDIT_EPOCH, decay=0)

    def test_newer_post_higher(self):
        """A newer post with fewer votes can beat an older one."""
        old = hot(50, 0, REDDIT_EPOCH)
        new = hot(1, 0, REDDIT_EPOCH + 86400)
        assert new > old

    def test_deterministic(self):
        """Reddit's hot score has no `now` — same inputs, same output."""
        a = hot(42, 3, REDDIT_EPOCH + 10000)
        b = hot(42, 3, REDDIT_EPOCH + 10000)
        assert a == b


# ---------------------------------------------------------------------------
# hn_hot (HN formula)
# ---------------------------------------------------------------------------

class TestHnHot:
    def test_single_upvote_is_zero(self):
        """HN: (1-1) / ... = 0. One upvote scores nothing."""
        now = REDDIT_EPOCH + 3600
        assert hn_hot(1, 0, REDDIT_EPOCH, now) == 0.0

    def test_more_votes_higher(self):
        now = REDDIT_EPOCH + 3600
        a = hn_hot(10, 0, REDDIT_EPOCH, now)
        b = hn_hot(50, 0, REDDIT_EPOCH, now)
        assert b > a

    def test_older_post_decays(self):
        born = REDDIT_EPOCH
        a = hn_hot(20, 0, born, born + 3600)
        b = hn_hot(20, 0, born, born + 36000)
        assert a > b

    def test_zero_points(self):
        now = REDDIT_EPOCH + 3600
        assert hn_hot(0, 0, REDDIT_EPOCH, now) == 0.0

    def test_needs_now(self):
        """HN's formula includes the current time."""
        a = hn_hot(50, 0, REDDIT_EPOCH, REDDIT_EPOCH + 3600)
        b = hn_hot(50, 0, REDDIT_EPOCH, REDDIT_EPOCH + 36000)
        assert a != b


# ---------------------------------------------------------------------------
# score (dispatcher)
# ---------------------------------------------------------------------------

class TestScore:
    def test_reddit(self):
        post = {"ups": 10, "downs": 0, "created_epoch": REDDIT_EPOCH + 1000}
        s = score(post, formula="reddit")
        assert isinstance(s, float)

    def test_hn(self):
        now = REDDIT_EPOCH + 10000
        post = {"ups": 10, "downs": 0, "created_epoch": REDDIT_EPOCH}
        s = score(post, now=now, formula="hn")
        assert isinstance(s, float)

    def test_unknown_formula(self):
        post = {"ups": 1, "downs": 0, "created_epoch": REDDIT_EPOCH}
        with pytest.raises(RankingError, match="unknown formula"):
            score(post, formula="myspace")

    def test_missing_epoch(self):
        post = {"ups": 1, "downs": 0}
        with pytest.raises(RankingError, match="created_epoch"):
            score(post, formula="reddit")

    def test_hn_needs_now(self):
        post = {"ups": 1, "downs": 0, "created_epoch": REDDIT_EPOCH}
        with pytest.raises(RankingError, match="current time"):
            score(post, now=None, formula="hn")


# ---------------------------------------------------------------------------
# age_worth_of
# ---------------------------------------------------------------------------

class TestAgeWorthOf:
    def test_ten_times(self):
        """Ten times the votes is worth exactly DECAY_SECONDS (45000)."""
        assert age_worth_of(10) == DECAY_SECONDS

    def test_doubling(self):
        val = age_worth_of(2)
        assert val == pytest.approx(math.log10(2) * DECAY_SECONDS, abs=0.01)

    def test_multiplier_one(self):
        """1× the votes is worth zero seconds."""
        assert age_worth_of(1) == 0.0

    def test_negative_multiplier(self):
        with pytest.raises(RankingError):
            age_worth_of(-5)

    def test_zero_multiplier(self):
        with pytest.raises(RankingError):
            age_worth_of(0)


# ---------------------------------------------------------------------------
# rank
# ---------------------------------------------------------------------------

class TestRank:
    @pytest.fixture()
    def posts(self):
        return [
            {"id": 1, "ups": 50, "downs": 0, "created_epoch": REDDIT_EPOCH},
            {"id": 2, "ups": 10, "downs": 0, "created_epoch": REDDIT_EPOCH + 80000},
            {"id": 3, "ups": 200, "downs": 5, "created_epoch": REDDIT_EPOCH + 40000},
        ]

    def test_returns_all(self, posts):
        result = rank(posts, formula="reddit")
        assert len(result) == 3

    def test_positions_assigned(self, posts):
        result = rank(posts, formula="reddit")
        positions = [p["position"] for p in result]
        assert positions == [1, 2, 3]

    def test_hot_key_added(self, posts):
        result = rank(posts, formula="reddit")
        assert all("hot" in p for p in result)

    def test_highest_first(self, posts):
        result = rank(posts, formula="reddit")
        scores = [p["hot"] for p in result]
        assert scores == sorted(scores, reverse=True)

    def test_empty_list(self):
        assert rank([], formula="reddit") == []

    def test_hn_ranking(self, posts):
        now = REDDIT_EPOCH + 86400
        result = rank(posts, now=now, formula="hn")
        assert len(result) == 3


# ---------------------------------------------------------------------------
# is_order_stable
# ---------------------------------------------------------------------------

class TestIsOrderStable:
    def _make_posts(self):
        """A newer low-vote post and an older high-vote post."""
        return [
            {"id": 1, "ups": 5, "downs": 0, "created_epoch": REDDIT_EPOCH + 86400},
            {"id": 2, "ups": 50, "downs": 0, "created_epoch": REDDIT_EPOCH},
        ]

    def test_reddit_is_stable(self):
        """Reddit's score has no `now`, so order never changes without votes."""
        posts = self._make_posts()
        t1 = REDDIT_EPOCH + 100000
        t2 = t1 + 86400 * 7
        assert is_order_stable(posts, t1, t2, "reddit") is True

    def test_hn_is_unstable(self):
        """HN's score decays with time; posts overtake each other."""
        posts = self._make_posts()
        t1 = REDDIT_EPOCH + 86400
        t2 = t1 + 86400 * 2
        assert is_order_stable(posts, t1, t2, "hn") is False


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------

class TestExplain:
    def test_structure(self):
        a = {"ups": 50, "downs": 0, "created_epoch": REDDIT_EPOCH + 1000}
        b = {"ups": 5, "downs": 0, "created_epoch": REDDIT_EPOCH + 80000}
        now = REDDIT_EPOCH + 90000
        result = explain(a, b, now)
        assert "formulas" in result
        assert "reddit" in result["formulas"]
        assert "hn" in result["formulas"]
        assert "agree" in result
        for f in ("reddit", "hn"):
            entry = result["formulas"][f]
            assert "a" in entry
            assert "b" in entry
            assert "winner" in entry
            assert "margin" in entry

    def test_agreement(self):
        """When one post clearly dominates, both formulas agree."""
        a = {"ups": 100, "downs": 0, "created_epoch": REDDIT_EPOCH + 80000}
        b = {"ups": 1, "downs": 0, "created_epoch": REDDIT_EPOCH}
        now = REDDIT_EPOCH + 90000
        result = explain(a, b, now)
        assert result["agree"] is True


# ---------------------------------------------------------------------------
# thread_order — the pure Python walk
# ---------------------------------------------------------------------------

class TestThreadOrder:
    def test_flat_list(self):
        """All top-level comments: parent_id=None."""
        rows = [
            {"id": 1, "parent_id": None, "body": "A"},
            {"id": 2, "parent_id": None, "body": "B"},
            {"id": 3, "parent_id": None, "body": "C"},
        ]
        result = thread_order(rows)
        assert [r["id"] for r in result] == [1, 2, 3]
        assert all(r["depth"] == 1 for r in result)

    def test_deep_chain(self):
        """Single chain: 1 -> 2 -> 3 -> 4 -> 5, depths 1-5."""
        rows = [
            {"id": 1, "parent_id": None},
            {"id": 2, "parent_id": 1},
            {"id": 3, "parent_id": 2},
            {"id": 4, "parent_id": 3},
            {"id": 5, "parent_id": 4},
        ]
        result = thread_order(rows)
        assert [r["id"] for r in result] == [1, 2, 3, 4, 5]
        assert [r["depth"] for r in result] == [1, 2, 3, 4, 5]

    def test_branching(self):
        """Two replies under the same parent."""
        rows = [
            {"id": 1, "parent_id": None},
            {"id": 2, "parent_id": 1},
            {"id": 3, "parent_id": 1},
        ]
        result = thread_order(rows)
        ids = [r["id"] for r in result]
        assert ids == [1, 2, 3]
        assert result[1]["depth"] == 2
        assert result[2]["depth"] == 2

    def test_empty(self):
        assert thread_order([]) == []

    def test_two_node_cycle(self):
        """A -> B -> A. Must terminate and not hang."""
        rows = [
            {"id": 1, "parent_id": 2},
            {"id": 2, "parent_id": 1},
        ]
        result = thread_order(rows)
        ids = {r["id"] for r in result}
        assert ids == {1, 2}

    def test_self_referencing(self):
        """A comment is its own parent. Treated as a root."""
        rows = [{"id": 1, "parent_id": 1}]
        result = thread_order(rows)
        assert len(result) == 1
        assert result[0]["depth"] == 1

    def test_orphan_promoted_to_root(self):
        """Parent id that doesn't exist in the data. Promoted to top level."""
        rows = [
            {"id": 1, "parent_id": None},
            {"id": 2, "parent_id": 999},
        ]
        result = thread_order(rows)
        assert len(result) == 2
        assert all(r["depth"] == 1 for r in result)

    def test_max_depth(self):
        rows = [
            {"id": 1, "parent_id": None},
            {"id": 2, "parent_id": 1},
            {"id": 3, "parent_id": 2},
        ]
        result = thread_order(rows, max_depth=2)
        assert result[2].get("collapsed") is True

    def test_children_before_parents(self):
        """Input order: children first, parents later. Should still work."""
        rows = [
            {"id": 3, "parent_id": 2},
            {"id": 2, "parent_id": 1},
            {"id": 1, "parent_id": None},
        ]
        result = thread_order(rows)
        assert [r["id"] for r in result] == [1, 2, 3]


# ---------------------------------------------------------------------------
# nest
# ---------------------------------------------------------------------------

class TestNest:
    def test_empty(self):
        assert nest([]) == []

    def test_flat(self):
        rows = [
            {"id": 1, "parent_id": None},
            {"id": 2, "parent_id": None},
        ]
        tree = nest(rows)
        assert len(tree) == 2
        assert tree[0]["replies"] == []

    def test_nested(self):
        rows = [
            {"id": 1, "parent_id": None},
            {"id": 2, "parent_id": 1},
            {"id": 3, "parent_id": 2},
        ]
        tree = nest(rows)
        assert len(tree) == 1
        assert len(tree[0]["replies"]) == 1
        assert len(tree[0]["replies"][0]["replies"]) == 1

    def test_cycle_surfaced(self):
        """Cycle nodes are surfaced as unreachable roots, not lost."""
        rows = [
            {"id": 1, "parent_id": 2},
            {"id": 2, "parent_id": 1},
        ]
        tree = nest(rows)
        ids = set()
        stack = list(tree)
        while stack:
            n = stack.pop()
            if n["id"] in ids:
                continue
            ids.add(n["id"])
            stack.extend(n.get("replies", []))
        assert ids == {1, 2}

    def test_self_parent(self):
        rows = [{"id": 1, "parent_id": 1}]
        tree = nest(rows)
        assert len(tree) == 1

    def test_children_before_parents(self):
        rows = [
            {"id": 2, "parent_id": 1},
            {"id": 1, "parent_id": None},
        ]
        tree = nest(rows)
        assert len(tree) == 1
        assert tree[0]["id"] == 1
        assert len(tree[0]["replies"]) == 1


# ---------------------------------------------------------------------------
# flatten
# ---------------------------------------------------------------------------

class TestFlatten:
    def test_roundtrip(self):
        rows = [
            {"id": 1, "parent_id": None},
            {"id": 2, "parent_id": 1},
            {"id": 3, "parent_id": 1},
        ]
        tree = nest(rows)
        flat = flatten(tree)
        assert len(flat) == 3
        assert flat[0]["id"] == 1
        assert flat[0]["depth"] == 1

    def test_empty(self):
        assert flatten([]) == []


# ---------------------------------------------------------------------------
# find_cycles
# ---------------------------------------------------------------------------

class TestFindCycles:
    def test_no_cycles(self):
        rows = [
            {"id": 1, "parent_id": None},
            {"id": 2, "parent_id": 1},
        ]
        assert find_cycles(rows) == []

    def test_two_node_cycle(self):
        rows = [
            {"id": 1, "parent_id": 2},
            {"id": 2, "parent_id": 1},
        ]
        result = find_cycles(rows)
        assert set(result) == {1, 2}

    def test_three_node_cycle(self):
        rows = [
            {"id": 1, "parent_id": 3},
            {"id": 2, "parent_id": 1},
            {"id": 3, "parent_id": 2},
        ]
        result = find_cycles(rows)
        assert set(result) == {1, 2, 3}

    def test_self_cycle(self):
        rows = [{"id": 1, "parent_id": 1}]
        result = find_cycles(rows)
        assert result == [1]


# ---------------------------------------------------------------------------
# tree_stats
# ---------------------------------------------------------------------------

class TestTreeStats:
    def test_basic_stats(self):
        rows = [
            {"id": 1, "parent_id": None},
            {"id": 2, "parent_id": 1},
            {"id": 3, "parent_id": 2},
        ]
        stats = tree_stats(rows)
        assert stats["comments"] == 3
        assert stats["top_level"] == 1
        assert stats["max_depth"] == 3
        assert stats["orphans"] == []
        assert stats["cycles"] == []

    def test_orphan_detection(self):
        rows = [
            {"id": 1, "parent_id": None},
            {"id": 2, "parent_id": 999},
        ]
        stats = tree_stats(rows)
        assert 2 in stats["orphans"]

    def test_cycle_detection(self):
        rows = [
            {"id": 1, "parent_id": 2},
            {"id": 2, "parent_id": 1},
        ]
        stats = tree_stats(rows)
        assert set(stats["cycles"]) == {1, 2}

    def test_empty(self):
        stats = tree_stats([])
        assert stats["comments"] == 0
        assert stats["max_depth"] == 0


# ---------------------------------------------------------------------------
# FORMULAS constant
# ---------------------------------------------------------------------------

class TestFormulas:
    def test_reddit_and_hn_present(self):
        assert "reddit" in FORMULAS
        assert "hn" in FORMULAS

    def test_default_is_reddit(self):
        assert DEFAULT_FORMULA == "reddit"


# ---------------------------------------------------------------------------
# Reddit vs HN disagreement (the brief's own question)
# ---------------------------------------------------------------------------

class TestBriefQuestion:
    """The brief's question: brand new 1-vote post vs day-old 50-vote post."""

    def test_reddit_new_wins(self):
        """Under Reddit's formula the new post with 1 vote wins."""
        now = REDDIT_EPOCH + 86400 * 2
        new_post = {"id": 4, "ups": 1, "downs": 0, "created_epoch": now - 120}
        old_post = {"id": 5, "ups": 50, "downs": 0, "created_epoch": now - 86400}
        ranked = rank([new_post, old_post], now=now, formula="reddit")
        assert ranked[0]["id"] == 4

    def test_hn_old_wins(self):
        """Under HN's formula the day-old post with 50 votes wins."""
        now = REDDIT_EPOCH + 86400 * 2
        new_post = {"id": 4, "ups": 1, "downs": 0, "created_epoch": now - 120}
        old_post = {"id": 5, "ups": 50, "downs": 0, "created_epoch": now - 86400}
        ranked = rank([new_post, old_post], now=now, formula="hn")
        assert ranked[0]["id"] == 5
