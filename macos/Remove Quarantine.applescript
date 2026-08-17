-- FLExText Concatenator — Remove Quarantine
--
-- macOS quarantines anything downloaded from the internet, and because this
-- app is not signed with an Apple Developer certificate, Gatekeeper refuses
-- to open it until the quarantine flag is cleared.
--
-- HOW TO USE
--   1. Double-click this file. It opens in Script Editor.
--   2. Press the Run button (or Command-R).
--   3. Answer Yes when asked.
--
-- This is a script DOCUMENT, not an application, so Gatekeeper does not block
-- it — Script Editor is the program doing the running, and it is already
-- trusted. Nothing is installed and nothing runs in the background.

set appName to "flextext-concat.app"
set found to {}

repeat with f in {path to downloads folder, path to applications folder, path to desktop folder}
	try
		set candidate to (f as text) & appName
		alias candidate
		set end of found to candidate
	end try
end repeat

if (count of found) is 0 then
	display dialog "Could not find " & appName & " in Downloads, Applications or the Desktop." & return & return & "Pick it manually?" buttons {"Cancel", "Choose…"} default button "Choose…" cancel button "Cancel" with icon caution with title "Remove Quarantine"
	set target to POSIX path of (choose file with prompt "Select " & appName)
else
	set target to POSIX path of (item 1 of found)
end if

display dialog "Remove the macOS quarantine flag from:" & return & return & "   " & target & return & return & "Quarantine is the check that warns you before running software downloaded from the internet. Remove it only for software you built yourself or got from someone you trust." buttons {"No", "Yes"} default button "No" cancel button "No" with icon caution with title "Remove Quarantine"

try
	do shell script "/usr/bin/xattr -dr com.apple.quarantine " & quoted form of target
on error errMsg
	display dialog "Could not remove the quarantine flag." & return & return & errMsg buttons {"OK"} default button "OK" with icon stop with title "Remove Quarantine"
	return
end try

display dialog "Done — " & appName & " will now open normally." & return & return & "You only need to do this once per download." buttons {"OK"} default button "OK" with title "Remove Quarantine"
