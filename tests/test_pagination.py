"""Token pagination, tested with no HTTP at all.

The repeated-token guard is the point of this module: without it, a server-side quirk
that hands back the same token twice becomes an infinite loop silently accumulating
duplicate issues, which looks like a hang rather than a bug.
"""

from __future__ import annotations

from typing import Any

import pytest

from jiralens.exceptions import JiraApiError
from jiralens.jira.pagination import paginate_tokens


def pages(*specs: tuple[list[str], str | None]) -> tuple[list[dict[str, Any]], list[str | None]]:
    """Build a fake endpoint from (issue keys, next token) pairs.

    Returns the page bodies and a list that records the token each call received.
    """
    bodies = [
        {"issues": [{"key": k} for k in keys], **({"nextPageToken": tok} if tok else {})}
        for keys, tok in specs
    ]
    return bodies, []


class TestHappyPath:
    def test_a_single_page_yields_once(self) -> None:
        bodies, seen = pages((["A-1", "A-2"], None))
        out = list(paginate_tokens(lambda t: (seen.append(t), bodies[0])[1]))
        assert len(out) == 1
        assert seen == [None]

    def test_three_pages_yield_every_issue_exactly_once(self) -> None:
        bodies, seen = pages((["A-1", "A-2"], "t1"), (["A-3", "A-4"], "t2"), (["A-5"], None))
        calls = iter(bodies)

        def fetch(token: str | None) -> dict[str, Any]:
            seen.append(token)
            return next(calls)

        keys = [i["key"] for page in paginate_tokens(fetch) for i in page["issues"]]
        assert keys == ["A-1", "A-2", "A-3", "A-4", "A-5"]
        assert len(keys) == len(set(keys))

    def test_each_page_is_requested_with_the_previous_token(self) -> None:
        bodies, seen = pages((["A-1"], "t1"), (["A-2"], "t2"), (["A-3"], None))
        calls = iter(bodies)

        def fetch(token: str | None) -> dict[str, Any]:
            seen.append(token)
            return next(calls)

        list(paginate_tokens(fetch))
        assert seen == [None, "t1", "t2"]

    def test_an_empty_result_still_yields_one_page(self) -> None:
        out = list(paginate_tokens(lambda _t: {"issues": []}))
        assert out == [{"issues": []}]

    def test_a_missing_token_key_ends_pagination(self) -> None:
        """Absence of nextPageToken is how the endpoint says 'that was the last page'."""
        calls = {"n": 0}

        def fetch(_t: str | None) -> dict[str, Any]:
            calls["n"] += 1
            return {"issues": [{"key": "A-1"}]}

        assert len(list(paginate_tokens(fetch))) == 1
        assert calls["n"] == 1

    @pytest.mark.parametrize("falsy", ["", None])
    def test_a_falsy_token_also_ends_pagination(self, falsy: Any) -> None:
        out = list(paginate_tokens(lambda _t: {"issues": [], "nextPageToken": falsy}))
        assert len(out) == 1

    def test_pagination_is_lazy(self) -> None:
        """Pages must not be fetched until consumed — a scan should stream."""
        fetched: list[str | None] = []

        def fetch(token: str | None) -> dict[str, Any]:
            fetched.append(token)
            return {"issues": [], "nextPageToken": f"t{len(fetched)}"}

        iterator = paginate_tokens(fetch)
        assert fetched == []
        next(iterator)
        assert len(fetched) == 1


class TestGuards:
    def test_a_repeated_token_raises_instead_of_looping(self) -> None:
        def fetch(_token: str | None) -> dict[str, Any]:
            return {"issues": [{"key": "A-1"}], "nextPageToken": "same"}

        with pytest.raises(JiraApiError, match="repeated pagination token"):
            list(paginate_tokens(fetch))

    def test_a_token_repeated_after_several_pages_is_caught(self) -> None:
        sequence = ["t1", "t2", "t3", "t2"]
        calls = iter(sequence)

        def fetch(_token: str | None) -> dict[str, Any]:
            return {"issues": [], "nextPageToken": next(calls)}

        with pytest.raises(JiraApiError, match="repeated pagination token"):
            list(paginate_tokens(fetch))

    def test_the_error_names_the_endpoint_that_looped(self) -> None:
        def fetch(_token: str | None) -> dict[str, Any]:
            return {"issues": [], "nextPageToken": "same"}

        with pytest.raises(JiraApiError, match="changelog bulkfetch"):
            list(paginate_tokens(fetch, label="changelog bulkfetch"))

    def test_a_non_object_page_is_rejected(self) -> None:
        with pytest.raises(JiraApiError, match="not an object"):
            list(paginate_tokens(lambda _t: ["not", "a", "dict"]))  # type: ignore[arg-type,return-value]

    def test_a_non_string_token_is_rejected(self) -> None:
        with pytest.raises(JiraApiError, match="not a string"):
            list(paginate_tokens(lambda _t: {"issues": [], "nextPageToken": 42}))

    def test_the_page_ceiling_is_enforced(self) -> None:
        counter = {"n": 0}

        def fetch(_token: str | None) -> dict[str, Any]:
            counter["n"] += 1
            return {"issues": [], "nextPageToken": f"t{counter['n']}"}

        with pytest.raises(JiraApiError, match="exceeded 5 pages"):
            list(paginate_tokens(fetch, max_pages=5))
        assert counter["n"] == 5

    def test_an_error_from_the_fetcher_propagates_unchanged(self) -> None:
        def fetch(_token: str | None) -> dict[str, Any]:
            raise JiraApiError("upstream exploded")

        with pytest.raises(JiraApiError, match="upstream exploded"):
            list(paginate_tokens(fetch))
