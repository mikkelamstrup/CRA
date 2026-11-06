"""Linux AMD64 Landlock ABI >= 6, fail closed; no elevated privileges."""
import ctypes
import os
import platform
from pathlib import Path

READ_FILE=1 << 2
READ_DIR=1 << 3
EXECUTE=1
WRITE_FILE=1 << 1
TRUNCATE=1 << 14
IOCTL=1 << 15
READ=READ_FILE|READ_DIR
ALL_FS=(1 << 16)-1
BIND_TCP=1
CONNECT_TCP=2

class Ruleset(ctypes.Structure):
    _fields_=[('fs',ctypes.c_uint64),('net',ctypes.c_uint64),('scoped',ctypes.c_uint64)]

class PathRule(ctypes.Structure):
    _pack_=1
    _fields_=[('access',ctypes.c_uint64),('parent_fd',ctypes.c_int32)]

class PortRule(ctypes.Structure):
    _fields_=[('access',ctypes.c_uint64),('port',ctypes.c_uint64)]

LIBC=ctypes.CDLL(None,use_errno=True)

def checked(result):
    if result<0:
        err=ctypes.get_errno()
        raise OSError(err,os.strerror(err))
    return result

def abi():
    if platform.machine()!='x86_64':raise RuntimeError('AMD64 syscall ABI required')
    return checked(LIBC.syscall(444,0,0,1))

def restrict(paths, *, bind_ports=(), connect_ports=(), trusted_egress=False):
    """Paths are explicit (path, rights) pairs. Existing FDs remain accessible."""
    if abi()<6:raise RuntimeError('Landlock ABI 6 or newer required')
    attrs=Ruleset(ALL_FS,BIND_TCP|(0 if trusted_egress else CONNECT_TCP),3)
    rules=checked(LIBC.syscall(444,ctypes.byref(attrs),ctypes.sizeof(attrs),0))
    try:
        for path,rights in paths:
            path=Path(path)
            if not path.exists():raise FileNotFoundError(path)
            if not path.is_dir():rights &= READ_FILE|EXECUTE|WRITE_FILE|TRUNCATE|IOCTL
            fd=os.open(path,os.O_PATH|os.O_CLOEXEC)
            try:
                rule=PathRule(rights,fd)
                checked(LIBC.syscall(445,rules,1,ctypes.byref(rule),0))
            finally:os.close(fd)
        for ports,right in [(bind_ports,BIND_TCP),(() if trusted_egress else connect_ports,CONNECT_TCP)]:
            for port in ports:
                if not 1<=port<=65535:raise ValueError('Invalid sandbox TCP port')
                rule=PortRule(right,port)
                checked(LIBC.syscall(445,rules,2,ctypes.byref(rule),0))
        checked(LIBC.prctl(38,1,0,0,0))  # PR_SET_NO_NEW_PRIVS
        checked(LIBC.syscall(446,rules,0))
    finally:os.close(rules)
