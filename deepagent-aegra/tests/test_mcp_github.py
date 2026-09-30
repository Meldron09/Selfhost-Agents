"""GitHub helpers shared by the settings API and the `mcp` subagent: the
direct `GET /user` token probe and the GitHub-only transport builder.

HTTP is mocked with `httpx.MockTransport` (offline, like the rest of the suite).
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from agent.mcp import github


def _probe(handler, token="ghp_abc"):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await github.probe_token(token, client=client)

    return asyncio.run(run())


def test_probe_200_returns_login_and_scopes_and_sends_bearer_once():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(
            200, json={"login": "octocat"}, headers={"X-OAuth-Scopes": "repo, read:org"}
        )

    info = _probe(handler)
    assert (info.login, info.scopes) == ("octocat", ["repo", "read:org"])
    assert seen == {"url": "https://api.github.com/user", "auth": "Bearer ghp_abc"}


def test_probe_missing_scopes_header_means_empty_list():
    """Fine-grained PATs carry no `X-OAuth-Scopes` header."""
    info = _probe(lambda request: httpx.Response(200, json={"login": "octocat"}))
    assert info.scopes == []


def test_probe_401_is_token_rejected():
    with pytest.raises(github.TokenRejected):
        _probe(lambda request: httpx.Response(401, json={"message": "Bad credentials"}))


def test_probe_network_failure_is_unreachable_not_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(github.GitHubUnreachable):
        _probe(handler)


def test_probe_unexpected_status_is_unreachable():
    with pytest.raises(github.GitHubUnreachable, match="503"):
        _probe(lambda request: httpx.Response(503))


@pytest.mark.parametrize("stored", ["ghp_abc", "Bearer ghp_abc", "bearer ghp_abc"])
def test_transport_adds_bearer_exactly_once(stored):
    transport = github.build_transport({"Authorization": stored})
    assert transport.url == "https://api.githubcopilot.com/mcp/"
    assert transport.headers["Authorization"].lower() == "bearer ghp_abc"
    assert transport.headers["Authorization"].lower().count("bearer") == 1


@pytest.mark.parametrize("stored", ["ghp_abc", "Bearer ghp_abc"])
def test_probe_bearer_exactly_once_whatever_is_stored(stored):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"login": "octocat"})

    _probe(handler, token=stored)
    assert seen["auth"] == "Bearer ghp_abc"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>captive portal</html>"),
        httpx.Response(200, json={"no": "login"}),
    ],
)
def test_probe_200_with_an_unexpected_body_is_unreachable(response):
    with pytest.raises(github.GitHubUnreachable):
        _probe(lambda request: response)
