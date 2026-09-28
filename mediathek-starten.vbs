Dim url, running, WshShell, isAutostart
url = "http://localhost:8765"
isAutostart = (WScript.Arguments.Count > 0 And WScript.Arguments(0) = "autostart")

' Prüfen ob Server schon läuft (kein Heartbeat mehr - Server läuft dauerhaft,
' einfacher Check ob die Startseite antwortet reicht)
Dim objHTTP
Set objHTTP = CreateObject("MSXML2.XMLHTTP")
On Error Resume Next
objHTTP.Open "GET", url & "/", False
objHTTP.Send
running = (Err.Number = 0 And objHTTP.Status = 200)
On Error GoTo 0

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)

If running Then
    ' Server läuft schon - nur bei manuellem Doppelklick den Browser öffnen,
    ' beim automatischen Windows-Login-Start nichts tun.
    If Not isAutostart Then
        WshShell.Run "cmd /c start " & url, 0, False
    End If
Else
    If isAutostart Then
        WshShell.Run "python mediathek.py --autostart", 0, False
    Else
        WshShell.Run "python mediathek.py", 0, False
    End If
End If
