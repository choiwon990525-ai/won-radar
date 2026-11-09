"""WON 레이더 시험 — 인터넷 없이 돈다(가짜 유튜브 API · 가짜 robots). 진짜 네트워크를 쓰려 하면 바로 실패한다."""
import socket


class _NoNetwork(socket.socket):
    def connect(self, *a, **k):  # noqa: D401
        raise RuntimeError("시험 중에는 인터넷을 쓰지 않아요")

    def connect_ex(self, *a, **k):
        raise RuntimeError("시험 중에는 인터넷을 쓰지 않아요")


socket.socket = _NoNetwork
_orig_create = socket.create_connection


def _blocked(*a, **k):
    raise RuntimeError("시험 중에는 인터넷을 쓰지 않아요")


socket.create_connection = _blocked
