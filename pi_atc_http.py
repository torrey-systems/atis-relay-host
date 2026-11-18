#!/usr/bin/env python3
import json
import os

import requests

RETRY_STATUSES = {429, 500, 502, 503, 504}
RETRY_DELAYS = (1, 2, 4, 8, 16)
DEFAULT_TIMEOUT = 30


class AuthRejected(Exception):
    def __init__(self, status_code):
        super().__init__("station token rejected (HTTP %s)" % status_code)
        self.status_code = status_code


class Forbidden(Exception):
    def __init__(self, status_code=403):
        super().__init__("station token is for a different ICAO (HTTP %s)" % status_code)
        self.status_code = status_code


class TransientError(Exception):
    pass


class ClientError(Exception):
    def __init__(self, status_code, message=""):
        if message:
            super().__init__(message)

        else:
            super().__init__("HTTP %s" % status_code)

        self.status_code = status_code


CLIENT_ERROR_MESSAGES = {
    404: "unknown ICAO (HTTP 404)",
    413: "audio too large (HTTP 413)",
    422: "invalid audio upload (HTTP 422)",
}


def bearer_headers(token):
    return {
        "Authorization": "Bearer %s" % token,
        "Accept": "application/json",
    }


def api_url(base_url, icao, *parts):
    bits = [base_url.rstrip("/"), "api", "pi", icao.upper()]
    bits.extend(str(part) for part in parts)
    return "/".join(bits)


def _sleep(sleep_fn, seconds):
    if seconds <= 0:
        return

    sleep_fn(seconds)


def _request(session, method, url, retry_delays, sleep_fn, timeout, **kwargs):
    delays = list(retry_delays)
    attempt = 0

    while True:
        try:
            response = session.request(method, url, timeout=timeout, **kwargs)

        except requests.RequestException as exc:
            if attempt >= len(delays):
                raise TransientError("network error talking to ATIS Relay") from exc

            _sleep(sleep_fn, delays[attempt])
            attempt += 1
            continue

        status = response.status_code
        action = _auth_or_retry(status)

        if action == "retry":
            if attempt >= len(delays):
                raise TransientError("ATIS Relay returned HTTP %s" % status)

            _sleep(sleep_fn, delays[attempt])
            attempt += 1
            continue

        return response


def _auth_or_retry(status):
    if status == 401:
        raise AuthRejected(status)

    if status == 403:
        raise Forbidden(status)

    if status in RETRY_STATUSES:
        return "retry"

    return "other"


def _json_body(response):
    try:
        data = response.json()

    except (ValueError, json.JSONDecodeError):
        return {}

    if isinstance(data, dict):
        return data

    return {}


def upload_audio(
    base_url,
    token,
    icao,
    path,
    recorded_at,
    filename=None,
    session=None,
    sleep_fn=None,
    retry_delays=RETRY_DELAYS,
    timeout=DEFAULT_TIMEOUT,
):
    if session is None:
        session = requests.Session()

    if sleep_fn is None:
        sleep_fn = __import__("time").sleep

    if filename is None:
        filename = os.path.basename(path)

    url = api_url(base_url, icao, "audio")
    headers = bearer_headers(token)
    data = {
        "recorded_at": recorded_at,
    }
    delays = list(retry_delays)
    attempt = 0

    while True:
        with open(path, "rb") as fh:
            files = {
                "file": (filename, fh, "audio/mpeg"),
            }

            try:
                response = session.request(
                    "POST",
                    url,
                    timeout=timeout,
                    headers=headers,
                    files=files,
                    data=data,
                )

            except requests.RequestException as exc:
                if attempt >= len(delays):
                    raise TransientError("network error talking to ATIS Relay") from exc

                _sleep(sleep_fn, delays[attempt])
                attempt += 1
                continue

        status = response.status_code
        action = _auth_or_retry(status)

        if action == "retry":
            if attempt >= len(delays):
                raise TransientError("ATIS Relay returned HTTP %s" % status)

            _sleep(sleep_fn, delays[attempt])
            attempt += 1
            continue

        if status == 201:
            body = _json_body(response)
            key = body.get("key") or filename
            return key, body

        if status in CLIENT_ERROR_MESSAGES:
            raise ClientError(status, CLIENT_ERROR_MESSAGES[status])

        raise ClientError(status, "audio upload HTTP %s" % status)


def post_heartbeat(
    base_url,
    token,
    icao,
    payload,
    session=None,
    sleep_fn=None,
    retry_delays=RETRY_DELAYS,
    timeout=DEFAULT_TIMEOUT,
):
    if session is None:
        session = requests.Session()

    if sleep_fn is None:
        sleep_fn = __import__("time").sleep

    url = api_url(base_url, icao, "heartbeat")
    response = _request(
        session,
        "POST",
        url,
        retry_delays,
        sleep_fn,
        timeout,
        headers={**bearer_headers(token), "Content-Type": "application/json"},
        json=payload,
    )

    if response.status_code != 200:
        raise ClientError(response.status_code, "heartbeat HTTP %s" % response.status_code)

    return _json_body(response)


def fetch_commands(
    base_url,
    token,
    icao,
    session=None,
    sleep_fn=None,
    retry_delays=RETRY_DELAYS,
    timeout=DEFAULT_TIMEOUT,
):
    if session is None:
        session = requests.Session()

    if sleep_fn is None:
        sleep_fn = __import__("time").sleep

    url = api_url(base_url, icao, "commands")
    response = _request(
        session,
        "GET",
        url,
        retry_delays,
        sleep_fn,
        timeout,
        headers=bearer_headers(token),
    )

    if response.status_code != 200:
        raise ClientError(response.status_code, "command fetch HTTP %s" % response.status_code)

    body = _json_body(response)
    commands = body.get("commands")

    if not isinstance(commands, list):
        return []

    return commands


def ack_command(
    base_url,
    token,
    icao,
    command_id,
    ok=True,
    result=None,
    session=None,
    sleep_fn=None,
    retry_delays=RETRY_DELAYS,
    timeout=DEFAULT_TIMEOUT,
):
    if session is None:
        session = requests.Session()

    if sleep_fn is None:
        sleep_fn = __import__("time").sleep

    url = api_url(base_url, icao, "commands", command_id, "ack")
    payload = {"ok": bool(ok)}

    if result is not None:
        payload["result"] = result

    response = _request(
        session,
        "POST",
        url,
        retry_delays,
        sleep_fn,
        timeout,
        headers={**bearer_headers(token), "Content-Type": "application/json"},
        json=payload,
    )

    if response.status_code not in (200, 409, 410, 404):
        raise ClientError(response.status_code, "command ack HTTP %s" % response.status_code)

    return response.status_code, _json_body(response)
