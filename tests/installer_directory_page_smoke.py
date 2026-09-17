"""Compile the real setup configuration with a probe that stops before installing.

Run as the desktop user to exercise the existing installation's registry state.
The probe has no payload, shortcuts or registration, and blocks installation.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import winreg

ROOT = Path(__file__).resolve().parents[1]


def verify(update=False, fresh=False, auto_dir_page=False):
    directory = ROOT / 'logs' / ('installer-directory-' + str(time.time_ns()))
    directory.mkdir(parents=True)
    source = (ROOT / 'packaging/DLSS5_Installer.iss').read_text(encoding='utf-8')
    script = source.split('[Languages]', 1)[0]
    if auto_dir_page:
        script = script.replace('DisableDirPage=no', 'DisableDirPage=auto')
    if fresh:
        script = script.replace('{7BE891EC-ED7C-4745-9DFE-4523CDF01B9E}',
                                '{21AAC3A0-D547-48F0-A08D-74350A4C5FE6}')
        previous = None
    else:
        # Require the real prior-install state, so the regression cannot pass
        # merely because it ran in a sandbox user's empty registry.
        key = (r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
               r'\{7BE891EC-ED7C-4745-9DFE-4523CDF01B9E}_is1')
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as handle:
            previous = winreg.QueryValueEx(handle, 'Inno Setup: App Path')[0]
    script += '\nUninstallable=no\nDisableReadyPage=no\n'
    trace = directory / 'pages.txt'
    chosen = directory / '目标 中文 路径'
    literal = lambda path: str(path).replace("'", "''")
    script += '''
[Code]
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpReady then
    PostMessage(WizardForm.CancelButton.Handle, $00F5, 0, 0)
  else
  begin
    if CurPageID = wpLicense then WizardForm.LicenseAcceptedRadio.Checked := True;
    PostMessage(WizardForm.NextButton.Handle, $00F5, 0, 0);
  end;
end;
procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  Cancel := True;
  Confirm := False;
end;
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  SaveStringToFile('%s', 'page=' + IntToStr(CurPageID) + #13#10, True);
  if CurPageID = wpSelectDir then
  begin
    SaveStringToFile('%s', 'DIRECTORY_PAGE_VISIBLE=' + WizardDirValue + #13#10, True);
    if WizardForm.DirEdit.Enabled and WizardForm.DirBrowseButton.Enabled then
      SaveStringToFile('%s', 'EDITABLE=yes' + #13#10, True);
    WizardForm.DirEdit.Text := '%s';
    SaveStringToFile('%s', 'CHOSEN=' + WizardDirValue + #13#10, True);
  end;
  Result := CurPageID <> wpReady;
end;
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := 'Verification only. Installation is blocked.';
end;
%s
''' % (literal(trace), literal(trace), literal(trace), literal(chosen), literal(trace),
       source[source.index('function DefaultLocation('):])
    probe = directory / 'probe.iss'
    probe.write_text(script, encoding='utf-8-sig')
    command = [str(ROOT / 'build/inno/ISCC.exe'), '/Q', '/O' + str(directory),
               '/Fdirectory-probe', '/DProjectRoot=' + str(ROOT), '/DAppSource=' + str(ROOT)]
    if update:
        command.append('/DUpdateOnly')
    subprocess.run(command + [str(probe)], check=True, timeout=30, capture_output=True)
    log = directory / 'setup.log'
    environment = {**os.environ, 'TEMP': str(ROOT / 'build/inno-temp'),
                   'TMP': str(ROOT / 'build/inno-temp')}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    run = subprocess.run([str(directory / 'directory-probe.exe'), '/NORESTART', '/LOG=' + str(log)],
                         env=environment, timeout=30, startupinfo=startup,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    pages = trace.read_text(encoding='mbcs')
    assert 'DIRECTORY_PAGE_VISIBLE=' in pages, pages
    assert 'EDITABLE=yes' in pages, pages
    assert 'CHOSEN=' + str(chosen) in pages, pages
    if previous:
        assert 'DIRECTORY_PAGE_VISIBLE=' + previous in pages, pages
    assert run.returncode != 0, 'The probe must stop before installation'
    assert not chosen.exists(), 'The probe must not create the installation directory'
    assert 'Starting the installation process.' not in log.read_text(encoding='utf-8-sig')
    result = dict(status='passed', update=update, fresh=fresh, previous=previous,
                  directory_page=True, editable=True, chosen=str(chosen), no_install=True)
    (directory / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--update', action='store_true')
    parser.add_argument('--fresh', action='store_true')
    parser.add_argument('--auto-dir-page', action='store_true',
                        help='Reproduce the old auto-skip configuration as a negative control')
    args = parser.parse_args()
    verify(args.update, args.fresh, args.auto_dir_page)
