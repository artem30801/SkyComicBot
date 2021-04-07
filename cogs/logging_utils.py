import logging.handlers
import os
import os.path
import pickle
import platform
import socket

def set_keepalive(sock, after_idle_sec=1, interval_sec=3, max_fails=5):
    """
    Sets `keepalive` parameters of given socket.

    Args:
        sock (socket): Socket which parameters will be changed.
        after_idle_sec (int, optional): Start sending keepalive packets after this amount of seconds. Defaults to 1.
        interval_sec (int, optional): Interval of keepalive packets in seconds. Defaults to 3.
        max_fails (int, optional): Count of fails leading to socket disconnect. Defaults to 5.

    Raises:
        NotImplementedError: for unknown platform.
    """
    current_platform = platform.system()  # could be empty

    if current_platform == "Linux":
        return _set_keepalive_linux(sock, after_idle_sec, interval_sec, max_fails)
    if current_platform == "Windows":
        return _set_keepalive_windows(sock, after_idle_sec, interval_sec)
    if current_platform == "Darwin":
        return _set_keepalive_osx(sock, interval_sec)

    raise NotImplementedError


def _set_keepalive_linux(sock, after_idle_sec, interval_sec, max_fails):
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, after_idle_sec)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval_sec)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, max_fails)


def _set_keepalive_windows(sock, after_idle_sec, interval_sec):
    sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, after_idle_sec * 1000, interval_sec * 1000))


def _set_keepalive_osx(sock, interval_sec):
    TCP_KEEPALIVE = 0x10
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, TCP_KEEPALIVE, interval_sec)


class BufferingSocketHandler(logging.handlers.SocketHandler):
    def __init__(self, host, port, buffer_file):
        super().__init__(host, port)
        self._buffer = FileBuffer(buffer_file)

    @property  # getter only
    def buffer(self):
        return self._buffer

    def _emit(self, record):
        try:
            s = self.makePickle(record)
            self.send(s)
            return self.sock is not None
        except Exception:
            self.handleError(record)
            return False

    def makeSocket(self, timeout=1):
        result = super().makeSocket(timeout)
        set_keepalive(result)
        return result

    def emit(self, record):
        self.send_buffer()
        success = self._emit(record)
        if not success:
            self.buffer.append(record)

    def send_buffer(self):
        try:
            self.acquire()
            success = True
            for item in self.buffer:
                success &= self._emit(item)
            if success:
                self.buffer.flush()
        finally:
            self.release()


class FileBuffer:
    def __init__(self, fname):
        self.fname = fname

    @property
    def size(self):
        return int(os.path.isfile(self.fname) \
                   and os.path.getsize(self.fname))

    def append(self, data):
        with open(self.fname, 'ba') as f:
            pickle.dump(data, f)

    def __iter__(self):
        if self.size > 0:
            try:
                with open(self.fname, 'br') as f:
                    while True:
                        yield pickle.load(f)
            except EOFError:
                return

    def flush(self):
        if self.size > 0:
            os.remove(self.fname)
