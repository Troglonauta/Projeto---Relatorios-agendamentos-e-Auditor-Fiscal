import os
import re
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext


def caminho_recurso(nome_arquivo):
    """Resolve o caminho de um recurso tanto em dev quanto dentro do .exe.

    O PyInstaller (--onefile) extrai os recursos numa pasta temporária
    apontada por sys._MEIPASS; em desenvolvimento, usa a pasta do script.
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nome_arquivo)

# ---------------------------------------------------------------------------
# Lógica de extração da Chave de Acesso (44 dígitos)
# ---------------------------------------------------------------------------
# Os XMLs da Sefaz variam de layout e os namespaces costumam dar dor de cabeça
# ao usar xml.etree.ElementTree. Por isso a extração é feita por Regex sobre o
# texto bruto do arquivo, cobrindo os padrões conhecidos + um fallback genérico.
#
#   NFe -> chave no atributo Id da tag <infNFe Id="NFe<44 dígitos>">
#   CTe -> chave dentro da tag <chCTe><44 dígitos></chCTe>
#          (ou no atributo Id da tag <infCte Id="CTe<44 dígitos>">)
# ---------------------------------------------------------------------------

_PADROES_CHAVE = [
    # Atributo Id de NFe ou CTe: Id="NFe2926..." / Id="CTe2926..."
    re.compile(r'Id\s*=\s*["\'](?:NFe|CTe)(\d{44})["\']', re.IGNORECASE),
    # Tag dedicada do CTe: <chCTe>2926...</chCTe>
    re.compile(r'<\s*chCTe\s*>\s*(\d{44})\s*<\s*/\s*chCTe\s*>', re.IGNORECASE),
    # Tag dedicada da NFe (layout antigo): <chNFe>2926...</chNFe>
    re.compile(r'<\s*chNFe\s*>\s*(\d{44})\s*<\s*/\s*chNFe\s*>', re.IGNORECASE),
]

# Fallback: qualquer sequência isolada de exatamente 44 dígitos.
_PADRAO_FALLBACK = re.compile(r'(?<!\d)(\d{44})(?!\d)')


def extrair_chave_acesso(caminho_completo):
    """Lê o XML como texto e retorna a chave de 44 dígitos, ou None.

    Não levanta exceção de parsing: se o conteúdo não puder ser lido ou a
    chave não for encontrada, retorna None para o chamador contabilizar o erro.
    """
    try:
        with open(caminho_completo, 'r', encoding='utf-8', errors='ignore') as arquivo:
            conteudo = arquivo.read()
    except OSError:
        return None

    # Tenta os padrões específicos primeiro (mais confiáveis).
    for padrao in _PADROES_CHAVE:
        correspondencia = padrao.search(conteudo)
        if correspondencia:
            return correspondencia.group(1)

    # Último recurso: encontra qualquer chave de 44 dígitos solta no XML.
    correspondencia = _PADRAO_FALLBACK.search(conteudo)
    if correspondencia:
        return correspondencia.group(1)

    return None


class RenomeadorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Renomeador de Documentos v4.0")
        self.root.geometry("650x450")

        # Ícone da janela (Fertimaxi) — silencioso se o arquivo não existir
        try:
            self.root.iconbitmap(caminho_recurso("fertimaxi.ico"))
        except Exception:
            pass

        self.arquivos_selecionados = []

        # --- Interface Topo ---
        self.btn_selecionar = tk.Button(root, text="Selecionar Arquivos...", command=self.selecionar_arquivos)
        self.btn_selecionar.pack(pady=(10, 5), fill=tk.X, padx=10)

        self.lbl_info = tk.Label(root, text="Nenhum arquivo selecionado.", anchor="w")
        self.lbl_info.pack(fill=tk.X, padx=10)

        # --- Área de Log (Quadro Branco) ---
        self.txt_log = scrolledtext.ScrolledText(root, height=15, state=tk.DISABLED)
        self.txt_log.pack(pady=5, fill=tk.BOTH, expand=True, padx=10)

        # --- Interface Base (Botões Inferiores) ---
        frame_bottom = tk.Frame(root)
        frame_bottom.pack(fill=tk.X, padx=10, pady=(5, 10))

        self.btn_renomear = tk.Button(frame_bottom, text="Renomear Arquivos", state=tk.DISABLED, command=self.renomear_arquivos)
        self.btn_renomear.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))

        self.btn_info = tk.Button(frame_bottom, text=" i ", width=3, command=self.mostrar_info)
        self.btn_info.pack(side=tk.RIGHT)

        # --- Barra de Status ---
        self.lbl_status = tk.Label(root, text="Pronto. Selecione os arquivos para começar.", bd=1, relief=tk.SUNKEN, anchor="w")
        self.lbl_status.pack(side=tk.BOTTOM, fill=tk.X)

    def log(self, mensagem):
        """Função auxiliar para escrever no quadro branco do aplicativo"""
        self.txt_log.config(state=tk.NORMAL)
        self.txt_log.insert(tk.END, mensagem + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.config(state=tk.DISABLED)

    def selecionar_arquivos(self):
        diretorio_inicial = r"C:\Users\harllynsson.maximo\VIDEOS"
        caminhos = filedialog.askopenfilenames(
            title="Selecione os arquivos XML",
            initialdir=diretorio_inicial,
            filetypes=[("Arquivos XML", "*.xml")]
        )

        if caminhos:
            self.arquivos_selecionados = list(caminhos)
            self.lbl_info.config(text=f"{len(self.arquivos_selecionados)} arquivo(s) selecionado(s).")
            self.lbl_status.config(text="Arquivos carregados. Aguardando comando para renomear.")
            self.btn_renomear.config(state=tk.NORMAL)

            self.log(f"--- {len(self.arquivos_selecionados)} arquivo(s) na fila ---")
            for caminho in self.arquivos_selecionados:
                self.log(os.path.basename(caminho))
            self.log("")  # Pula linha
        else:
            self.lbl_status.config(text="Operação de seleção cancelada.")

    def renomear_arquivos(self):
        self.lbl_status.config(text="Processando...")
        self.btn_renomear.config(state=tk.DISABLED)
        self.btn_selecionar.config(state=tk.DISABLED)
        self.root.update()

        sucessos = 0
        erros = 0

        self.log("--- INICIANDO RENOMEAÇÃO ---")

        for caminho_completo in self.arquivos_selecionados:
            diretorio = os.path.dirname(caminho_completo)
            nome_arquivo = os.path.basename(caminho_completo)

            try:
                chave = extrair_chave_acesso(caminho_completo)

                if chave:
                    novo_nome = f"{chave}.xml"
                    novo_caminho = os.path.join(diretorio, novo_nome)

                    # Já está no nome certo? Considera sucesso e segue.
                    if os.path.abspath(novo_caminho) == os.path.abspath(caminho_completo):
                        self.log(f"[OK] Já nomeado corretamente: {novo_nome}")
                        sucessos += 1
                    elif os.path.exists(novo_caminho):
                        self.log(f"[AVISO] Já existe: {novo_nome}")
                        erros += 1
                    else:
                        os.rename(caminho_completo, novo_caminho)
                        self.log(f"[OK] {nome_arquivo} -> {novo_nome}")
                        sucessos += 1
                else:
                    self.log(f"[ERRO] Chave não encontrada: {nome_arquivo}")
                    erros += 1

            except OSError as exc:
                # Falha ao renomear (arquivo em uso, permissão, etc.) — segue a fila.
                self.log(f"[ERRO] Falha ao renomear {nome_arquivo}: {exc}")
                erros += 1
            except Exception as exc:
                # Qualquer erro inesperado não pode parar o processamento dos demais.
                self.log(f"[ERRO] Inesperado em {nome_arquivo}: {exc}")
                erros += 1

        self.log("--- PROCESSO CONCLUÍDO ---")
        self.lbl_status.config(text=f"Concluído: {sucessos} renomeados, {erros} não processados.")

        # Reseta os botões e a fila após finalizar
        self.arquivos_selecionados = []
        self.btn_selecionar.config(state=tk.NORMAL)
        self.lbl_info.config(text="Nenhum arquivo selecionado.")
        messagebox.showinfo("Finalizado", f"Processo concluído!\n\nSucessos: {sucessos}\nErros/Ignorados: {erros}")

    def mostrar_info(self):
        messagebox.showinfo(
            "Sobre o Sistema",
            "Renomeador de XML (Chave de Acesso NFe/CTe)\nVersão: 4.0\n\nDesenvolvido por Harllynsson.Maximo"
        )


if __name__ == "__main__":
    root = tk.Tk()
    app = RenomeadorApp(root)
    root.mainloop()
