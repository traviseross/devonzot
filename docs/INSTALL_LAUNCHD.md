# DEVONzot Launchd Setup Guide

## Installation Steps

1. **Copy the launchd plist file**
   - From the repo: `config/com.devonzot.addnew.plist`
   - To: `~/Library/LaunchAgents/com.devonzot.addnew.plist`

2. **Edit the plist if needed**
   - Update paths for your system (Python, script, logs).

3. **Load the job**
   ```
   launchctl load ~/Library/LaunchAgents/com.devonzot.addnew.plist
   ```

4. **Check logs**
   - `launchd_stdout.log` and `launchd_stderr.log` in your DEVONzot directory.

5. **Unload the job (to stop)**
   ```
   launchctl unload ~/Library/LaunchAgents/com.devonzot.addnew.plist
   ```

## Notes
- Make sure `.env` is present and correctly configured.
- The script will run in loop mode as specified in the plist.
- For troubleshooting, check log files and plist syntax.

---

For full installation and usage, see `docs/README.md`.
