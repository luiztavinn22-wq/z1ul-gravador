"""
Z1UL_Setup.exe - instalador online do Z1UL GRAVADOR

Abre uma janela com a marca, baixa a versão mais recente do GitHub,
instala em %LOCALAPPDATA%\\Programs, cria atalhos e abre o app sozinho.
Não pede permissão de administrador e não tem telas de "Avançar".

Com o argumento --desinstalar, remove o app (usado pelo Painel de Controle).
"""

import os
import re
import ssl
import sys
import time
import queue
import shutil
import ctypes
import zipfile
import tempfile
import threading
import subprocess
import urllib.request
import winreg
import tkinter as tk
from tkinter import messagebox

APP = "Z1UL GRAVADOR"
PASTA_NOME = "Z1UL Gravador"
EXE = "Z1UL Gravador.exe"
PACOTE = "Z1UL_Gravador_app.zip"
CHAVE_DESINSTALAR = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Z1UL Gravador"
DESTINO = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Programs", PASTA_NOME)

SEM_JANELA = 0x08000000
DESACOPLADO = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

PRETO = "#03050A"
CARTAO = "#0A1020"
BORDA = "#162545"
AZUL = "#1F6BFF"
AZUL_HOVER = "#3B82FF"
CIANO = "#00D9FF"
TEXTO = "#E9EFFA"
APAGADO = "#7A89A8"
VERMELHO = "#FF3D60"
VAZIO = "#111A30"


def recurso(nome):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nome)


def ler_recurso(nome):
    try:
        with open(recurso(nome), "r", encoding="utf-8-sig") as f:
            return f.read().strip()
    except OSError:
        return ""


REPO = ler_recurso("repo.txt")  # "usuario/repositorio", gravado pelo GitHub Actions
URL_BASE = f"https://github.com/{REPO}/releases/latest/download/"


class Cancelado(Exception):
    pass


def contexto_ssl():
    ctx = ssl.create_default_context()  # já inclui os certificados do Windows
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


SSL = contexto_ssl()


def abrir_url(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Z1UL-Setup"})
    return urllib.request.urlopen(req, timeout=timeout, context=SSL)


def curl(*args, timeout=120):
    return subprocess.run(["curl.exe", *args], capture_output=True, stdin=subprocess.DEVNULL,
                          creationflags=SEM_JANELA, timeout=timeout)


def baixar_texto(url):
    erros = []
    try:
        with abrir_url(url, timeout=20) as r:
            return r.read().decode("utf-8-sig").strip()
    except Exception as e:
        erros.append(f"Python: {e}")
    try:
        r = curl("-fsSL", "--retry", "2", url, timeout=60)
        if r.returncode == 0:
            return r.stdout.decode("utf-8-sig").strip()
        erros.append(f"curl: {r.stderr.decode('utf-8', 'replace').strip()}")
    except Exception as e:
        erros.append(f"curl: {e}")
    raise RuntimeError(" | ".join(erros))


def powershell(script):
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                   capture_output=True, stdin=subprocess.DEVNULL, creationflags=SEM_JANELA)


def aspas_ps(texto):
    return texto.replace("'", "''")


def criar_atalhos():
    alvo = os.path.join(DESTINO, EXE)
    powershell(f"""
$s = New-Object -ComObject WScript.Shell
foreach ($p in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {{
  $l = $s.CreateShortcut((Join-Path $p 'Z1UL GRAVADOR.lnk'))
  $l.TargetPath = '{aspas_ps(alvo)}'
  $l.WorkingDirectory = '{aspas_ps(DESTINO)}'
  $l.IconLocation = '{aspas_ps(alvo)},0'
  $l.Description = 'Replay instantaneo para jogos'
  $l.Save()
}}""")


def remover_atalhos():
    powershell("""
foreach ($p in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {
  Remove-Item (Join-Path $p 'Z1UL GRAVADOR.lnk') -ErrorAction SilentlyContinue
}""")


def tamanho_pasta_kb(pasta):
    total = 0
    for raiz, _, arquivos in os.walk(pasta):
        for a in arquivos:
            try:
                total += os.path.getsize(os.path.join(raiz, a))
            except OSError:
                pass
    return total // 1024


