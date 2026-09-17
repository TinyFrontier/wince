# Render with: vhs assets/demo.tape   (needs TYPESAFE_API_KEY in .env or the environment)
Output assets/demo.gif
Set Shell "zsh"
Set FontSize 15
Set Width 960
Set Height 560
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms

Hide
Type "export PATH=$PWD/.venv/bin:$PATH && clear"
Enter
Sleep 500ms
Show

Type "wince check --diff examples/3-big-pr-permissions.patch"
Sleep 600ms
Enter
Sleep 5s

Type "wince check --diff examples/1-readme-typo.patch"
Sleep 500ms
Enter
Sleep 3s
