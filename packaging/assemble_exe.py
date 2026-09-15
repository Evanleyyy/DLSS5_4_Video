import hashlib
from pathlib import Path
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
archive = ROOT / 'build' / 'payload.7z'
with archive.open('rb') as archive_file:
    digest = hashlib.file_digest(archive_file, 'sha256').digest()
build_info = ROOT / 'build' / 'BuildInfo.cs'
build_info.write_text('internal static class BuildInfo { public const string Id = "' +
                      digest.hex()[:20] + '"; }', encoding='utf-8')
extractor = ROOT / 'build' / '7zip' / 'x64' / '7za.exe'
stub = ROOT / 'build' / 'launcher.exe'
compiler = r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
subprocess.run([compiler, '/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/codepage:65001',
                '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
                '/out:' + str(stub), '/resource:' + str(extractor) + ',extractor',
                str(ROOT / 'packaging' / 'LargePackageLauncher.cs'), str(build_info)], check=True)
output = ROOT / 'output' / 'DLSS5_Standalone.exe'
output.parent.mkdir(exist_ok=True)
assert stub.stat().st_size + archive.stat().st_size + 64 < 2**32, 'Windows EXE exceeds 4 GiB'
with output.open('wb') as target:
    with stub.open('rb') as source:
        shutil.copyfileobj(source, target, 8 * 1024 * 1024)
    offset = target.tell()
    with archive.open('rb') as source:
        shutil.copyfileobj(source, target, 8 * 1024 * 1024)
    target.write(struct.pack('<16sqq32s', b'DLSS5PACKv1!!!!!', offset, archive.stat().st_size, digest))
print(output)
print('EXE bytes:', output.stat().st_size)
print('Payload SHA256:', digest.hex())
