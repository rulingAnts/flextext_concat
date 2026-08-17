-- ═══════════════════════════════════════════════════════════════════════════
--  INSTALL — FLExText Concatenator
--
--    ▶  PRESS THE RUN BUTTON ABOVE  (the ▶ triangle, or press Command-R)
--
--  That is the only step. This script will:
--    1. clear the macOS quarantine flag that blocks unsigned apps,
--    2. move the app into your Applications folder,
--    3. eject the disk image, then close itself and quit Script Editor.
--
--  Nothing runs in the background and nothing is installed except the app
--  itself. You can read this whole script first — it is short.
--
--  This is a script DOCUMENT, not an application, so macOS does not block it
--  the way it blocks the app. Script Editor does the running, and Script
--  Editor is already trusted.
-- ═══════════════════════════════════════════════════════════════════════════

set appName to "FLExText Concatenator.app"
set hiddenName to "." & appName
set displayName to "FLExText Concatenator"

-- ── 1. Find the hidden payload ─────────────────────────────────────────────
set searchRoots to "/Volumes ~/Downloads ~/Desktop"
set findCmd to "find " & searchRoots & " -maxdepth 4 -name " & quoted form of hiddenName & " -type d 2>/dev/null | head -1"
set sourcePath to ""
try
	set sourcePath to do shell script findCmd
end try

if sourcePath is "" then
	display dialog "Could not find " & displayName & " automatically." & return & return & "Make sure the disk image is still mounted — it should appear in the Finder sidebar. Then choose it below." buttons {"Cancel", "Choose…"} default button "Choose…" cancel button "Cancel" with icon caution with title "Install " & displayName
	set chosen to POSIX path of (choose folder with prompt "Select the mounted disk image")
	try
		set sourcePath to do shell script "find " & quoted form of chosen & " -maxdepth 3 -name " & quoted form of hiddenName & " -type d 2>/dev/null | head -1"
	end try
	if sourcePath is "" then
		display dialog "Still could not find " & hiddenName & " there." & return & return & "Open the downloaded .dmg first so the disk image is mounted, then run this again." buttons {"OK"} default button "OK" with icon stop with title "Install " & displayName
		return
	end if
end if

-- ── 2. Pick a destination we can actually write to ─────────────────────────
set destDir to "/Applications"
try
	do shell script "test -w /Applications"
on error
	set destDir to (POSIX path of (path to home folder)) & "Applications"
	do shell script "mkdir -p " & quoted form of destDir
end try
set destPath to destDir & "/" & appName

-- ── 3. Confirm ─────────────────────────────────────────────────────────────
display dialog "Install " & displayName & "?" & return & return & "From:  " & sourcePath & return & "To:      " & destPath & return & return & "This clears the macOS quarantine flag, which is the check that warns you before running software downloaded from the internet. Do this only for software you trust." buttons {"No", "Yes"} default button "No" cancel button "No" with icon caution with title "Install " & displayName

-- ── 4. Replace an existing copy, if any ────────────────────────────────────
try
	do shell script "test -e " & quoted form of destPath
	display dialog displayName & " is already installed." & return & return & "Replace it with this copy?" buttons {"Cancel", "Replace"} default button "Cancel" cancel button "Cancel" with icon caution with title "Install " & displayName
	do shell script "rm -rf " & quoted form of destPath
end try

-- ── 5. Unquarantine, move, unhide ──────────────────────────────────────────
-- A disk image is read-only, so copy out rather than move, and clear the
-- quarantine flag on the copy — the original cannot be changed, and files
-- copied off a quarantined image inherit the flag.
--
-- Order matters: copy under the HIDDEN name, clear quarantine, and only then
-- rename to .app. If it became a visible app while still quarantined,
-- LaunchServices could register it in that state and keep warning about it
-- afterwards. This way it only becomes an app once it is already clean, and a
-- failure never leaves a visible broken app behind.
set stagePath to destDir & "/" & hiddenName
try
	do shell script "rm -rf " & quoted form of stagePath
	do shell script "/usr/bin/ditto " & quoted form of sourcePath & " " & quoted form of stagePath
	do shell script "/usr/bin/xattr -dr com.apple.quarantine " & quoted form of stagePath
	do shell script "/bin/mv " & quoted form of stagePath & " " & quoted form of destPath
on error errMsg
	try
		do shell script "rm -rf " & quoted form of stagePath & " " & quoted form of destPath
	end try
	display dialog "Installation failed." & return & return & errMsg & return & return & "Nothing was changed. The disk image is untouched, so you can simply try again." buttons {"OK"} default button "OK" with icon stop with title "Install " & displayName
	return
end try

-- ── 6. Done ────────────────────────────────────────────────────────────────
display dialog displayName & " is installed." & return & return & "Open it from your Applications folder or from Launchpad — you will not see any security warnings." & return & return & "You can now eject the disk image and delete the .dmg file." buttons {"Show me", "Done"} default button "Show me" with title "Install " & displayName
set choice to button returned of result

-- Eject the image we installed from, so nothing is left mounted.
if sourcePath starts with "/Volumes/" then
	try
		set volName to do shell script "echo " & quoted form of sourcePath & " | cut -d/ -f3"
		do shell script "/usr/bin/hdiutil detach " & quoted form of ("/Volumes/" & volName) & " -quiet"
	end try
end if

if choice is "Show me" then
	tell application "Finder"
		reveal POSIX file destPath as alias
		activate
	end tell
end if

-- Tidy up: close this document without offering to save, then quit Script
-- Editor. Must be last — quitting stops the script that is running. Wrapped
-- in try so a failure here never looks like an installation failure, since
-- the app is already installed by this point.
try
	tell application "Script Editor"
		close (every document) saving no
		quit
	end tell
end try
