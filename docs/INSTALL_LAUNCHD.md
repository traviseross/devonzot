# DEVONzot Launchd Setup Guide

## Installation Steps

1. **Copy the launchd plist file**
   - From the repo: `config/com.devonzot.addnew.plist`
   - To: `~/Library/LaunchAgents/com.devonzot.addnew.plist`

2. **Edit the plist if needed**
   - Update paths for your system (Python interpreter, script path, log paths).
   - The script path must include the `src/` prefix, for example:
     ```
     /Users/yourname/DEVONzot/src/devonzot_service.py
     ```
   - The Python path should point to the venv interpreter:
     ```
     /Users/yourname/DEVONzot/venv/bin/python3
     ```

3. **Load the job**

   macOS 12 and later (recommended):
   ```
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.devonzot.addnew.plist
   ```

   macOS 11 and earlier (deprecated fallback):
   ```
   launchctl load ~/Library/LaunchAgents/com.devonzot.addnew.plist
   ```

4. **Check logs**
   - `launchd_stdout.log` and `launchd_stderr.log` in your DEVONzot directory.
   - Also check `service.log` for the service's own runtime output.

5. **Unload the job (to stop)**

   macOS 12 and later (recommended):
   ```
   launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.devonzot.addnew.plist
   ```

   macOS 11 and earlier (deprecated fallback):
   ```
   launchctl unload ~/Library/LaunchAgents/com.devonzot.addnew.plist
   ```

## Notes

- Make sure `.env` is present and correctly configured before loading the job. The service reads credentials at startup.
- DEVONthink 3 must be running when the launchd job fires. Consider using a `StartCalendarInterval` key so the job only runs during hours when DEVONthink is likely open, or pair it with a Login Item that opens DEVONthink at login.
- The script runs in the mode specified in the plist `ProgramArguments` array (e.g., `--service` for perpetual polling or omit for the default streaming mode).
- For troubleshooting, check `launchd_stdout.log`, `launchd_stderr.log`, and `service.log`, and verify plist syntax with:
  ```
  plutil -lint ~/Library/LaunchAgents/com.devonzot.addnew.plist
  ```

---

For full installation and usage, see `docs/README.md`.
