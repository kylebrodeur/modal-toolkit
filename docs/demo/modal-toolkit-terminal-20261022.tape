Set Shell "bash"
Set FontSize 16
Set Width 1200
Set Height 700
Set TypingSpeed 60ms
Set PlaybackSpeed 1
Set Theme {"background":"#1a1510","foreground":"#e8dfd0","accent":"#c98d1a","cursor":"#5a9e80","selection":"#3a3020"}

Type "clear" Enter Sleep 1s
Type "cd /Users/kylebrodeur/workspace/modal-toolkit-system/modal-toolkit" Enter Sleep 1s
Type "echo 'One CLI over the whole Modal family.'" Enter Sleep 2s
Type "uv run mtk doctor" Enter Sleep 22s

Type "clear" Enter Sleep 500ms
Type "echo 'What does the idle fleet cost?'" Enter Sleep 2s
Type "uv run mtk cost | python3 /Users/kylebrodeur/workspace/modal-toolkit-system/tools/demo/cost-table.py" Enter Sleep 16s

Type "clear" Enter Sleep 500ms
Type "echo 'How the pieces connect.'" Enter Sleep 2s
Type "uv run mtk flow" Enter Sleep 14s

Type "clear" Enter Sleep 1s
Type "echo 'github.com/kylebrodeur/modal-toolkit'" Enter Sleep 4s
