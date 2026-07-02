import io


class CallbackWriter(io.TextIOBase):
    def __init__(self, callback):
        self.callback = callback
        self.buffer = ""

    def write(self, text):
        if not text:
            return 0
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.rstrip()
            if line:
                self.callback(line)
        return len(text)

    def flush(self):
        if self.buffer.strip():
            self.callback(self.buffer.strip())
        self.buffer = ""
