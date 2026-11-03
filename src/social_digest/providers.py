"""Paid provider requests, pagination and normalization. No implicit retries."""

import datetime as dt
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

import requests

from .config import Config
from .store import Ledger, fingerprint

TWITTER = "https://api.twitterapi.io"
TIKHUB = "https://api.tikhub.io/api/v1/xiaohongshu/app_v2"


class ProviderError(RuntimeError):
    pass


def successful(provider: str, body: dict) -> None:
    if provider == "twitter":
        if body.get("status") == "error" or body.get("error"):
            raise ProviderError("TwitterAPI.io returned an API error; inspect private raw artifact")
        return
    upstream = body.get("data", {})
    if body.get("code") != 200 or not isinstance(upstream, dict):
        raise ProviderError("TikHub returned an API error; inspect private raw artifact")
    if upstream.get("success") is False or upstream.get("code", 0) not in (0, 200):
        raise ProviderError("TikHub upstream failure; inspect private raw artifact")
    if "data" not in upstream:
        raise ProviderError("TikHub response is missing upstream data")


class Providers:
    def __init__(self, config: Config, ledger: Ledger, env: dict[str, str]):
        self.config, self.ledger, self.env = config, ledger, env
        self.session = requests.Session()

    def get(self, provider: str, endpoint: str, params: dict) -> tuple[dict, str]:
        key = fingerprint([provider, endpoint, params])
        body = self.ledger.cached(key)
        if body is None:
            variable = "TWITTERAPI_API_KEY" if provider == "twitter" else "TIKHUB_API_KEY"
            secret = self.env.get(variable)
            if not secret:
                raise ProviderError(f"missing {variable}")
            micro = (
                self.config.budget.twitter_page_reserve * 150 if provider == "twitter" else 10000
            )
            self.ledger.reserve(key, provider, micro)
            headers = (
                {"X-API-Key": secret}
                if provider == "twitter"
                else {"Authorization": f"Bearer {secret}"}
            )
            base = TWITTER if provider == "twitter" else TIKHUB
            try:
                response = self.session.get(
                    base + endpoint,
                    params=params,
                    headers=headers,
                    timeout=45,
                    allow_redirects=False,
                )
            except requests.RequestException:
                raise ProviderError(
                    "provider transport failed; request billing is uncertain"
                ) from None
            try:
                body = response.json()
            except ValueError:
                body = {"http_status": response.status_code, "invalid_json": True}
            if not isinstance(body, dict):
                body = {"http_status": response.status_code, "invalid_json": True}
            if response.status_code != 200:
                body["http_status"] = response.status_code
            self.ledger.complete(key, body)
            if response.status_code != 200:
                raise ProviderError(f"provider HTTP {response.status_code}; response cached")
        if not isinstance(body, dict) or body.get("invalid_json") or body.get("http_status"):
            raise ProviderError("cached provider response is unusable")
        successful(provider, body)
        return body, str((self.ledger.directory / "raw" / f"{key}.json").resolve())


def timestamp(value) -> str | None:
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            number = float(value)
            if number > 1e12:
                number /= 1000
            result = dt.datetime.fromtimestamp(number, dt.UTC)
        else:
            try:
                result = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                result = parsedate_to_datetime(str(value))
        return result.astimezone(dt.UTC).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def twitter_item(tweet: dict, raw: str) -> dict | None:
    identifier = str(tweet.get("id", ""))
    if not identifier:
        return None
    return {
        "id": f"twitter:{identifier}",
        "provider": "twitter",
        "source_id": identifier,
        "url": f"https://x.com/i/status/{identifier}",
        "author": tweet.get("author", {}).get("userName", ""),
        "published_at": timestamp(tweet.get("createdAt")),
        "text": str(tweet.get("text", "")),
        "kind": "tweet",
        "raw": [raw],
        "metrics": {k: tweet.get(k, 0) for k in ("likeCount", "retweetCount", "replyCount")},
    }


def xhs_item(note: dict, raw: str) -> dict | None:
    identifier = str(note.get("id", ""))
    if not identifier:
        return None
    url = f"https://www.xiaohongshu.com/explore/{identifier}"
    if note.get("xsec_token"):
        url += "?" + urlencode({"xsec_token": note["xsec_token"]})
    return {
        "id": f"xhs:{identifier}",
        "provider": "xhs",
        "source_id": identifier,
        "url": url,
        "author": note.get("user", {}).get("nickname", ""),
        "published_at": timestamp(note.get("timestamp", note.get("time"))),
        "text": "\n".join(str(note.get(k, "")) for k in ("title", "desc")).strip(),
        "kind": str(note.get("type", "normal")),
        "raw": [raw],
        "metrics": {
            k: note.get(k, 0) for k in ("liked_count", "comments_count", "collected_count")
        },
    }


