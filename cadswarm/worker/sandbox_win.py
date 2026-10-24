"""Windows process isolation for agent part scripts (stdlib + ctypes only: it runs in FreeCAD's python).

`run_confined` starts one child process and waits for it, at the strongest level this machine allows:

  appcontainer  an AppContainer with no capabilities: no network, no access to the user's profile or
                files except the few folders explicitly granted to the container's SID.
  lowintegrity  a low-integrity copy of our own token: it can't write anywhere but low-integrity
                folders (the per-run scratch dir), but CAN still read the user's files and use the
                network. Used only when AppContainers are unavailable (old Windows, policy).

Either way the child sits in a Job object: killed when the job closes, no grandchildren, a memory cap,
and a wall-clock timeout enforced by terminating the job.
"""
import ctypes
import msvcrt
import os
import subprocess
import threading
import time
from ctypes import wintypes as wt

PROFILE_NAME = "CadSwarm.ScriptSandbox"

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
adv = ctypes.WinDLL("advapi32", use_last_error=True)
userenv = ctypes.WinDLL("userenv", use_last_error=True)

PVOID = ctypes.c_void_p
HRESULT_ALREADY_EXISTS = 0x800700B7

PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_SUSPENDED = 0x4
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x400
STARTF_USESTDHANDLES = 0x100
HANDLE_FLAG_INHERIT = 1
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
INFINITE = 0xFFFFFFFF
TOKEN_ALL_ACCESS = 0xF01FF
SE_GROUP_INTEGRITY = 0x20
TokenIntegrityLevel = 25
SecurityImpersonation = 2
TokenPrimary = 1
LOW_INTEGRITY_SID = "S-1-16-4096"

JobObjectBasicUIRestrictions = 4
JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x8
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_UILIMIT_ALL = 0xFF


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", PVOID), ("Attributes", wt.DWORD)]


class SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [("AppContainerSid", PVOID), ("Capabilities", PVOID),
                ("CapabilityCount", wt.DWORD), ("Reserved", wt.DWORD)]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wt.DWORD), ("lpSecurityDescriptor", PVOID), ("bInheritHandle", wt.BOOL)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR),
                ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD),
                ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD), ("dwXCountChars", wt.DWORD),
                ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD), ("dwFlags", wt.DWORD),
                ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD), ("lpReserved2", PVOID),
                ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE)]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", PVOID)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
                ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", SID_AND_ATTRIBUTES)]


def _sig(fn, restype, *argtypes):
    fn.restype, fn.argtypes = restype, list(argtypes)


_sig(k32.CloseHandle, wt.BOOL, wt.HANDLE)
_sig(k32.CreatePipe, wt.BOOL, ctypes.POINTER(wt.HANDLE), ctypes.POINTER(wt.HANDLE),
     ctypes.POINTER(SECURITY_ATTRIBUTES), wt.DWORD)
_sig(k32.SetHandleInformation, wt.BOOL, wt.HANDLE, wt.DWORD, wt.DWORD)
_sig(k32.GetCurrentProcess, wt.HANDLE)
_sig(k32.InitializeProcThreadAttributeList, wt.BOOL, PVOID, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t))
_sig(k32.UpdateProcThreadAttribute, wt.BOOL, PVOID, wt.DWORD, ctypes.c_size_t, PVOID, ctypes.c_size_t,
     PVOID, PVOID)
_sig(k32.DeleteProcThreadAttributeList, None, PVOID)
_sig(k32.CreateProcessW, wt.BOOL, wt.LPCWSTR, wt.LPWSTR, PVOID, PVOID, wt.BOOL, wt.DWORD, PVOID,
     wt.LPCWSTR, ctypes.POINTER(STARTUPINFOEXW), ctypes.POINTER(PROCESS_INFORMATION))
