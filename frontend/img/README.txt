============================================================
 IDENTIDADE VISUAL FERTIMAXI - Salvar nesta pasta
============================================================

Salve os 3 arquivos com EXATAMENTE estes nomes (case-sensitive):

  favicon.png     -> Icone da gota (16x16 a 64x64). Aparece na aba do navegador.
  logo.png        -> Gota + texto FERTIMAXI. Tamanho ideal: 200x60. Aparece no menu lateral.
  background.png  -> Fundo abstrato verde (Full HD ou superior). Aparece atras do card de login.

Caminhos consumidos pelo HTML/CSS:
  /static/img/favicon.png
  /static/img/logo.png
  /static/img/background.png

Como copiar (PowerShell, exemplo):
  Copy-Item "C:\caminho\dos\arquivos\favicon.png"    "C:\protheus-reports\frontend\img\favicon.png"
  Copy-Item "C:\caminho\dos\arquivos\logo.png"       "C:\protheus-reports\frontend\img\logo.png"
  Copy-Item "C:\caminho\dos\arquivos\background.png" "C:\protheus-reports\frontend\img\background.png"

Nao precisa reiniciar o servidor para imagens novas — apenas dar Ctrl+F5 no navegador.
