-- ═══════════════════════════════════════════════════════════════════════════
--  INSTALL — FLExText Concatenator
-- ═══════════════════════════════════════════════════════════════════════════
--
--  ─────────────────────────────────────────────────────────────────────────
--   WHAT YOU DO — three clicks
--  ─────────────────────────────────────────────────────────────────────────
--
--    1.  Click the  ▶  Run button at the top of this window
--        (or press Command-R).
--
--    2.  A box asks whether to install. Click  Yes.
--        (If the app is already installed, a second box asks whether to
--         replace it. Click  Replace.)
--
--    3.  A box says it is done. Click  Done  —  or  Show me  to open your
--        Applications folder with the app selected.
--
--  That is everything. You do not need to drag anything, open Terminal, or
--  change any settings.
--
--  ─────────────────────────────────────────────────────────────────────────
--   WHAT THE SCRIPT DOES — you do not do these
--  ─────────────────────────────────────────────────────────────────────────
--
--    •  Clears the macOS quarantine flag that blocks unsigned apps.
--    •  Copies the app into your Applications folder.
--    •  Ejects the disk image (a second or two after you click Done).
--    •  Closes this document, and quits Script Editor if nothing else is
--       open in it.
--
--  So this window closing on its own means it WORKED — it is not a crash,
--  and you will not be asked to save anything.
--
--  Afterwards: open FLExText Concatenator from your Applications folder or
--  from Launchpad.
--
--  ─────────────────────────────────────────────────────────────────────────
--   IF ANYTHING IS LEFT OVER — how to finish by hand
--  ─────────────────────────────────────────────────────────────────────────
--
--  macOS does not always let a script close the window it is running in, so
--  this window or the disk image may still be here. That changes nothing
--  about the installation — the app is already in your Applications folder.
--  To tidy up yourself:
--
--    •  Close this window ......  press  Command-W
--                                 (if asked whether to save, choose Don't Save)
--    •  Quit Script Editor .....  press  Command-Q
--    •  Eject the disk image ...  find "FLExText Concatenator" in the Finder
--                                 sidebar, under Locations, and click the
--                                 ⏏ button beside it — or drag it to the Trash
--
--  Then delete the .dmg file from your Downloads folder. Nothing is left
--  behind after that.
--
--  ─────────────────────────────────────────────────────────────────────────
--   WHY AN INSTALL SCRIPT AT ALL
--  ─────────────────────────────────────────────────────────────────────────
--
--  This app is not signed with a paid Apple certificate, so macOS quarantines
--  it and refuses to open it. This file is a script DOCUMENT, not a program,
--  so macOS does not block it — Script Editor does the running, and Script
--  Editor is already trusted.
--
--  Nothing runs in the background and nothing is installed except the app
--  itself. Everything the script does is in the lines below; it is short
--  enough to read first, and you are welcome to.
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
-- Success is announced BEFORE any tidying up, and it says what the tidying
-- will be — the previous wording told the user to eject the disk image
-- themselves moments before the script ejected it, and never mentioned that
-- the window was about to close.
display dialog "✓  " & displayName & " is installed." & return & return & ¬
	"Open it from your Applications folder or from Launchpad. You will not see any security warnings." & return & return & ¬
	"When you close this box the script tidies up: the disk image ejects itself after a second or two, and this window closes." & return & return & ¬
	"If either is still here afterwards, that is harmless — the app is already installed. To finish by hand: press Command-W to close this window (Don't Save), Command-Q to quit Script Editor, and click the ⏏ beside the disk image in the Finder sidebar. Then delete the .dmg from Downloads." ¬
	buttons {"Show me", "Done"} default button "Show me" with title "Install " & displayName
set choice to button returned of result

if choice is "Show me" then
	tell application "Finder"
		reveal POSIX file destPath as alias
		activate
	end tell
end if

-- ── 7. Tidy up ─────────────────────────────────────────────────────────────
--
-- Two things make this harder than it looks, both learned the hard way:
--
--   * This document lives ON the disk image, so Script Editor holds a file
--     handle open there and a plain `hdiutil detach` fails with "Resource
--     busy". Verified. `-force` works. Scheduling it in a detached shell also
--     lets it run after this script has let go.
--
--   * You cannot reliably quit the application that is currently running your
--     script — the quit event queues but is never processed. So quitting is
--     best-effort, and the eject is scheduled FIRST so it still happens even
--     if closing the document ends this script early.

if sourcePath starts with "/Volumes/" then
	try
		set volName to do shell script "echo " & quoted form of sourcePath & " | cut -d/ -f3"
		do shell script "(/bin/sleep 2; /usr/bin/hdiutil detach " & ¬
			quoted form of ("/Volumes/" & volName) & " -force) >/dev/null 2>&1 &"
	end try
end if

-- Close only the document that came off the disk image. Closing every
-- document would throw away whatever else the user had open in Script Editor.
try
	tell application "Script Editor"
		repeat with d in (every document)
			try
				if (path of d) contains "/Volumes/" then close d saving no
			end try
		end repeat
		if (count of documents) is 0 then quit
	end tell
end try