_sig(k32.CreateJobObjectW, wt.HANDLE, PVOID, wt.LPCWSTR)
_sig(k32.SetInformationJobObject, wt.BOOL, wt.HANDLE, ctypes.c_int, PVOID, wt.DWORD)
_sig(k32.QueryInformationJobObject, wt.BOOL, wt.HANDLE, ctypes.c_int, PVOID, wt.DWORD, PVOID)
_sig(k32.AssignProcessToJobObject, wt.BOOL, wt.HANDLE, wt.HANDLE)
_sig(k32.TerminateJobObject, wt.BOOL, wt.HANDLE, wt.UINT)
_sig(k32.TerminateProcess, wt.BOOL, wt.HANDLE, wt.UINT)
_sig(k32.ResumeThread, wt.DWORD, wt.HANDLE)
_sig(k32.WaitForSingleObject, wt.DWORD, wt.HANDLE, wt.DWORD)
_sig(k32.GetExitCodeProcess, wt.BOOL, wt.HANDLE, ctypes.POINTER(wt.DWORD))
_sig(k32.LocalFree, PVOID, PVOID)
_sig(adv.ConvertSidToStringSidW, wt.BOOL, PVOID, ctypes.POINTER(wt.LPWSTR))
_sig(adv.ConvertStringSidToSidW, wt.BOOL, wt.LPCWSTR, ctypes.POINTER(PVOID))
_sig(adv.FreeSid, PVOID, PVOID)
_sig(adv.OpenProcessToken, wt.BOOL, wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE))
_sig(adv.DuplicateTokenEx, wt.BOOL, wt.HANDLE, wt.DWORD, PVOID, ctypes.c_int, ctypes.c_int,
     ctypes.POINTER(wt.HANDLE))
_sig(adv.SetTokenInformation, wt.BOOL, wt.HANDLE, ctypes.c_int, PVOID, wt.DWORD)
_sig(adv.CreateProcessAsUserW, wt.BOOL, wt.HANDLE, wt.LPCWSTR, wt.LPWSTR, PVOID, PVOID, wt.BOOL, wt.DWORD,
     PVOID, wt.LPCWSTR, ctypes.POINTER(STARTUPINFOEXW), ctypes.POINTER(PROCESS_INFORMATION))
_sig(userenv.CreateAppContainerProfile, ctypes.c_long, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, PVOID, wt.DWORD,
     ctypes.POINTER(PVOID))
_sig(userenv.DeriveAppContainerSidFromAppContainerName, ctypes.c_long, wt.LPCWSTR, ctypes.POINTER(PVOID))


class SandboxError(Exception):
    pass


def _check(ok, what):
    if not ok:
        raise SandboxError(f"{what} failed (Windows error {ctypes.get_last_error()})")


def container_sid_string():
    """The AppContainer's SID as 'S-1-15-2-…', creating the (per-user, capability-less) profile if needed."""
    psid = PVOID()
    hr = userenv.CreateAppContainerProfile(PROFILE_NAME, "CADSwarm script sandbox",
                                           "Runs agent part scripts with no network or file access",
                                           None, 0, ctypes.byref(psid))
    if hr & 0xFFFFFFFF == HRESULT_ALREADY_EXISTS:
        hr = userenv.DeriveAppContainerSidFromAppContainerName(PROFILE_NAME, ctypes.byref(psid))
    if hr != 0:
        raise SandboxError(f"AppContainer profile unavailable (HRESULT {hr & 0xFFFFFFFF:#x})")
    try:
        return _sid_to_string(psid)
    finally:
        adv.FreeSid(psid)


def _sid_to_string(psid):
    text = wt.LPWSTR()
    _check(adv.ConvertSidToStringSidW(psid, ctypes.byref(text)), "ConvertSidToStringSid")
    try:
        return text.value
    finally:
        k32.LocalFree(text)


def _sid_from_string(sid_text):
    psid = PVOID()
    _check(adv.ConvertStringSidToSidW(sid_text, ctypes.byref(psid)), "ConvertStringSidToSid")
    return psid


