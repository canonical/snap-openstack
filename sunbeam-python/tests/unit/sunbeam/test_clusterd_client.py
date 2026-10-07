# SPDX-FileCopyrightText: 2026 - Canonical Ltd
# SPDX-License-Identifier: Apache-2.0

import datetime
import json
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.x509.oid import NameOID
from requests.exceptions import HTTPError
from urllib3.util import connection

from sunbeam.clusterd.client import Client
from sunbeam.clusterd.service import ClusterServiceUnavailableException


@pytest.fixture
def clusterd_https(tmp_path):
    """Serve clusterd responses over mTLS, with controllable connection drops."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "clusterd-test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, SHA256())
        .public_bytes(serialization.Encoding.PEM)
    )
    private_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cert_path = tmp_path / "server.crt"
    key_path = tmp_path / "server.key"
    cert_path.write_bytes(certificate)
    key_path.write_bytes(private_key)

    requests = []
    connections = []
    drop_requests = set()
    response_status = [200]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            connections.append(self.connection)

        def handle_request(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            requests.append((self.command, self.path, self.connection))
            if len(requests) in drop_requests:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            payload = json.dumps({"metadata": "{}", "error": "test error"}).encode()
            self.send_response(response_status[0])
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = handle_request  # noqa: N815 - BaseHTTPRequestHandler API
        do_POST = handle_request  # noqa: N815
        do_PUT = handle_request  # noqa: N815
        do_PATCH = handle_request  # noqa: N815
        do_DELETE = handle_request  # noqa: N815

        def log_message(self, *args):
            pass

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    context.load_verify_locations(cadata=certificate.decode())
    context.verify_mode = ssl.CERT_REQUIRED
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = Client.from_http(
        f"https://127.0.0.1:{server.server_port}",
        certificate.decode(),
        certificate.decode(),
        private_key.decode(),
    )
    client._session.trust_env = False
    client.cluster.timeout = 3
    try:
        yield client, requests, drop_requests, response_status
    finally:
        client._session.close()
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_read_recovers_from_dropped_pooled_connection(clusterd_https):
    client, requests, drop_requests, _ = clusterd_https
    assert client.cluster.get_config("Horizon") == "{}"
    drop_requests.add(2)

    assert client.cluster.get_config("Horizon") == "{}"

    assert len(requests) == 3
    assert all(request[:2] == ("GET", "/1.0/config/Horizon") for request in requests)
    assert requests[0][2] is requests[1][2]
    assert requests[2][2] is not requests[1][2]


def test_read_retry_exhaustion_reports_unavailable(clusterd_https):
    client, requests, drop_requests, _ = clusterd_https
    assert client.cluster.get_config("Horizon") == "{}"
    drop_requests.update({2, 3})

    with pytest.raises(ClusterServiceUnavailableException, match="RemoteDisconnected"):
        client.cluster.get_config("Horizon")

    assert len(requests) == 3
    assert requests[2][2] is not requests[1][2]


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_mutations_are_not_replayed(clusterd_https, method):
    client, requests, drop_requests, _ = clusterd_https
    assert client.cluster.get_config("Horizon") == "{}"
    drop_requests.add(2)

    with pytest.raises(ClusterServiceUnavailableException, match="RemoteDisconnected"):
        client.cluster._request(method, "/1.0/config/Horizon", data="{}")

    assert len(requests) == 2
    assert requests[1][0] == method.upper()
    assert requests[0][2] is requests[1][2]


def test_http_errors_are_not_retried(clusterd_https):
    client, requests, _, response_status = clusterd_https
    response_status[0] = 503

    with pytest.raises(HTTPError):
        client.cluster.get_config("Horizon")

    assert len(requests) == 1


@pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete"])
@pytest.mark.parametrize("error", [ConnectionRefusedError, socket.timeout])
def test_connect_failure_recovers_before_sending(clusterd_https, mocker, method, error):
    client, requests, _, _ = clusterd_https
    create_connection = connection.create_connection
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise error("transient connection failure")
        return create_connection(*args, **kwargs)

    mocker.patch.object(connection, "create_connection", side_effect=fail_once)

    assert client.cluster._request(method, "/1.0/config/Horizon", data="{}") == {
        "metadata": "{}",
        "error": "test error",
    }

    assert attempts == 2
    assert len(requests) == 1
    assert requests[0][:2] == (method.upper(), "/1.0/config/Horizon")


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("error", [ConnectionRefusedError, socket.timeout])
def test_connect_retry_exhaustion_reports_unavailable(
    clusterd_https, mocker, method, error
):
    client, requests, _, _ = clusterd_https
    connect = mocker.patch.object(
        connection,
        "create_connection",
        side_effect=error("persistent connection failure"),
    )

    message = (
        "timed out" if error is socket.timeout else "persistent connection failure"
    )
    with pytest.raises(ClusterServiceUnavailableException, match=message):
        client.cluster._request(method, "/1.0/config/Horizon", data="{}")

    assert connect.call_count == 2
    assert requests == []


def test_connect_and_read_failures_share_retry_budget(clusterd_https, mocker):
    client, requests, drop_requests, _ = clusterd_https
    create_connection = connection.create_connection
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionRefusedError("transient connection failure")
        return create_connection(*args, **kwargs)

    mocker.patch.object(connection, "create_connection", side_effect=fail_once)
    drop_requests.add(1)

    with pytest.raises(ClusterServiceUnavailableException, match="RemoteDisconnected"):
        client.cluster.get_config("Horizon")

    assert attempts == 2
    assert len(requests) == 1