def collect(api: Providers, since: str, until: str) -> list[dict]:
    items = {}

    def add(item):
        if item and item["published_at"] and since <= item["published_at"][:10] < until:
            existing = items.get(item["id"])
            if existing:
                existing["raw"] = list(dict.fromkeys(existing["raw"] + item["raw"]))
            else:
                items[item["id"]] = item

    cfg = api.config
    if cfg.twitter.enabled:
        queries = cfg.twitter.queries + [f"from:{name}" for name in cfg.twitter.watchlist]
        for query in dict.fromkeys(queries):
            cursor = ""
            seen = set()
            for _ in range(cfg.twitter.max_pages):
                body, raw = api.get(
                    "twitter",
                    "/twitter/tweet/advanced_search",
                    {
                        "query": f"({query}) since:{since} until:{until}",
                        "queryType": cfg.twitter.sort,
                        "cursor": cursor,
                    },
                )
                if not isinstance(body.get("tweets"), list):
                    raise ProviderError("Twitter search response has no tweets list")
                for tweet in body["tweets"]:
                    add(twitter_item(tweet, raw))
                cursor = body.get("next_cursor", "")
                if not body.get("has_next_page") or not cursor or cursor in seen:
                    break
                seen.add(cursor)
    if cfg.xhs.enabled:
        for query in dict.fromkeys(cfg.xhs.queries):
            params = {
                "keyword": query,
                "page": 1,
                "sort_type": cfg.xhs.sort,
                "note_type": "不限",
                "time_filter": "不限",
            }
            seen = set()
            for _ in range(cfg.xhs.max_pages):
                body, raw = api.get("xhs", "/search_notes", params)
                upstream = body["data"]
                values = upstream["data"].get("items")
                if not isinstance(values, list):
                    raise ProviderError("TikHub search response has no items list")
                for value in values:
                    if value.get("model_type") == "note":
                        add(xhs_item(value["note"], raw))
                page = upstream.get("next_page")
                if not values or not page or page in seen or page <= params["page"]:
                    break
                seen.add(page)
                if not upstream.get("search_id") or not upstream.get("search_session_id"):
                    raise ProviderError("TikHub pagination is missing search session identifiers")
                params = {
                    **params,
                    "page": page,
                    "search_id": upstream["search_id"],
                    "search_session_id": upstream["search_session_id"],
                }
    return list(items.values())


def enrich(api: Providers, item: dict, comments: bool) -> dict:
    result = dict(item)
    if item["provider"] == "twitter":
        body, raw = api.get("twitter", "/twitter/tweets", {"tweet_ids": item["source_id"]})
        tweets = body.get("tweets", [])
        matches = [x for x in tweets if str(x.get("id")) == item["source_id"]]
        if not matches:
            raise ProviderError("Twitter detail did not return the requested tweet")
        result["text"] = str(matches[0].get("text", item["text"]))
        result["raw"] = [*item["raw"], raw]
        # Replies are searched only for selected items, using the same bounded search endpoint.
        if comments:
            body, raw = api.get(
                "twitter",
                "/twitter/tweet/advanced_search",
                {"query": f"conversation_id:{item['source_id']}", "queryType": "Top", "cursor": ""},
            )
            result["comments"] = [
                {"id": str(x.get("id", "")), "text": str(x.get("text", ""))}
                for x in body.get("tweets", [])[:20]
            ]
            result["raw"].append(raw)
    else:
        endpoint = "/get_video_note_detail" if item["kind"] == "video" else "/get_image_note_detail"
        body, raw = api.get("xhs", endpoint, {"note_id": item["source_id"]})
        values = body["data"]["data"]
        if not isinstance(values, list) or not values:
            raise ProviderError("TikHub detail response has no note list")
        notes = values[0].get("note_list", [])
        matches = [note for note in notes if str(note.get("id")) == item["source_id"]]
        if not matches:
            raise ProviderError("TikHub detail did not return the requested note")
        note = matches[0]
        result["text"] = "\n".join(str(note.get(k, "")) for k in ("title", "desc")).strip()
        if not result["text"]:
            raise ProviderError("TikHub detail response has no note text")
        result["raw"] = [*item["raw"], raw]
        if comments:
            body, raw = api.get("xhs", "/get_note_comments", {"note_id": item["source_id"]})
            values = body["data"]["data"].get("comments")
            if not isinstance(values, list):
                raise ProviderError("TikHub comments response has no comments list")
            result["comments"] = [
                {"id": str(x.get("id", "")), "text": str(x.get("content", ""))} for x in values[:20]
            ]
            result["raw"].append(raw)
    return result