def grant(path, sid, rights, recursive_inherit=True):
    """Give the container `rights` ('RX', 'R', 'M') on `path` (and below, for a folder) via icacls."""
    spec = f"*{sid}:" + ("(OI)(CI)" if recursive_inherit else "") + f"({rights})"
    r = subprocess.run(["icacls", path, "/grant", spec], capture_output=True, text=True,
                       creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise SandboxError(f"could not grant access on {path}: {(r.stdout + r.stderr).strip()[:300]}")


def has_access(path, sid):
    """True if `path` already lets this container in: an ACE for its SID, or ALL APPLICATION PACKAGES
    (which Program Files grants)."""
    r = subprocess.run(["icacls", path], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    out = r.stdout.upper()
    return sid.upper() in out or "ALL APPLICATION PACKAGES" in out


def mark_low_integrity(path):
    r = subprocess.run(["icacls", path, "/setintegritylevel", "(OI)(CI)L"], capture_output=True, text=True,
                       creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise SandboxError(f"could not label {path} low integrity: {(r.stdout + r.stderr).strip()[:300]}")


def _env_block(env):
    if env is None:
        return None
    return ctypes.create_unicode_buffer("".join(f"{k}={v}\0" for k, v in env.items()) + "\0")


def _make_job(memory_bytes):
    job = k32.CreateJobObjectW(None, None)
    _check(job, "CreateJobObject")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION | JOB_OBJECT_LIMIT_PROCESS_MEMORY)
    info.BasicLimitInformation.ActiveProcessLimit = 1  # the child itself: no grandchildren
    info.ProcessMemoryLimit = memory_bytes
    _check(k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info),
                                       ctypes.sizeof(info)), "SetInformationJobObject")
    ui = wt.DWORD(JOB_OBJECT_UILIMIT_ALL)  # no clipboard, no desktop/window games, no foreign handles
    k32.SetInformationJobObject(job, JobObjectBasicUIRestrictions, ctypes.byref(ui), ctypes.sizeof(ui))
    return job


def _peak_memory(job):
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    if k32.QueryInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info),
                                     ctypes.sizeof(info), None):
        return info.PeakProcessMemoryUsed
    return 0


def _low_integrity_token():
    me = wt.HANDLE()
    _check(adv.OpenProcessToken(k32.GetCurrentProcess(), TOKEN_ALL_ACCESS, ctypes.byref(me)), "OpenProcessToken")
    tok = wt.HANDLE()
    try:
        _check(adv.DuplicateTokenEx(me, TOKEN_ALL_ACCESS, None, SecurityImpersonation, TokenPrimary,
                                    ctypes.byref(tok)), "DuplicateTokenEx")
    finally:
        k32.CloseHandle(me)
    psid = _sid_from_string(LOW_INTEGRITY_SID)
    label = TOKEN_MANDATORY_LABEL()
    label.Label.Sid, label.Label.Attributes = psid, SE_GROUP_INTEGRITY
    try:
        _check(adv.SetTokenInformation(tok, TokenIntegrityLevel, ctypes.byref(label), ctypes.sizeof(label)),
               "SetTokenInformation(IntegrityLevel)")
    except SandboxError:
        k32.CloseHandle(tok)
        raise
    finally:
        k32.LocalFree(psid)
    return tok


def _pipe(parent_reads):
    """(parent_end, child_end) of an anonymous pipe; only the child's end is inheritable."""
    sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
    r, w = wt.HANDLE(), wt.HANDLE()
    _check(k32.CreatePipe(ctypes.byref(r), ctypes.byref(w), ctypes.byref(sa), 0), "CreatePipe")
    parent, child = (r, w) if parent_reads else (w, r)
    _check(k32.SetHandleInformation(parent, HANDLE_FLAG_INHERIT, 0), "SetHandleInformation")
    return parent.value, child.value


class Result:
    def __init__(self):
        self.exit_code = None
        self.stdout = b""
        self.stderr = b""
        self.timed_out = False
        self.peak_memory = 0


