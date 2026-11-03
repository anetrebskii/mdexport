"""A stand-in for GitHubClient that records calls, returns deep copies and raises configured errors."""

import copy

from mdexport.github_export import GitHubError


class FakeGitHub:
    """Listing tables, `reviews_of[number]`, `wiki` pages; `fail[name]` or `fail[("reviews", number)]` raises."""

    def __init__(self):
        self.issue_list: list[dict] = []
        self.pull_list: list[dict] = []
        self.comment_list: list[dict] = []
        self.review_comment_list: list[dict] = []
        self.reviews_of: dict[int, list[dict]] = {}
        self.wiki: dict[str, str] = {}
        self.fail: dict = {}
        self.calls: list = []
        self.closed = False

    def _get(self, key, value):
        self.calls.append(key)
        if key in self.fail:
            raise self.fail[key]
        return copy.deepcopy(value)

    def issues(self) -> list[dict]:
        return self._get("issues", self.issue_list)

    def pulls(self) -> list[dict]:
        return self._get("pulls", self.pull_list)

    def issue_comments(self) -> list[dict]:
        return self._get("issue_comments", self.comment_list)

    def review_comments(self) -> list[dict]:
        return self._get("review_comments", self.review_comment_list)

    def reviews(self, number: int) -> list[dict]:
        return self._get(("reviews", number), self.reviews_of.get(number, []))

    def wiki_pages(self) -> dict[str, str]:
        return self._get("wiki", self.wiki)

    def close(self) -> None:
        self.closed = True
