"""Bounded QEMU Ethernet stream observer. No payload storage or inferred outcomes.

Trusted launcher supplies two already-open sockets. Parser child cannot open
files or new sockets. One process per operator link; attribution is out-of-band.
"""
import ctypes
import errno
import json
import os
from pathlib import Path
import resource
import selectors
import socket
import struct
import subprocess
import sys
import time

from landlock import restrict

MAX_FRAME = 69632  # QEMU 10.2.1 NET_BUFSIZE, includes large offloaded frames.
MAX_QUEUE = 262144
MAX_FLOWS = 64
STALL_SECONDS = 30


def header(frame):
    """Return only numeric network headers, never strings from guest payload."""
    if len(frame) < 14:
        return None
    typ = int.from_bytes(frame[12:14], 'big'); pos = 14
    for _ in range(2):
        if typ not in (0x8100, 0x88a8):
            break
        if len(frame) < pos + 4:
            return None
        typ = int.from_bytes(frame[pos+2:pos+4], 'big'); pos += 4
    fragmented = False
    if typ == 0x0800:
        if len(frame) < pos + 20 or frame[pos] >> 4 != 4:
            return None
        length = (frame[pos] & 15) * 4
        total = int.from_bytes(frame[pos+2:pos+4], 'big')
        if length < 20 or total < length or len(frame) < pos + total:
            return None
        end = pos + total; proto = frame[pos+9]
        src = socket.inet_ntop(socket.AF_INET, frame[pos+12:pos+16])
        dst = socket.inet_ntop(socket.AF_INET, frame[pos+16:pos+20])
        fragmented = bool(int.from_bytes(frame[pos+6:pos+8], 'big') & 0x3fff)
        pos += length
    elif typ == 0x86dd:
        if len(frame) < pos + 40 or frame[pos] >> 4 != 6:
            return None
        payload = int.from_bytes(frame[pos+4:pos+6], 'big')
        end = pos + 40 + payload
        if payload == 0 or len(frame) < end:
            return None  # No IPv6 jumbogram or extension reassembly.
        proto = frame[pos+6]
        src = socket.inet_ntop(socket.AF_INET6, frame[pos+8:pos+24])
        dst = socket.inet_ntop(socket.AF_INET6, frame[pos+24:pos+40]); pos += 40
        for _ in range(8):
            if proto not in (0, 43, 44, 51, 60):
                break
            if pos + 2 > end:
                return None
            nxt = frame[pos]
            length = 8 if proto == 44 else ((frame[pos+1]+2)*4 if proto == 51 else (frame[pos+1]+1)*8)
            if pos + length > end:
                return None
            if proto == 44:
                fragmented = True
            proto = nxt; pos += length
        if proto in (0, 43, 44, 51, 60):
            return None
    else:
        return None
    sport = dport = None
    if not fragmented and proto in (6, 17):
        minimum = 20 if proto == 6 else 8
        if pos + minimum > end:
            return None
        if proto == 6 and ((frame[pos+12] >> 4)*4 < 20 or pos+(frame[pos+12] >> 4)*4 > end):
            return None
        sport, dport = struct.unpack('!HH', frame[pos:pos+4])
    return src, dst, proto, sport, dport


class Compare(ctypes.Structure):
    _fields_ = [('arg', ctypes.c_uint), ('op', ctypes.c_int), ('a', ctypes.c_uint64), ('b', ctypes.c_uint64)]


