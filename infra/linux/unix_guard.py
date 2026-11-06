"""Additional seccomp layer: no new Unix endpoints or io_uring bypass.

Landlock ABI 8 cannot restrict pathname Unix sockets. QEMU receives only its
own monitor/log/network FDs from libvirt and needs no new AF_UNIX endpoints.
"""
import ctypes
import errno

class Compare(ctypes.Structure):
    _fields_=[('arg',ctypes.c_uint),('op',ctypes.c_int),('a',ctypes.c_uint64),('b',ctypes.c_uint64)]

def enforce():
    lib=ctypes.CDLL('libseccomp.so.2')
    lib.seccomp_init.argtypes=[ctypes.c_uint32];lib.seccomp_init.restype=ctypes.c_void_p
    lib.seccomp_release.argtypes=[ctypes.c_void_p]
    lib.seccomp_syscall_resolve_name.argtypes=[ctypes.c_char_p];lib.seccomp_syscall_resolve_name.restype=ctypes.c_int
    lib.seccomp_rule_add_array.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int,ctypes.c_uint,ctypes.POINTER(Compare)]
    lib.seccomp_load.argtypes=[ctypes.c_void_p]
    ctx=lib.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW; native architecture only.
    if not ctx:raise RuntimeError('Cannot create extra seccomp policy')
    try:
        # Kernel consumes a 32-bit domain. Mask high bits so raw syscall arguments
        # cannot evade the rule by setting unused upper register bits.
        unix=Compare(0,7,0xffffffff,1)  # SCMP_CMP_MASKED_EQ / AF_UNIX
        for name,comparison in [(b'socket',unix),(b'socketpair',None),
                                (b'io_uring_setup',None),(b'pidfd_getfd',None)]:
            number=lib.seccomp_syscall_resolve_name(name)
            if number<0:raise RuntimeError('Required seccomp syscall is unknown')
            result=lib.seccomp_rule_add_array(ctx,0x50000|errno.EPERM,number,1 if comparison else 0,ctypes.byref(comparison) if comparison else None)
            if result<0:raise OSError(-result,'Cannot add extra seccomp rule')
        result=lib.seccomp_load(ctx)
        if result<0:raise OSError(-result,'Cannot enforce extra seccomp policy')
    finally:lib.seccomp_release(ctx)
