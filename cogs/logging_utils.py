import logging.handlers
import os
import os.path
import pickle


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