def sandbox():
    # Load policy library before Landlock removes filesystem access.
    lib = ctypes.CDLL('libseccomp.so.2')
    lib.seccomp_init.argtypes = [ctypes.c_uint32]; lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]; lib.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = lib.seccomp_init(0x50000 | errno.EPERM)  # Default deny, native arch only.
    if not ctx:
        raise RuntimeError('Cannot create flow policy')
    try:
        # No process/IPC/keyring/scheduler mutation, exec, file opens or new
        # sockets. Only I/O on launcher FDs and local Python memory/time remain.
        for name in ('read', 'write', 'close', 'fstat', 'lseek',
                     'mmap', 'mprotect', 'munmap', 'mremap', 'brk', 'madvise',
                     'rt_sigaction', 'rt_sigprocmask', 'rt_sigreturn', 'sigaltstack',
                     'futex', 'sched_yield', 'clock_gettime', 'clock_nanosleep',
                     'gettimeofday', 'getpid', 'getppid', 'gettid', 'getrandom',
                     'epoll_ctl', 'epoll_wait', 'epoll_pwait', 'epoll_pwait2',
                     'accept', 'accept4', 'recvfrom', 'sendto', 'shutdown',
                     'getsockname', 'getpeername', 'getsockopt',
                     'exit', 'exit_group'):
            num = lib.seccomp_syscall_resolve_name(name.encode())
            if num < 0 or lib.seccomp_rule_add_array(ctx, 0x7fff0000, num, 0, None) < 0:
                raise RuntimeError('Cannot build flow policy')
        # Kernel uses 32-bit command numbers; mask unused high register bits.
        for name, commands in ((b'ioctl', (0x5421,)), (b'fcntl', (1, 2, 3, 4))):
            number = lib.seccomp_syscall_resolve_name(name)
            for command in commands:
                comparison = Compare(1, 7, 0xffffffff, command)
                if number < 0 or lib.seccomp_rule_add_array(ctx, 0x7fff0000, number, 1, ctypes.byref(comparison)) < 0:
                    raise RuntimeError('Cannot build descriptor policy')
        restrict([])
        if lib.seccomp_load(ctx) < 0:
            raise RuntimeError('Cannot load flow policy')
    finally:
        lib.seccomp_release(ctx)


class Observer:
    def __init__(self, output):
        self.output = output; self.flows = {}; self.sequence = 0; self.window_sequence = 0
        self.dropped = 0; self.unknown = 0; self.overflow = 0
        self.packets = 0; self.bytes = 0; self.max_queue = 0

    def packet(self, direction, frame):
        self.packets += 1; self.bytes += len(frame)
        parsed = header(frame)
        if parsed is None:
            self.unknown += 1; return
        key = (direction, *parsed)
        if key not in self.flows:
            if len(self.flows) >= MAX_FLOWS:
                self.overflow += 1; return
            self.flows[key] = [0, 0]
        self.flows[key][0] += 1; self.flows[key][1] += len(frame)

    def emit(self, value):
        self.sequence += 1
        data = (json.dumps({'sequence': self.sequence, 'window_sequence': self.window_sequence, 'observed_at': time.time(), **value}, separators=(',', ':'))+'\n').encode()
        if len(data) > 4096:
            raise ValueError('Metadata atomic bound')
        try:
            if os.write(self.output, data) != len(data):
                raise RuntimeError('Non-atomic metadata pipe')
        except BlockingIOError:
            self.dropped += 1  # Forwarding remains independent of the consumer.

    def flush(self, state):
        self.window_sequence += 1
        for (direction, src, dst, proto, sport, dport), (packets, size) in self.flows.items():
            self.emit({'kind': 'flow', 'direction': direction, 'src': src, 'dst': dst,
                       'protocol': proto, 'source_port': sport, 'destination_port': dport,
                       'packets': packets, 'bytes': size})
        self.flows.clear()
        self.emit({'kind': 'status', 'state': state, 'metadata_dropped': self.dropped,
                   'unknown_packets': self.unknown, 'flow_overflow': self.overflow,
                   'packets': self.packets, 'bytes': self.bytes, 'max_queue': self.max_queue})


