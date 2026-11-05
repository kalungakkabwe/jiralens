"""Token-based pagination.

The enhanced Jira endpoints paginate with an opaque ``nextPageToken`` rather than
``startAt``. Two consequences shape this module:

* Pages cannot be fetched in parallel — each request needs the previous response's
  token — so a large scan is inherently sequential.
* A bug or a server-side quirk that returns the *same* token twice would loop
  forever, accumulating duplicate issues. The guard below turns that into a loud
  failure instead of a hang.

Kept separate from the client so it can be tested without any HTTP at all.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from jiralens.exceptions import JiraApiError
from jiralens.logging_config import get_logger

logger = get_logger("jiralens.jira.pagination")

#: A scan that needs more pages than this has almost certainly hit a server-side
#: loop rather than a genuinely enormous project: 10,000 pages is a million issues.
MAX_PAGES = 10_000

FetchPage = Callable[[str | None], dict[str, Any]]


def paginate_tokens(
    fetch: FetchPage,
    *,
    label: str = "request",
    max_pages: int = MAX_PAGES,
) -> Iterator[dict[str, Any]]:
    """Yield each page until the endpoint stops handing back a token.

    Args:
        fetch: called with the next page token, or None for the first page.
        label: used in error messages so a failure says which endpoint looped.
        max_pages: safety ceiling.

    Raises:
        JiraApiError: on a repeated token, a malformed page, or the ceiling.
    """
    token: str | None = None
    seen: set[str] = set()

    for page_number in range(1, max_pages + 1):
        page = fetch(token)
        if not isinstance(page, dict):
            raise JiraApiError(f"{label}: Jira returned a page that is not an object.")

        yield page

        next_token = page.get("nextPageToken")
        if not next_token:
            logger.debug("%s complete after %s page(s)", label, page_number)
            return

        if not isinstance(next_token, str):
            raise JiraApiError(f"{label}: nextPageToken was not a string.")

        if next_token in seen:
            raise JiraApiError(
                f"{label}: Jira returned a repeated pagination token, which would "
                "loop forever. Aborting rather than collecting duplicate results."
            )

        seen.add(next_token)
        token = next_token

    raise JiraApiError(
        f"{label}: exceeded {max_pages} pages. Narrow the query — this is far more "
        "than any real project should need."
    )


__all__ = ["MAX_PAGES", "FetchPage", "paginate_tokens"]
