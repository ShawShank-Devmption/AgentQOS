"""CPU socket lifecycle and clone-only overload handling (§7.6)."""

import threading
from unittest.mock import Mock, patch

import pytest

from controller.punt_handler import BoundedPuntQueue, PuntReceiver


class SocketInput:
    """A bounded CPU source with a barrier after its offered clones."""

    def __init__(self, frames: list[bytes]) -> None:
        self.frames = iter(frames)
        self.drained = threading.Event()
        self.closed = False

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int, int]]:
        try:
            return next(self.frames), ("cpu0", 0, 0)
        except StopIteration:
            self.drained.set()
            self.drained.wait(0.005)
            raise TimeoutError from None

    def close(self) -> None:
        self.closed = True


def test_receiver_binds_cpu_interface_and_discards_only_excess_clones() -> None:
    source = SocketInput([b"first", b"second"])
    transport = Mock()
    transport.recvfrom.side_effect = source.recvfrom
    transport.close.side_effect = source.close
    queue = BoundedPuntQueue(1)
    with (
        patch("controller.punt_handler.sys.platform", "linux"),
        patch("controller.punt_handler.socket.socket", return_value=transport),
        patch("controller.punt_handler.socket.AF_PACKET", 17, create=True),
        PuntReceiver("cpu0", queue),
    ):
        assert source.drained.wait(1)
    transport.bind.assert_called_once_with(("cpu0", 0))
    assert source.closed
    assert queue.pop() == b"first"
    assert queue.dropped == 1


def test_bind_failure_closes_the_owned_socket() -> None:
    transport = Mock()
    transport.bind.side_effect = OSError("missing interface")
    with (
        patch("controller.punt_handler.sys.platform", "linux"),
        patch("controller.punt_handler.socket.socket", return_value=transport),
        patch("controller.punt_handler.socket.AF_PACKET", 17, create=True),
        pytest.raises(OSError, match="missing interface"),
        PuntReceiver("cpu0", BoundedPuntQueue(1)),
    ):
        pytest.fail("bound nonexistent CPU interface")
    transport.close.assert_called_once()


def test_non_linux_receiver_fails_before_socket_creation() -> None:
    with (
        patch("controller.punt_handler.sys.platform", "darwin"),
        patch("controller.punt_handler.socket.socket") as create,
        pytest.raises(RuntimeError, match="Linux"),
        PuntReceiver("cpu0", BoundedPuntQueue(1)),
    ):
        pytest.fail("AF_PACKET is not supported on this host")
    create.assert_not_called()