def run_confined(level, argv, env, cwd, stdin_bytes, timeout, memory_bytes, container_sid=None, max_out=256 << 20):
    """Run `argv` confined at `level` ('appcontainer' | 'lowintegrity'); returns a Result."""
    in_parent, in_child = _pipe(parent_reads=False)
    out_parent, out_child = _pipe(parent_reads=True)
    err_parent, err_child = _pipe(parent_reads=True)
    child_ends = [in_child, out_child, err_child]
    attr_list = caps_sid = token = job = None
    pi = PROCESS_INFORMATION()
    try:
        # Attribute list: which handles the child inherits (only its three pipe ends) + its container.
        count = 2 if level == "appcontainer" else 1
        size = ctypes.c_size_t()
        k32.InitializeProcThreadAttributeList(None, count, 0, ctypes.byref(size))
        attr_list = ctypes.create_string_buffer(size.value)
        _check(k32.InitializeProcThreadAttributeList(attr_list, count, 0, ctypes.byref(size)),
               "InitializeProcThreadAttributeList")
        handles = (wt.HANDLE * 3)(*child_ends)
        _check(k32.UpdateProcThreadAttribute(attr_list, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handles,
                                             ctypes.sizeof(handles), None, None), "UpdateProcThreadAttribute(handles)")
        sc = None
        if level == "appcontainer":
            caps_sid = _sid_from_string(container_sid)
            sc = SECURITY_CAPABILITIES(caps_sid, None, 0, 0)  # no capabilities: no network, no profile
            _check(k32.UpdateProcThreadAttribute(attr_list, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                                                 ctypes.byref(sc), ctypes.sizeof(sc), None, None),
                   "UpdateProcThreadAttribute(container)")
        else:
            token = _low_integrity_token()

        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(si)
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        si.StartupInfo.hStdInput, si.StartupInfo.hStdOutput, si.StartupInfo.hStdError = child_ends
        si.lpAttributeList = ctypes.cast(attr_list, PVOID)
        cmd = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        flags = (EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT)
        envp = _env_block(env)
        if level == "appcontainer":
            ok = k32.CreateProcessW(argv[0], cmd, None, None, True, flags, envp, cwd, ctypes.byref(si),
                                    ctypes.byref(pi))
        else:
            ok = adv.CreateProcessAsUserW(token, argv[0], cmd, None, None, True, flags, envp, cwd,
                                          ctypes.byref(si), ctypes.byref(pi))
        _check(ok, f"CreateProcess({level})")

        # Into the job before it runs a single instruction.
        job = _make_job(memory_bytes)
        try:
            _check(k32.AssignProcessToJobObject(job, pi.hProcess), "AssignProcessToJobObject")
        except SandboxError:
            k32.TerminateProcess(pi.hProcess, 1)
            raise
        k32.ResumeThread(pi.hThread)
    except BaseException:
        for h in (in_parent, out_parent, err_parent):
            k32.CloseHandle(h)
        raise
    finally:
        for h in child_ends:  # the child holds its own copies now
            k32.CloseHandle(h)
        if attr_list is not None:
            k32.DeleteProcThreadAttributeList(attr_list)
        if caps_sid:
            k32.LocalFree(caps_sid)
        if token:
            k32.CloseHandle(token)

    result = Result()
    bufs = {"out": bytearray(), "err": bytearray()}

    def drain(handle, key, cap):
        f = os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY), "rb", buffering=0)
        with f:
            try:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        return
                    if len(bufs[key]) < cap:
                        bufs[key] += chunk
            except OSError:
                return

    def feed(handle):
        f = os.fdopen(msvcrt.open_osfhandle(handle, os.O_WRONLY), "wb", buffering=0)
        try:
            f.write(stdin_bytes)
        except OSError:
            pass  # child died before reading its request
        finally:
            try:
                f.close()
            except OSError:
                pass

    threads = [threading.Thread(target=drain, args=(out_parent, "out", max_out), daemon=True),
               threading.Thread(target=drain, args=(err_parent, "err", 1 << 20), daemon=True),
               threading.Thread(target=feed, args=(in_parent,), daemon=True)]
    for t in threads:
        t.start()
    try:
        if k32.WaitForSingleObject(pi.hProcess, int(timeout * 1000)) == WAIT_TIMEOUT:
            result.timed_out = True
            k32.TerminateJobObject(job, 1)
            k32.WaitForSingleObject(pi.hProcess, 5000)
        code = wt.DWORD()
        k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        result.exit_code = code.value
        result.peak_memory = _peak_memory(job)
    finally:
        k32.TerminateJobObject(job, 1)  # belt and braces; closing the job kills it anyway
        k32.CloseHandle(job)
        deadline = time.monotonic() + 5
        for t in threads:
            t.join(max(deadline - time.monotonic(), 0.1))
        k32.CloseHandle(pi.hThread)
        k32.CloseHandle(pi.hProcess)
    result.stdout, result.stderr = bytes(bufs["out"]), bytes(bufs["err"])
    return result