def registrar(versao):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CHAVE_DESINSTALAR) as k:
        textos = {
            "DisplayName": APP,
            "DisplayVersion": versao,
            "Publisher": "Z1UL",
            "DisplayIcon": os.path.join(DESTINO, EXE),
            "InstallLocation": DESTINO,
            "UninstallString": f'"{os.path.join(DESTINO, "Desinstalar.exe")}" --desinstalar',
        }
        for nome, valor in textos.items():
            winreg.SetValueEx(k, nome, 0, winreg.REG_SZ, valor)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, tamanho_pasta_kb(DESTINO))


def fechar_app():
    subprocess.run(["taskkill", "/f", "/im", EXE], capture_output=True,
                   stdin=subprocess.DEVNULL, creationflags=SEM_JANELA)
    time.sleep(0.5)


def apagar_pasta(pasta):
    for _ in range(10):
        if not os.path.exists(pasta):
            return
        shutil.rmtree(pasta, ignore_errors=True)
        time.sleep(0.4)
    if os.path.exists(pasta):
        raise RuntimeError("Não foi possível substituir a versão antiga. Feche o Z1UL GRAVADOR "
                           "e tente de novo.")


# ======================================================================= janela
class Splash(tk.Tk):
    def __init__(self):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        super().__init__()
        self.escala = self.winfo_fpixels("1i") / 96.0
        self.fila = queue.Queue()
        self.cancelado = False
        self.frac = None
        self.anim = 0
        self.trabalhando = False

        largura, altura = self.S(560), self.S(330)
        x = (self.winfo_screenwidth() - largura) // 2
        y = (self.winfo_screenheight() - altura) // 2
        self.geometry(f"{largura}x{altura}+{x}+{y}")
        self.overrideredirect(True)
        self.configure(bg=BORDA)
        self.title(APP)
        try:
            self.iconbitmap(recurso("z1ul.ico"))
        except Exception:
            pass

        corpo = tk.Frame(self, bg=PRETO)
        corpo.pack(fill="both", expand=True, padx=1, pady=1)

        fechar = tk.Label(corpo, text="✕", bg=PRETO, fg=APAGADO, font=("Segoe UI", 12), cursor="hand2")
        fechar.place(relx=1.0, x=-self.S(14), y=self.S(10), anchor="ne")
        fechar.bind("<Button-1>", lambda e: self.cancelar())
        fechar.bind("<Enter>", lambda e: fechar.configure(fg=TEXTO))
        fechar.bind("<Leave>", lambda e: fechar.configure(fg=APAGADO))

        topo = tk.Frame(corpo, bg=PRETO)
        topo.pack(fill="x", padx=self.S(40), pady=(self.S(48), 0))
        marca = tk.Canvas(topo, width=self.S(64), height=self.S(64), bg=PRETO, highlightthickness=0)
        self._desenhar_marca(marca, self.S(64))
        marca.pack(side="left")
        nome = tk.Frame(topo, bg=PRETO)
        nome.pack(side="left", padx=(self.S(16), 0))
        tk.Label(nome, text="Z1UL", bg=PRETO, fg=TEXTO,
                 font=("Bahnschrift", 30, "bold")).pack(anchor="w")
        tk.Label(nome, text="GRAVADOR", bg=PRETO, fg=CIANO,
                 font=("Bahnschrift", 12, "bold")).pack(anchor="w")

        base = tk.Frame(corpo, bg=PRETO)
        base.pack(fill="x", side="bottom", padx=self.S(40), pady=(0, self.S(34)))

        self.botoes = tk.Frame(base, bg=PRETO)
        self.btn_tentar = self._botao(self.botoes, "Tentar de novo", self.comecar, AZUL, AZUL_HOVER)
        self.btn_tentar.pack(side="right")
        self._botao(self.botoes, "Fechar", self.destroy, CARTAO, BORDA).pack(
            side="right", padx=(0, self.S(8)))

        self.lbl_status = tk.Label(base, text="", bg=PRETO, fg=TEXTO, anchor="w",
                                   font=("Segoe UI Semibold", 12))
        self.lbl_status.pack(fill="x")
        self.lbl_detalhe = tk.Label(base, text="", bg=PRETO, fg=APAGADO, anchor="w", justify="left",
                                    font=("Segoe UI", 9), wraplength=self.S(470))
        self.lbl_detalhe.pack(fill="x", pady=(self.S(2), self.S(12)))
        self.barra = tk.Canvas(base, height=self.S(6), bg=PRETO, highlightthickness=0)
        self.barra.pack(fill="x")

        for w in (corpo, topo, nome, marca):
            w.bind("<ButtonPress-1>", self._pegar)
            w.bind("<B1-Motion>", self._arrastar)

        self.after(10, self._mostrar_na_barra_de_tarefas)
        self.after(30, self._animar)
        self.after(50, self._ler_fila)
        self.after(400, self.comecar)

    def S(self, v):
        return int(v * self.escala)

    def _desenhar_marca(self, c, t):
        k = t / 48
        c.create_rectangle(1, 1, t - 1, t - 1, outline=AZUL, width=max(2, int(2 * k)))
        c.create_rectangle(6 * k, 6 * k, 42 * k, 42 * k, fill="#081633", outline="")
        pontos = [13, 13, 35, 13, 35, 18, 20, 30, 35, 30, 35, 35, 13, 35, 13, 30, 28, 18, 13, 18]
        c.create_polygon([p * k for p in pontos], fill=CIANO, outline="")
        c.create_rectangle(38 * k, 38 * k, t, t, fill=AZUL, outline="")

    def _botao(self, pai, texto, comando, cor, hover):
        b = tk.Label(pai, text=texto, bg=cor, fg=TEXTO, font=("Segoe UI Semibold", 10),
                     padx=self.S(16), pady=self.S(7), cursor="hand2")
        b.bind("<Button-1>", lambda e: comando())
        b.bind("<Enter>", lambda e: b.configure(bg=hover))
        b.bind("<Leave>", lambda e: b.configure(bg=cor))
        return b

    def _mostrar_na_barra_de_tarefas(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            estilo = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            estilo = (estilo & ~0x00000080) | 0x00040000  # tira TOOLWINDOW, põe APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, estilo)
            self.withdraw()
            self.after(10, self.deiconify)
        except Exception:
            pass

    def _pegar(self, e):
        self._dx, self._dy = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()

    def _arrastar(self, e):
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    # -------------------------------------------------------------- barra
    def _animar(self):
        c = self.barra
        c.delete("all")
        w = max(c.winfo_width(), 10)
        h = self.S(6)
        c.create_rectangle(0, 0, w, h, fill=VAZIO, outline="")
        if self.frac is None and self.trabalhando:
            seg = w * 0.28
            self.anim = (self.anim + self.S(7)) % int(w + seg)
            x = self.anim - seg
            c.create_rectangle(max(0, x), 0, min(w, x + seg), h, fill=AZUL, outline="")
        elif self.frac is not None:
            x = w * self.frac
            c.create_rectangle(0, 0, x, h, fill=AZUL, outline="")
            if x > self.S(6):
                c.create_rectangle(x - self.S(6), 0, x, h, fill=CIANO, outline="")
        self.after(30, self._animar)

    def _ler_fila(self):
        try:
            while True:
                ev = self.fila.get_nowait()
                if ev[0] == "estado":
                    _, status, detalhe, frac = ev
                    self.lbl_status.configure(text=status, fg=TEXTO)
                    self.lbl_detalhe.configure(text=detalhe)
                    self.frac = frac
                elif ev[0] == "erro":
                    self.trabalhando = False
                    self.frac = None
                    self.lbl_status.configure(text="Não foi possível instalar", fg=VERMELHO)
                    self.lbl_detalhe.configure(text=ev[1])
                    self.botoes.pack(fill="x", side="bottom", pady=(self.S(14), 0))
                elif ev[0] == "fim":
                    self.destroy()
                    return
        except queue.Empty:
            pass
        self.after(50, self._ler_fila)

    def estado(self, status, detalhe="", frac=None):
        self.fila.put(("estado", status, detalhe, frac))

    # -------------------------------------------------------------- instalação
    def comecar(self):
        if self.trabalhando:
            return
        self.botoes.pack_forget()
        self.trabalhando = True
        threading.Thread(target=self._instalar, daemon=True).start()

    def cancelar(self):
        self.cancelado = True
        self.destroy()

    def _progresso(self, feito, total, inicio):
        vel = feito / max(time.time() - inicio, 0.1) / 1048576
        if total:
            frac = min(feito / total, 1.0)
            self.estado(f"Baixando Z1UL GRAVADOR… {int(frac * 100)}%",
                        f"{feito / 1048576:.1f} de {total / 1048576:.1f} MB, {vel:.1f} MB/s", frac)
        else:
            self.estado("Baixando Z1UL GRAVADOR…", f"{feito / 1048576:.1f} MB, {vel:.1f} MB/s")

    def _baixar(self, url, destino):
        try:
            self._baixar_python(url, destino)
        except Cancelado:
            raise
        except Exception as e:
            erro_python = e
            try:
                self._baixar_curl(url, destino)
            except Cancelado:
                raise
            except Exception as e2:
                raise RuntimeError(f"Falha no download. Python: {erro_python} | curl: {e2}")

    def _baixar_python(self, url, destino):
        with abrir_url(url) as r, open(destino, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            feito, inicio, ultimo = 0, time.time(), 0.0
            while True:
                if self.cancelado:
                    raise Cancelado()
                bloco = r.read(256 * 1024)
                if not bloco:
                    break
                f.write(bloco)
                feito += len(bloco)
                if time.time() - ultimo > 0.1:
                    ultimo = time.time()
                    self._progresso(feito, total, inicio)

    def _baixar_curl(self, url, destino):
        total = 0
        try:
            cab = curl("-sIL", url, timeout=30).stdout.decode("utf-8", "replace")
            tamanhos = re.findall(r"content-length:\s*(\d+)", cab, re.I)
            if tamanhos:
                total = int(tamanhos[-1])
        except Exception:
            pass
        if os.path.exists(destino):
            os.remove(destino)
        proc = subprocess.Popen(["curl.exe", "-fsSL", "--retry", "2", "-o", destino, url],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, creationflags=SEM_JANELA)
        inicio = time.time()
        while proc.poll() is None:
            if self.cancelado:
                proc.kill()
                raise Cancelado()
            feito = os.path.getsize(destino) if os.path.exists(destino) else 0
            self._progresso(feito, total, inicio)
            time.sleep(0.15)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.read().decode("utf-8", "replace").strip()
                               or f"código {proc.returncode}")

    def _instalar(self):
        tmp = None
        try:
            if not REPO:
                raise RuntimeError("Este instalador foi gerado sem o endereço de download.")
            self.estado("Procurando a versão mais recente…")
            try:
                versao = baixar_texto(URL_BASE + "versao.txt")
            except Exception as e:
                raise RuntimeError(f"Sem conexão com o servidor. Detalhe: {e}")

            tmp = tempfile.mkdtemp(prefix="z1ul_setup_")
            pacote = os.path.join(tmp, PACOTE)
            self._baixar(URL_BASE + PACOTE, pacote)

            self.estado("Instalando…", f"Versão {versao}")
            novo = os.path.join(tmp, "app")
            with zipfile.ZipFile(pacote) as z:
                z.extractall(novo)
            if not os.path.isfile(os.path.join(novo, EXE)):
                raise RuntimeError("O arquivo baixado está incompleto. Tente de novo.")

            fechar_app()
            apagar_pasta(DESTINO)
            os.makedirs(os.path.dirname(DESTINO), exist_ok=True)
            shutil.move(novo, DESTINO)
            if getattr(sys, "frozen", False):
                shutil.copyfile(sys.executable, os.path.join(DESTINO, "Desinstalar.exe"))

            self.estado("Criando atalhos…", f"Versão {versao}")
            criar_atalhos()
            registrar(versao)

            self.estado("Abrindo o Z1UL GRAVADOR…", f"Versão {versao}", 1.0)
            subprocess.Popen([os.path.join(DESTINO, EXE)], cwd=DESTINO, close_fds=True,
                             creationflags=DESACOPLADO)
            time.sleep(1.5)
            self.fila.put(("fim",))
        except Cancelado:
            pass
        except Exception as e:
            self.fila.put(("erro", str(e)))
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)


# ======================================================================= desinstalação
def desinstalar():
    raiz = tk.Tk()
    raiz.withdraw()
    if not messagebox.askyesno(APP, "Remover o Z1UL GRAVADOR deste computador?\n\n"
                                    "Seus clipes salvos não serão apagados."):
        return
    fechar_app()
    remover_atalhos()
    for chave, valor in ((CHAVE_DESINSTALAR, None),
                         (r"Software\Microsoft\Windows\CurrentVersion\Run", "Z1UL Gravador")):
        try:
            if valor:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, chave, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, valor)
            else:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, chave)
        except OSError:
            pass
    # a pasta é apagada depois que este programa fechar
    subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 3 > nul & rmdir /s /q "{DESTINO}"',
                     creationflags=SEM_JANELA | DESACOPLADO, close_fds=True)
    messagebox.showinfo(APP, "O Z1UL GRAVADOR foi removido.")


if __name__ == "__main__":
    if "--desinstalar" in sys.argv:
        desinstalar()
    else:
        Splash().mainloop()
