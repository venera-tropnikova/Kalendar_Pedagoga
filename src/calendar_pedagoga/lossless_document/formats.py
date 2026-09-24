"""Signature validation and bounded, isolated conversion of legacy Word files."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import os
import configparser
import shutil
import struct
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic
import zipfile

from lxml import etree

from .models import ConversionEvent

OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_PARTS = 4096
MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


class ExtractionError(ValueError):
    """A technical read error, never a judgement that this is 'not a program'."""


class ConversionError(ExtractionError):
    def __init__(self, message: str, event: ConversionEvent):
        super().__init__(message)
        self.event = event


def parse_xml(data: bytes) -> etree._Element:
    try:
        root = etree.fromstring(data, etree.XMLParser(
            resolve_entities=False, no_network=True, load_dtd=False, recover=False,
            remove_blank_text=False, remove_comments=False,
        ))
        if root.getroottree().docinfo.doctype:
            raise ExtractionError("DTD declarations are not supported")
        return root
    except etree.XMLSyntaxError as exc:
        raise ExtractionError(f"Invalid XML: {exc}") from exc


def read_package(data: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_PARTS or sum(i.file_size for i in entries) > MAX_PACKAGE_BYTES:
                raise ExtractionError("Package resource limit exceeded")
            names = [i.filename for i in entries]
            if len(names) != len(set(names)):
                raise ExtractionError("Duplicate package part names")
            if any(i.flag_bits & 1 or i.file_size > MAX_INPUT_BYTES for i in entries):
                raise ExtractionError("Encrypted or oversized package part")
            if any(n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/") for n in names):
                raise ExtractionError("Unsafe package part path")
            return {i.filename: archive.read(i) for i in entries if not i.is_dir()}
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
        raise ExtractionError(f"Cannot read package: {exc}") from exc


def _is_word_compound(data: bytes) -> bool:
    """Read only the bounded CFB directory; never execute an OLE object."""
    if len(data) < 512 or data[28:30] != b"\xfe\xff":
        return False
    major, shift = struct.unpack_from("<HH", data, 26)[0], struct.unpack_from("<H", data, 30)[0]
    if (major, shift) not in ((3, 9), (4, 12)):
        return False
    sector_size = 1 << shift
    sectors = len(data) // sector_size - 1
    if sectors < 1:
        return False

    def sector(index: int) -> bytes:
        if not 0 <= index < sectors:
            raise ExtractionError("Invalid CFB sector reference")
        return data[(index + 1) * sector_size:(index + 2) * sector_size]

    def chain(start: int, fat: tuple[int, ...]):
        seen: set[int] = set()
        while start != 0xFFFFFFFE:
            if start in seen or not 0 <= start < len(fat) or len(seen) > sectors:
                raise ExtractionError("Invalid or cyclic CFB chain")
            seen.add(start)
            yield sector(start)
            start = fat[start]

    try:
        fat_count = struct.unpack_from("<I", data, 44)[0]
        if fat_count > sectors:
            return False
        difat = list(struct.unpack_from("<109I", data, 76))
        next_difat, difat_count = struct.unpack_from("<II", data, 68)
        if difat_count > sectors:
            return False
        seen_difat: set[int] = set()
        for _ in range(difat_count):
            if next_difat in seen_difat:
                return False
            seen_difat.add(next_difat)
            values = struct.unpack(f"<{sector_size // 4}I", sector(next_difat))
            difat.extend(values[:-1])
            next_difat = values[-1]
        fat_ids = [i for i in difat if i != 0xFFFFFFFF]
        if len(fat_ids) != fat_count or len(set(fat_ids)) != len(fat_ids):
            return False
        fat = tuple(v for i in fat_ids for v in struct.unpack(f"<{sector_size // 4}I", sector(i)))
        directory = b"".join(chain(struct.unpack_from("<I", data, 48)[0], fat))
        for offset in range(0, len(directory), 128):
            entry = directory[offset:offset + 128]
            length = struct.unpack_from("<H", entry, 64)[0]
            if entry[66] == 2 and 2 <= length <= 64 and length % 2 == 0:
                if entry[:length - 2].decode("utf-16-le") == "WordDocument":
                    return True
    except (ExtractionError, struct.error, UnicodeError):
        return False
    return False


def detect_format(data: bytes) -> str:
    if len(data) > MAX_INPUT_BYTES:
        raise ExtractionError("Input resource limit exceeded")
    if data.startswith(OLE_MAGIC):
        if _is_word_compound(data):
            return "DOC"
        raise ExtractionError("OLE container has no readable WordDocument stream")
    if data.startswith(b"PK\x03\x04"):
        parts = read_package(data)
        if "[Content_Types].xml" in parts and "word/document.xml" in parts:
            types = parse_xml(parts["[Content_Types].xml"])
            if any(e.get("PartName") == "/word/document.xml" and e.get("ContentType") == MAIN_TYPE for e in types):
                return "DOCX"
        raise ExtractionError("ZIP container has no DOCX main document")
    raise ExtractionError("Unsupported document signature")


_PROFILE = '''<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
 <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
  <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
  <prop oor:name="DisableActiveContent" oor:op="fuse"><value>true</value></prop>
  <prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop>
  <prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>
 </item>
 <item oor:path="/org.openoffice.Office.Writer/Content/Update">
  <prop oor:name="Link" oor:op="fuse"><value>2</value></prop>
  <prop oor:name="Field" oor:op="fuse"><value>false</value></prop>
 </item>
</oor:items>'''


def office_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONINSPECT", "VIRTUAL_ENV"):
        environment.pop(name, None)
    return environment


@dataclass(frozen=True)
class LibreOfficeConverter:
    executable: Path
    timeout_seconds: float = 90

    @classmethod
    def discover(cls) -> LibreOfficeConverter:
        candidate = shutil.which("soffice")
        if not candidate and os.name == "nt":
            candidate = str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "LibreOffice/program/soffice.exe")
        if not candidate or not Path(candidate).is_file():
            raise ExtractionError("DOC conversion requires an installed LibreOffice executable")
        return cls(Path(candidate))

    def convert(self, data: bytes) -> tuple[bytes, ConversionEvent]:
        if detect_format(data) != "DOC":
            raise ExtractionError("Legacy conversion expects a DOC signature")
        started = monotonic()
        source_hash = sha256(data).hexdigest()
        version = "unavailable"
        command: tuple[str, ...] = ()
        output = b""
        stdout = stderr = ""
        returncode = None
        status = "failed"
        failure: Exception | None = None
        try:
            # Inspect local build metadata; --version can attach to an unrelated GUI instance.
            config = configparser.ConfigParser(interpolation=None)
            config.read(self.executable.parent / "version.ini", encoding="utf-8")
            version = "buildid=" + config.get("Version", "buildid", fallback="unknown")
            version += "; executable_sha256=" + sha256(self.executable.read_bytes()).hexdigest()
            with TemporaryDirectory(prefix="kp_extract_doc_") as tmp:
                directory = Path(tmp)
                profile = directory / "profile"
                (profile / "user").mkdir(parents=True)
                (profile / "user/registrymodifications.xcu").write_text(_PROFILE, encoding="utf-8")
                source = directory / "input.doc"
                source.write_bytes(data)
                destination = directory / "converted"
                destination.mkdir()
                command = (str(self.executable), f"-env:UserInstallation={profile.as_uri()}",
                           "--headless", "--nologo", "--nodefault", "--norestore", "--nolockcheck",
                           "--convert-to", "docx:Office Open XML Text", "--outdir", str(destination), str(source))
                # A separate process group allows timeout cleanup without touching a user's Office process.
                options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
                job = None
                if os.name == "nt":
                    import win32api
                    import win32job
                    job = win32job.CreateJobObject(None, "")
                    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
                    limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
                process = None
                try:
                    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                               cwd=directory, env=office_environment(), **options)
                    if job is not None:
                        win32job.AssignProcessToJobObject(job, int(process._handle))
                except Exception:
                    if process is not None:
                        process.kill()
                        process.communicate(timeout=15)
                    if job is not None:
                        win32api.CloseHandle(job)
                    raise
                try:
                    out, err = process.communicate(timeout=self.timeout_seconds)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        win32job.TerminateJobObject(job, 1)
                    else:
                        import signal
                        os.killpg(process.pid, signal.SIGKILL)
                    out, err = process.communicate(timeout=15)
                    stdout, stderr = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
                    status = "timeout"
                    raise ExtractionError("DOC conversion timed out")
                finally:
                    if job is not None:
                        win32api.CloseHandle(job)
                returncode = process.returncode
                stdout, stderr = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
                target = destination / "input.docx"
                if returncode != 0 or not target.is_file():
                    raise ExtractionError("DOC conversion did not produce a document")
                if target.stat().st_size > MAX_INPUT_BYTES:
                    raise ExtractionError("Converted document resource limit exceeded")
                output = target.read_bytes()
                if detect_format(output) != "DOCX":
                    raise ExtractionError("Converted document signature is invalid")
                status = "converted"
        except Exception as exc:  # Preserve a journal for backend/OS failures as well.
            failure = exc
        # Journal arguments use stable placeholders; no temporary pathname is part of identity.
        logged_command = tuple("<isolated-profile>" if v.startswith("-env:UserInstallation=") else
                               "<input.doc>" if v.endswith("input.doc") else
                               "<output-directory>" if v.endswith("converted") else v for v in command)
        event = ConversionEvent(str(self.executable), version, source_hash, sha256(output).hexdigest() if output else None,
                                "DOC", "DOCX", logged_command, status, returncode, stdout, stderr,
                                round(monotonic() - started, 6))
        if failure:
            raise ConversionError(str(failure), event) from failure
        return output, event
