"""The suite's no-network guard (tests/conftest.py): outbound connections fail, loopback works."""

from __future__ import annotations

import socket

import httpx
import pytest

NetworkBlocked = RuntimeError  # tests/conftest.py raises its NetworkBlocked, a RuntimeError subclass


def test_outbound_socket_is_blocked():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkBlocked, match="tests may not"):
            s.connect(("93.184.216.34", 443))
        with pytest.raises(NetworkBlocked, match="tests may not"):
            s.connect_ex(("8.8.8.8", 53))
    finally:
        s.close()


def test_dns_lookup_is_blocked():
    with pytest.raises(NetworkBlocked, match="tests may not"):
        socket.getaddrinfo("api.anthropic.com", 443)


def test_httpx_to_a_provider_fails_fast():
    with pytest.raises((NetworkBlocked, httpx.ConnectError)):
        httpx.get("https://api.voyageai.com/v1/embeddings", timeout=2)


def test_loopback_is_allowed():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect(server.getsockname())
        assert socket.getaddrinfo("localhost", 80)
    finally:
        client.close()
        server.close()