def forward(listener, backend, output, *, confined=True):
    selector = selectors.DefaultSelector()
    listener.setblocking(False); backend.setblocking(False); os.set_blocking(output, False)
    # Linux inherits this listener option on accepted sockets. Avoid adding a
    # Nagle/delayed-ACK cycle to interactive lab traffic.
    for sock in (listener, backend):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    observer = Observer(output)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (128*1024*1024, 128*1024*1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    os.nice(10)
    if confined:
        sandbox()
    # libffi can retain a library descriptor while loading ctypes. The parser
    # needs only its exact sockets, epoll and stdio (null/null/metadata pipe).
    keep = {0, 1, 2, output, listener.fileno(), backend.fileno(), selector.fileno()}
    if max(keep) >= 64:
        raise ValueError('Inherited descriptor bound')
    for fd in range(3, 64):
        if fd not in keep:
            try:
                os.close(fd)
            except OSError:
                pass
    selector.register(listener, selectors.EVENT_READ)
    observer.flush('waiting')
    deadline = time.monotonic() + 180
    operator = None
    while operator is None:
        if time.monotonic() > deadline:
            raise TimeoutError('Operator attach')
        if selector.select(1):
            operator, _ = listener.accept()
        else:
            observer.flush('waiting')  # Reader death must also release unattached ports.
    selector.unregister(listener); listener.close(); operator.setblocking(False)
    endpoints = [operator, backend]
    receive = [bytearray(), bytearray()]; send = [bytearray(), bytearray()]
    partial_since = [None, None]; blocked_since = [None, None]
    last_flush = time.monotonic()
    observer.flush('connected')
    try:
        while True:
            now = time.monotonic()
            for i, sock in enumerate(endpoints):
                buf = receive[i]; dest = send[1-i]
                while len(buf) >= 4:
                    size = int.from_bytes(buf[:4], 'big')
                    if size == 0 or size > MAX_FRAME:
                        raise ValueError('Frame bound')
                    if len(buf) < size+4 or len(dest) + size+4 > MAX_QUEUE:
                        break
                    observer.packet('operator_to_target' if i == 0 else 'target_to_operator', buf[4:size+4])
                    dest.extend(buf[:size+4]); del buf[:size+4]
                    partial_since[i] = None  # A new partial frame gets its own deadline.
                    observer.max_queue = max(observer.max_queue, len(dest))
                incomplete = bool(buf) and (len(buf) < 4 or len(buf) < int.from_bytes(buf[:4], 'big')+4)
                partial_since[i] = (partial_since[i] or now) if incomplete else None
                blocked_since[i] = (blocked_since[i] or now) if send[i] else None
                if any(t is not None and now-t > STALL_SECONDS for t in (partial_since[i], blocked_since[i])):
                    raise TimeoutError('Stalled frame or writer')
            # Parse both directions before registering writes: reverse traffic
            # must not wait for the selector timeout merely due to loop order.
            for i, sock in enumerate(endpoints):
                buf = receive[i]; dest = send[1-i]
                flags = 0
                if len(dest) <= MAX_QUEUE-MAX_FRAME-4 and len(buf) < MAX_FRAME+4:
                    flags |= selectors.EVENT_READ
                if send[i]:
                    flags |= selectors.EVENT_WRITE
                try:
                    selector.unregister(sock)
                except KeyError:
                    pass
                if flags:
                    selector.register(sock, flags, i)
            if now-last_flush >= 1:
                observer.flush('connected'); last_flush = now
            for key, flags in selector.select(.1):
                i = key.data; sock = key.fileobj
                if flags & selectors.EVENT_WRITE:
                    try:
                        size = sock.send(send[i])
                        if size:
                            del send[i][:size]; blocked_since[i] = time.monotonic() if send[i] else None
                    except BlockingIOError:
                        pass
                if flags & selectors.EVENT_READ:
                    try:
                        data = sock.recv(min(65536, MAX_FRAME+4-len(receive[i])))
                    except BlockingIOError:
                        continue
                    if not data:
                        return  # Reconnect needs coordinated VM/link restart, never silent READY.
                    receive[i].extend(data)
    finally:
        observer.flush('disconnected')
        for sock in endpoints:
            sock.close()
        selector.close()


def launch(front_port, target_port):
    """Only trusted node code calls this; guest never selects host destinations."""
    if not all(type(p) is int and 23000 <= p <= 23999 for p in (front_port, target_port)) or front_port == target_port:
        raise ValueError('Fixed lab port budget')
    with socket.socket() as listener, socket.socket() as backend:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', front_port)); listener.listen(1)
        backend.settimeout(5); backend.connect(('127.0.0.1', target_port)); backend.settimeout(None)
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(listener.fileno()), str(backend.fileno())],
                                   pass_fds=(listener.fileno(), backend.fileno()), close_fds=True,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, start_new_session=True)
    return process


def main():
    front, back = (socket.socket(fileno=int(v)) for v in sys.argv[1:3])
    try:
        forward(front, back, 1)
    except Exception:
        # No exception strings/raw guest bytes reach logs.
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
