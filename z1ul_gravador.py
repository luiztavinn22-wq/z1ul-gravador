"""
Z1UL GRAVADOR
Replay instantâneo para jogos no Windows.

O app grava a tela o tempo todo em pequenos pedaços numa pasta temporária.
Quando você aperta o atalho, ele junta os últimos segundos num MP4.
"""

import os
import re
import sys
import glob
import json
import math
import time
import queue
import shutil
import tempfile
import threading
import subprocess
from collections import deque
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import keyboard

try:
    import winsound
except ImportError:
    winsound = None

APP_NOME = "Z1UL GRAVADOR"
VERSAO = "1.0.0"

# ------------------------------------------------------------------ cores
PRETO = "#03050A"
LATERAL = "#070A12"
CARTAO = "#0A1020"
CARTAO_2 = "#0F1830"
BORDA = "#162545"
AZUL = "#1F6BFF"
AZUL_HOVER = "#3B82FF"
AZUL_CLARO = "#5CB8FF"
CIANO = "#00D9FF"
TEXTO = "#E9EFFA"
APAGADO = "#7A89A8"
VERMELHO = "#FF3D60"
VERDE = "#2FE3A5"
VAZIO = "#111A30"

FONTE_TITULO = "Bahnschrift"   # vem com o Windows 10/11
FONTE = "Segoe UI"

# ------------------------------------------------------------------ caminhos
SEG = 2  # duração de cada pedaço do buffer, em segundos
SEM_JANELA = 0x08000000 if os.name == "nt" else 0
PASTA_APP = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Z1UL Gravador")
ARQ_CONFIG = os.path.join(PASTA_APP, "config.json")
PASTA_BUFFER = os.path.join(tempfile.gettempdir(), "z1ul_buffer")

ENCODERS = {
    "Automático": None,
    "NVIDIA (NVENC)": "h264_nvenc",
    "AMD (AMF)": "h264_amf",
    "Intel (Quick Sync)": "h264_qsv",
    "Processador (x264)": "libx264",
}
NOME_ENCODER = {v: k for k, v in ENCODERS.items() if v}
QUALIDADES = {"Leve": 28, "Média": 24, "Alta": 21, "Ultra": 18}
RESOLUCOES = {"Nativa": None, "1440p": 1440, "1080p": 1080, "900p": 900, "720p": 720}
MONITORES = ["Monitor 1", "Monitor 2", "Monitor 3", "Monitor 4"]
SEM_DISPOSITIVO = "Nenhum dispositivo encontrado"

PADRAO = {
    "duracao": 30, "fps": "60", "monitor": "Monitor 1", "cursor": True,
    "resolucao": "Nativa", "qualidade": "Alta", "encoder": "Automático",
    "perfil": "Nenhum",
    "f_cor": False, "brilho": 0.0, "contraste": 1.0, "saturacao": 1.0, "gama": 1.0,
    "f_vibrancia": False, "vibrancia": 0.3,
    "f_nitidez": False, "nitidez": 0.6,
    "f_ruido": False, "ruido": 4.0,
    "f_pb": False,
    "f_vinheta": False, "vinheta": 0.5,
    "sistema_on": True, "sistema_disp": "", "vol_sistema": 100,
    "mic_on": True, "mic_disp": "", "vol_mic": 100, "mic_ruido": True,
    "bitrate_audio": "160k",
    "tecla_salvar": "alt+f10", "tecla_buffer": "alt+f9",
    "som_salvar": True, "iniciar_auto": False,
    "pasta": os.path.join(os.path.expanduser("~"), "Videos", "Z1UL Replays"),
}

OPCOES_VALIDAS = {
    "fps": ["30", "60", "120"], "monitor": MONITORES, "resolucao": list(RESOLUCOES),
    "qualidade": list(QUALIDADES), "encoder": list(ENCODERS),
    "bitrate_audio": ["128k", "160k", "192k", "320k"],
}

PERFIS = {
    "Nenhum": {"f_cor": False, "f_vibrancia": False, "f_nitidez": False,
               "f_ruido": False, "f_pb": False, "f_vinheta": False},
    "Vívido": {"f_cor": True, "brilho": 0.02, "contraste": 1.08, "saturacao": 1.25, "gama": 1.0,
               "f_vibrancia": True, "vibrancia": 0.35, "f_nitidez": True, "nitidez": 0.4,
               "f_ruido": False, "f_pb": False, "f_vinheta": False},
    "Cinema": {"f_cor": True, "brilho": -0.02, "contraste": 1.15, "saturacao": 0.85, "gama": 0.95,
               "f_vibrancia": False, "f_nitidez": False, "f_ruido": False,
               "f_pb": False, "f_vinheta": True, "vinheta": 0.55},
    "Competitivo": {"f_cor": True, "brilho": 0.05, "contraste": 1.1, "saturacao": 1.1, "gama": 1.15,
                    "f_vibrancia": True, "vibrancia": 0.25, "f_nitidez": True, "nitidez": 0.8,
                    "f_ruido": False, "f_pb": False, "f_vinheta": False},
}

# mudanças nessas chaves não exigem reiniciar o buffer
NAO_REINICIA = {"tecla_salvar", "tecla_buffer", "som_salvar", "iniciar_auto", "pasta", "perfil"}


# ================================================================== utilidades
def caminho_recurso(nome):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nome)


def achar_ffmpeg():
    pastas = []
    if getattr(sys, "frozen", False):
        pastas.append(os.path.dirname(sys.executable))
    aqui = os.path.dirname(os.path.abspath(__file__))
    pastas += [aqui, os.path.join(aqui, "ffmpeg")]
    for p in pastas:
        c = os.path.join(p, "ffmpeg.exe")
        if os.path.isfile(c):
            return c
    return shutil.which("ffmpeg")


def rodar(cmd):
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                          creationflags=SEM_JANELA)


def carregar_config():
    cfg = dict(PADRAO)
    try:
        with open(ARQ_CONFIG, "r", encoding="utf-8") as f:
            salvo = json.load(f)
        for k, v in salvo.items():
            if k not in PADRAO:
                continue
            padrao = PADRAO[k]
            if isinstance(padrao, float) and isinstance(v, (int, float)):
                cfg[k] = float(v)
            elif type(v) is type(padrao):
                cfg[k] = v
        for k, validos in OPCOES_VALIDAS.items():
            if cfg[k] not in validos:
                cfg[k] = PADRAO[k]
        cfg["duracao"] = max(10, min(300, int(cfg["duracao"])))
    except Exception:
        pass
    return cfg


def salvar_config(cfg):
    try:
        os.makedirs(PASTA_APP, exist_ok=True)
        with open(ARQ_CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def detectar_encoders(ffmpeg):
    ok = []
    for enc in ("h264_nvenc", "h264_amf", "h264_qsv", "libx264"):
        r = rodar([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                   "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=0.3",
                   "-c:v", enc, "-f", "null", "-"])
        if r.returncode == 0:
            ok.append(enc)
    return ok


def listar_audio(ffmpeg):
    r = rodar([ffmpeg, "-hide_banner", "-nostdin", "-list_devices", "true",
               "-f", "dshow", "-i", "dummy"])
    txt = r.stderr.decode("utf-8", errors="replace")
    nomes = re.findall(r'"([^"]+)"\s*\(audio\)', txt)
    if not nomes:  # formato antigo do FFmpeg
        secao = False
        for linha in txt.splitlines():
            if "DirectShow audio devices" in linha:
                secao = True
                continue
            if secao and "Alternative name" not in linha:
                m = re.search(r'"([^"]+)"', linha)
                if m:
                    nomes.append(m.group(1))
    return list(dict.fromkeys(nomes))


def adivinhar(lista, chaves, evitar=None):
    for n in lista:
        if n != evitar and any(c in n.lower() for c in chaves):
            return n
    return ""


def fmt_dur(v):
    s = int(v)
    if s < 60:
        return f"{s} s"
    return f"{s // 60} min {s % 60:02d} s" if s % 60 else f"{s // 60} min"


def fmt_relogio(s):
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def fmt_tecla(t):
    return " + ".join(p.capitalize() if len(p) > 1 else p.upper() for p in t.split("+")) if t else "Nenhum"


def F(tamanho, negrito=False, titulo=False):
    return ctk.CTkFont(family=FONTE_TITULO if titulo else FONTE, size=tamanho,
                       weight="bold" if negrito else "normal")


# ================================================================== FFmpeg
def filtro_video(cfg):
    monitor = MONITORES.index(cfg["monitor"]) if cfg["monitor"] in MONITORES else 0
    f = [f"ddagrab=output_idx={monitor}:framerate={int(cfg['fps'])}"
         f":draw_mouse={1 if cfg['cursor'] else 0}",
         "hwdownload", "format=bgra"]
    altura = RESOLUCOES.get(cfg["resolucao"])
    if altura:
        f.append(f"scale=-2:{altura}:flags=lanczos")
    if cfg["f_cor"]:
        f.append(f"eq=brightness={cfg['brilho']:.2f}:contrast={cfg['contraste']:.2f}"
                 f":saturation={cfg['saturacao']:.2f}:gamma={cfg['gama']:.2f}")
    if cfg["f_vibrancia"]:
        f.append(f"vibrance=intensity={cfg['vibrancia']:.2f}")
    if cfg["f_nitidez"]:
        f.append(f"unsharp=5:5:{cfg['nitidez']:.2f}:5:5:0")
    if cfg["f_ruido"]:
        s = float(cfg["ruido"])
        f.append(f"hqdn3d={s / 2:.1f}:{s / 2:.1f}:{s:.1f}:{s:.1f}")
    if cfg["f_pb"]:
        f.append("hue=s=0")
    if cfg["f_vinheta"]:
        f.append(f"vignette=angle={cfg['vinheta']:.2f}")
    f.append("format=yuv420p")
    return ",".join(f) + "[v]"


def args_encoder(enc, qp, fps):
    q = str(qp)
    if enc == "h264_nvenc":
        a = ["-c:v", enc, "-preset", "p5" if qp <= 18 else "p4", "-tune", "hq",
             "-rc", "vbr", "-cq", q, "-b:v", "0"]
    elif enc == "h264_amf":
        a = ["-c:v", enc, "-quality", "quality" if qp <= 21 else "balanced",
             "-rc", "cqp", "-qp_i", q, "-qp_p", q]
    elif enc == "h264_qsv":
        a = ["-c:v", enc, "-preset", "medium", "-global_quality", q]
    else:
        a = ["-c:v", "libx264", "-preset", "veryfast" if qp <= 21 else "ultrafast", "-crf", q]
    return a + ["-g", str(fps)]


def dispositivo_valido(nome):
    return bool(nome) and nome != SEM_DISPOSITIVO


def montar_comando(ffmpeg, cfg, encoder):
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    entradas = []
    if cfg["sistema_on"] and dispositivo_valido(cfg["sistema_disp"]):
        entradas.append(("sistema", cfg["sistema_disp"]))
    if cfg["mic_on"] and dispositivo_valido(cfg["mic_disp"]) and cfg["mic_disp"] != cfg["sistema_disp"]:
        entradas.append(("mic", cfg["mic_disp"]))
    for _, disp in entradas:
        cmd += ["-f", "dshow", "-thread_queue_size", "1024", "-rtbufsize", "150M",
                "-i", f"audio={disp}"]

    partes = [filtro_video(cfg)]
    rotulos = []
    for i, (tipo, _) in enumerate(entradas):
        if tipo == "sistema":
            cadeia = f"volume={cfg['vol_sistema'] / 100:.2f}"
        else:
            cadeia = f"volume={cfg['vol_mic'] / 100:.2f}"
            if cfg["mic_ruido"]:
                cadeia += ",afftdn=nf=-25"
        partes.append(f"[{i}:a]{cadeia}[a{i}]")
        rotulos.append(f"[a{i}]")

    mapa_audio = None
    if len(rotulos) == 2:
        partes.append(f"{''.join(rotulos)}amix=inputs=2:duration=longest:normalize=0[aout]")
        mapa_audio = "[aout]"
    elif len(rotulos) == 1:
        mapa_audio = rotulos[0]

    cmd += ["-filter_complex", ";".join(partes), "-map", "[v]"]
    if mapa_audio:
        cmd += ["-map", mapa_audio]
    cmd += args_encoder(encoder, QUALIDADES[cfg["qualidade"]], int(cfg["fps"]))
    if mapa_audio:
        cmd += ["-c:a", "aac", "-b:a", cfg["bitrate_audio"], "-ar", "48000"]

    voltas = math.ceil(int(cfg["duracao"]) / SEG) + 6
    cmd += ["-f", "segment", "-segment_time", str(SEG), "-segment_wrap", str(voltas),
            "-segment_format", "mpegts", "-reset_timestamps", "1",
            os.path.join(PASTA_BUFFER, "seg%03d.ts")]
    return cmd


class Gravador:
    def __init__(self, ffmpeg, eventos):
        self.ffmpeg = ffmpeg
        self.eventos = eventos
        self.proc = None
        self.inicio = 0.0
        self.cfg = None
        self.encoder = None
        self.parando = False
        self.log = deque(maxlen=40)
        self.trava = threading.Lock()

    @property
    def ativo(self):
        return self.proc is not None and self.proc.poll() is None

    def iniciar(self, cfg, encoder):
        if self.ativo:
            return
        self.cfg = dict(cfg)
        self.encoder = encoder
        shutil.rmtree(PASTA_BUFFER, ignore_errors=True)
        os.makedirs(PASTA_BUFFER, exist_ok=True)
        self.log.clear()
        self.parando = False
        self.proc = subprocess.Popen(
            montar_comando(self.ffmpeg, self.cfg, encoder),
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=SEM_JANELA)
        self.inicio = time.time()
        threading.Thread(target=self._vigiar, args=(self.proc,), daemon=True).start()

    def _vigiar(self, proc):
        for linha in proc.stderr:
            self.log.append(linha.decode("utf-8", errors="replace").rstrip())
        proc.wait()
        if not self.parando:
            detalhes = "\n".join(list(self.log)[-8:]) or f"Código de saída {proc.returncode}."
            self.eventos.put(("caiu", detalhes))

    def parar(self):
        proc = self.proc
        if not proc:
            return
        self.parando = True
        try:
            if proc.poll() is None:
                proc.stdin.write(b"q")
                proc.stdin.flush()
                proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        self.proc = None
        shutil.rmtree(PASTA_BUFFER, ignore_errors=True)

    def salvar(self, pasta_destino):
        if not self.ativo:
            raise RuntimeError("Ligue o buffer antes de salvar um replay.")
        if not self.trava.acquire(blocking=False):
            raise RuntimeError("Um replay já está sendo salvo.")
        tmp = None
        try:
            dur = int(self.cfg["duracao"])
            arquivos = sorted(glob.glob(os.path.join(PASTA_BUFFER, "seg*.ts")),
                              key=os.path.getmtime)
            if not arquivos:
                raise RuntimeError("O buffer ainda está vazio. Espere alguns segundos.")
            arquivos = arquivos[-(math.ceil(dur / SEG) + 2):]

            tmp = tempfile.mkdtemp(prefix="z1ul_")
            lista = os.path.join(tmp, "lista.txt")
            with open(lista, "w", encoding="utf-8") as f:
                for i, arq in enumerate(arquivos):
                    copia = os.path.join(tmp, f"{i:03d}.ts")
                    shutil.copyfile(arq, copia)
                    caminho = copia.replace("\\", "/").replace("'", "'\\''")
                    f.write(f"file '{caminho}'\n")

            base = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
            junto = os.path.join(tmp, "junto.mp4")
            r = rodar(base + ["-f", "concat", "-safe", "0", "-i", lista, "-c", "copy", junto])
            if r.returncode != 0:
                raise RuntimeError("Não foi possível juntar o vídeo: "
                                   + r.stderr.decode("utf-8", "replace")[-200:])

            os.makedirs(pasta_destino, exist_ok=True)
            final = os.path.join(pasta_destino,
                                 datetime.now().strftime("Z1UL_%Y-%m-%d_%H-%M-%S.mp4"))
            r = rodar(base + ["-sseof", f"-{dur}", "-i", junto, "-c", "copy",
                              "-movflags", "+faststart", final])
            if r.returncode != 0:
                shutil.move(junto, final)
            return final
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
            self.trava.release()


# ================================================================== interface
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")

        self.cfg = carregar_config()
        self.ffmpeg = achar_ffmpeg()
        self.eventos = queue.Queue()
        self.gravador = Gravador(self.ffmpeg, self.eventos) if self.ffmpeg else None
        self.encoders_ok = None
        self.dispositivos = []
        self.vars = {}
        self.rotulos_valor = {}
        self.paginas = {}
        self.botoes_nav = {}
        self.botoes_tecla = {}
        self.toast_atual = None
        self.pulso = False
        self.salvar_agendado = None
        self.reiniciar_depois = False
        self.parando = False

        self.title(APP_NOME)
        self.geometry("1200x780")
        self.minsize(1040, 660)
        self.configure(fg_color=PRETO)
        try:
            self.iconbitmap(caminho_recurso("z1ul.ico"))
        except Exception:
            pass

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._montar_lateral()
        self._montar_area()
        self._pagina_inicio()
        self._pagina_video()
        self._pagina_filtros()
        self._pagina_audio()
        self._pagina_atalhos()
        self._pagina_saida()
        self.mostrar("inicio")
        self.atualizar_resumo()
        self.registrar_atalhos()

        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.after(100, self._processar_eventos)
        self.after(500, self._tique)

        if self.ffmpeg:
            threading.Thread(target=self._detectar, daemon=True).start()
        else:
            self.after(500, lambda: messagebox.showerror(
                APP_NOME, "O FFmpeg não foi encontrado.\n\nReinstale o Z1UL GRAVADOR "
                          "ou coloque o ffmpeg.exe na pasta do programa."))

    # ------------------------------------------------------------ estrutura
    def _montar_lateral(self):
        lat = ctk.CTkFrame(self, width=244, fg_color=LATERAL, corner_radius=0)
        lat.grid(row=0, column=0, sticky="nsw")
        lat.grid_propagate(False)
        lat.pack_propagate(False)
        ctk.CTkFrame(lat, width=1, fg_color=BORDA, corner_radius=0).place(
            relx=1.0, rely=0, relheight=1.0, anchor="ne")

        topo = ctk.CTkFrame(lat, fg_color="transparent")
        topo.pack(fill="x", padx=22, pady=(30, 34))
        marca = tk.Canvas(topo, width=48, height=48, bg=LATERAL, highlightthickness=0)
        self._desenhar_marca(marca)
        marca.pack(side="left")
        nome = ctk.CTkFrame(topo, fg_color="transparent")
        nome.pack(side="left", padx=(12, 0))
        ctk.CTkLabel(nome, text="Z1UL", height=30, font=F(28, True, True),
                     text_color=TEXTO).pack(anchor="w")
        ctk.CTkLabel(nome, text="GRAVADOR", height=16, font=F(12, True, True),
                     text_color=CIANO).pack(anchor="w")

        itens = [("inicio", "◉    Painel"), ("video", "▣    Vídeo"), ("filtros", "✦    Filtros"),
                 ("audio", "♫    Áudio"), ("atalhos", "⌨    Atalhos"), ("saida", "▤    Pasta e início")]
        for chave, texto in itens:
            b = ctk.CTkButton(lat, text=texto, anchor="w", height=46, corner_radius=12,
                              fg_color="transparent", hover_color=CARTAO_2,
                              text_color=APAGADO, font=F(14, True),
                              command=lambda c=chave: self.mostrar(c))
            b.pack(fill="x", padx=14, pady=3)
            self.botoes_nav[chave] = b

        rod = ctk.CTkFrame(lat, fg_color=CARTAO, corner_radius=14, border_width=1, border_color=BORDA)
        rod.pack(side="bottom", fill="x", padx=14, pady=18)
        self.lbl_mini = ctk.CTkLabel(rod, text="●  Buffer desligado", font=F(13, True),
                                     text_color=APAGADO, anchor="w")
        self.lbl_mini.pack(fill="x", padx=16, pady=(12, 0))
        ctk.CTkLabel(rod, text=f"Versão {VERSAO}", font=F(11), text_color=APAGADO,
                     anchor="w").pack(fill="x", padx=16, pady=(0, 12))

    def _desenhar_marca(self, c):
        c.create_rectangle(1, 1, 47, 47, outline=AZUL, width=2)
        c.create_rectangle(6, 6, 42, 42, fill="#081633", outline="")
        c.create_polygon(13, 13, 35, 13, 35, 18, 20, 30, 35, 30, 35, 35, 13, 35, 13, 30,
                         28, 18, 13, 18, fill=CIANO, outline="")
        c.create_rectangle(38, 38, 47, 47, fill=AZUL, outline="")

    def _montar_area(self):
        self.area = ctk.CTkFrame(self, fg_color=PRETO, corner_radius=0)
        self.area.grid(row=0, column=1, sticky="nsew")
        self.area.grid_columnconfigure(0, weight=1)
        self.area.grid_rowconfigure(1, weight=1)

        self.aviso = ctk.CTkFrame(self.area, fg_color="#0B1A3A", corner_radius=12,
                                  border_width=1, border_color=AZUL)
        ctk.CTkButton(self.aviso, text="Reiniciar buffer", width=150, height=34, corner_radius=10,
                      fg_color=AZUL, hover_color=AZUL_HOVER, font=F(13, True),
                      command=self.reiniciar).pack(side="right", padx=10, pady=8)
        ctk.CTkLabel(self.aviso, text="Você mudou configurações de gravação. Reinicie o buffer "
                                      "para que elas passem a valer.",
                     font=F(13), text_color=TEXTO, anchor="w").pack(side="left", padx=18)

    def _nova_pagina(self, chave, titulo, subtitulo):
        p = ctk.CTkScrollableFrame(self.area, fg_color=PRETO, corner_radius=0,
                                   scrollbar_button_color=CARTAO_2,
                                   scrollbar_button_hover_color=BORDA)
        cab = ctk.CTkFrame(p, fg_color="transparent")
        cab.pack(fill="x", padx=6, pady=(28, 20))
        ctk.CTkLabel(cab, text=titulo, font=F(32, True, True), text_color=TEXTO,
                     anchor="w").pack(fill="x")
        ctk.CTkLabel(cab, text=subtitulo, font=F(14), text_color=APAGADO,
                     anchor="w").pack(fill="x", pady=(2, 0))
        self.paginas[chave] = p
        return p

    def mostrar(self, chave):
        for p in self.paginas.values():
            p.grid_forget()
        self.paginas[chave].grid(row=1, column=0, sticky="nsew", padx=(30, 24))
        for k, b in self.botoes_nav.items():
            if k == chave:
                b.configure(fg_color=CARTAO_2, text_color=AZUL_CLARO)
            else:
                b.configure(fg_color="transparent", text_color=APAGADO)
        if chave == "inicio":
            self.atualizar_clipes()

    # ------------------------------------------------------------ peças reutilizáveis
    def _cartao(self, pai, titulo=None, desc=None, direita=None):
        c = ctk.CTkFrame(pai, fg_color=CARTAO, corner_radius=16, border_width=1, border_color=BORDA)
        c.pack(fill="x", padx=6, pady=(0, 16))
        if titulo:
            cab = ctk.CTkFrame(c, fg_color="transparent")
            cab.pack(fill="x", padx=24, pady=(20, 6))
            if direita:
                direita(cab).pack(side="right", anchor="n")
            ctk.CTkLabel(cab, text=titulo, font=F(18, True, True), text_color=TEXTO,
                         anchor="w").pack(fill="x")
            if desc:
                ctk.CTkLabel(cab, text=desc, font=F(13), text_color=APAGADO, anchor="w",
                             justify="left", wraplength=600).pack(fill="x")
        return c

    def _fim_cartao(self, cartao):
        ctk.CTkFrame(cartao, height=10, fg_color="transparent").pack()

    def _linha(self, cartao, titulo, desc, construtor):
        r = ctk.CTkFrame(cartao, fg_color="transparent")
        r.pack(fill="x", padx=24, pady=9)
        w = construtor(r)
        w.pack(side="right", padx=(18, 0))
        t = ctk.CTkFrame(r, fg_color="transparent")
        t.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(t, text=titulo, font=F(14, True), text_color=TEXTO, anchor="w").pack(fill="x")
        if desc:
            ctk.CTkLabel(t, text=desc, font=F(12), text_color=APAGADO, anchor="w",
                         justify="left", wraplength=440).pack(fill="x")
        return w

    def _var(self, chave, tipo):
        if chave not in self.vars:
            classe = {"bool": tk.BooleanVar, "int": tk.IntVar,
                      "float": tk.DoubleVar, "str": tk.StringVar}[tipo]
            v = classe(value=self.cfg[chave])
            v.trace_add("write", lambda *_, k=chave: self._mudou(k))
            self.vars[chave] = v
        return self.vars[chave]

    def _switch(self, chave):
        return lambda pai: ctk.CTkSwitch(
            pai, text="", variable=self._var(chave, "bool"), onvalue=True, offvalue=False,
            width=48, switch_width=46, switch_height=24, progress_color=AZUL,
            button_color=TEXTO, button_hover_color=CIANO, fg_color="#1A2645")

    def _slider(self, chave, de, ate, passos, fmt, tipo="float"):
        def construir(pai):
            f = ctk.CTkFrame(pai, fg_color="transparent")
            var = self._var(chave, tipo)
            ctk.CTkSlider(f, from_=de, to=ate, number_of_steps=passos, variable=var,
                          width=250, height=18, progress_color=AZUL, button_color=AZUL_CLARO,
                          button_hover_color=CIANO, fg_color="#1A2645").pack(side="left")
            lbl = ctk.CTkLabel(f, text=fmt(var.get()), width=78, anchor="e",
                               font=F(14, True, True), text_color=AZUL_CLARO)
            lbl.pack(side="left", padx=(12, 0))
            self.rotulos_valor[chave] = (lbl, fmt)
            return f
        return construir

    def _opcoes(self, chave, valores, largura=220):
        return lambda pai: ctk.CTkOptionMenu(
            pai, values=valores, variable=self._var(chave, "str"), width=largura, height=36,
            corner_radius=10, dynamic_resizing=False, fg_color=CARTAO_2, button_color=AZUL,
            button_hover_color=AZUL_HOVER, dropdown_fg_color=CARTAO,
            dropdown_hover_color=CARTAO_2, dropdown_text_color=TEXTO, text_color=TEXTO,
            font=F(13), dropdown_font=F(13))

    def _segmentos(self, chave, valores, comando=None):
        return lambda pai: ctk.CTkSegmentedButton(
            pai, values=valores, variable=self._var(chave, "str"), command=comando, height=36,
            selected_color=AZUL, selected_hover_color=AZUL_HOVER, unselected_color=CARTAO_2,
            unselected_hover_color=BORDA, fg_color=CARTAO_2, text_color=TEXTO, font=F(13, True))

    def _botao(self, pai, texto, comando, principal=False, largura=120):
        return ctk.CTkButton(pai, text=texto, command=comando, width=largura, height=36,
                             corner_radius=10, font=F(13, True),
                             fg_color=AZUL if principal else CARTAO_2,
                             hover_color=AZUL_HOVER if principal else BORDA,
                             border_width=0 if principal else 1, border_color=BORDA,
                             text_color=TEXTO)

    # ------------------------------------------------------------ páginas
    def _pagina_inicio(self):
        p = self._nova_pagina("inicio", "Painel",
                              "Deixe o buffer ligado e salve a jogada logo depois que ela acontecer.")

        hero = ctk.CTkFrame(p, fg_color=CARTAO, corner_radius=20, border_width=1, border_color=BORDA)
        hero.pack(fill="x", padx=6, pady=(0, 16))

        topo = ctk.CTkFrame(hero, fg_color="transparent")
        topo.pack(fill="x", padx=28, pady=(26, 10))

        botoes = ctk.CTkFrame(topo, fg_color="transparent")
        botoes.pack(side="right")
        self.btn_buffer = ctk.CTkButton(botoes, text="▶   Ligar buffer", width=200, height=54,
                                        corner_radius=14, fg_color=AZUL, hover_color=AZUL_HOVER,
                                        font=F(16, True, True), command=self.alternar_buffer)
        self.btn_buffer.pack(side="right", padx=(10, 0))
        self.btn_salvar = ctk.CTkButton(botoes, text="Salvar replay", width=170, height=54,
                                        corner_radius=14, fg_color=CARTAO_2, hover_color=BORDA,
                                        border_width=1, border_color=AZUL, text_color=TEXTO,
                                        font=F(16, True, True), command=self.salvar_replay)
        self.btn_salvar.pack(side="right")

        estado = ctk.CTkFrame(topo, fg_color="transparent")
        estado.pack(side="left", fill="x", expand=True)
        linha = ctk.CTkFrame(estado, fg_color="transparent")
        linha.pack(anchor="w")
        self.lbl_ponto = ctk.CTkLabel(linha, text="●", font=F(22), text_color=APAGADO)
        self.lbl_ponto.pack(side="left", padx=(0, 10))
        self.lbl_status = ctk.CTkLabel(linha, text="Buffer desligado", font=F(24, True, True),
                                       text_color=TEXTO)
        self.lbl_status.pack(side="left")
        self.lbl_tempo = ctk.CTkLabel(estado, text="Ligue o buffer para começar a guardar os "
                                                   "últimos segundos da tela.",
                                      font=F(13), text_color=APAGADO, anchor="w")
        self.lbl_tempo.pack(anchor="w", pady=(2, 0))

        # linha do tempo do buffer: o elemento principal da tela
        self.timeline = tk.Canvas(hero, height=58, bg=CARTAO, highlightthickness=0)
        self.timeline.pack(fill="x", padx=28, pady=(12, 0))
        self.timeline.bind("<Configure>", lambda e: self._desenhar_timeline())
        legenda = ctk.CTkFrame(hero, fg_color="transparent")
        legenda.pack(fill="x", padx=28, pady=(4, 22))
        self.lbl_inicio_tl = ctk.CTkLabel(legenda, text="", font=F(12), text_color=APAGADO)
        self.lbl_inicio_tl.pack(side="left")
        ctk.CTkLabel(legenda, text="agora", font=F(12), text_color=APAGADO).pack(side="right")

        grade = ctk.CTkFrame(p, fg_color="transparent")
        grade.pack(fill="x", padx=6, pady=(0, 16))
        self.chips = {}
        itens = [("encoder", "Encoder"), ("video", "Imagem"), ("replay", "Duração do replay"),
                 ("atalho", "Atalho para salvar")]
        for i, (chave, titulo) in enumerate(itens):
            grade.grid_columnconfigure(i, weight=1, uniform="chips")
            c = ctk.CTkFrame(grade, fg_color=CARTAO, corner_radius=16, border_width=1,
                             border_color=BORDA)
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 12, 0))
            ctk.CTkLabel(c, text=titulo, font=F(12), text_color=APAGADO,
                         anchor="w").pack(fill="x", padx=18, pady=(16, 0))
            v = ctk.CTkLabel(c, text="…", font=F(20, True, True), text_color=TEXTO, anchor="w")
            v.pack(fill="x", padx=18)
            s = ctk.CTkLabel(c, text="", font=F(12), text_color=AZUL_CLARO, anchor="w")
            s.pack(fill="x", padx=18, pady=(0, 16))
            self.chips[chave] = (v, s)

        def botoes_clipes(pai):
            f = ctk.CTkFrame(pai, fg_color="transparent")
            self._botao(f, "Atualizar", self.atualizar_clipes, largura=100).pack(side="left", padx=(0, 8))
            self._botao(f, "Abrir pasta", self.abrir_pasta, principal=True,
                        largura=120).pack(side="left")
            return f

        c = self._cartao(p, "Clipes recentes", "Os replays salvos aparecem aqui, do mais novo "
                                               "para o mais antigo.", direita=botoes_clipes)
        self.lista_clipes = ctk.CTkFrame(c, fg_color="transparent")
        self.lista_clipes.pack(fill="x", padx=16, pady=(8, 18))

    def _pagina_video(self):
        p = self._nova_pagina("video", "Vídeo", "O que é capturado e com que qualidade.")

        c = self._cartao(p, "Captura")
        self._linha(c, "Monitor", "Qual tela será gravada.", self._opcoes("monitor", MONITORES))
        self._linha(c, "Quadros por segundo", "60 é o equilíbrio ideal. 120 exige mais da placa "
                                              "de vídeo e gera arquivos maiores.",
                    self._segmentos("fps", ["30", "60", "120"]))
        self._linha(c, "Mostrar o cursor", "Desligue em jogos de tiro para o vídeo ficar limpo.",
                    self._switch("cursor"))
        self._fim_cartao(c)

        c = self._cartao(p, "Qualidade da imagem")
        self._linha(c, "Resolução do vídeo", "Nativa grava no tamanho da sua tela. Reduzir "
                                             "deixa os arquivos menores.",
                    self._opcoes("resolucao", list(RESOLUCOES)))
        self._linha(c, "Qualidade", "Ultra preserva mais detalhes, mas o arquivo fica bem maior.",
                    self._segmentos("qualidade", list(QUALIDADES)))
        self.menu_encoder = self._linha(
            c, "Encoder", "Use o da sua placa de vídeo para não perder FPS no jogo. "
                          "Automático escolhe o melhor disponível.",
            self._opcoes("encoder", list(ENCODERS)))
        self._fim_cartao(c)

        c = self._cartao(p, "Replay")
        self._linha(c, "Segundos guardados", "Quanto tempo para trás é salvo quando você "
                                              "aperta o atalho.",
                    self._slider("duracao", 10, 300, 58, fmt_dur, tipo="int"))
        self._fim_cartao(c)

    def _pagina_filtros(self):
        p = self._nova_pagina("filtros", "Filtros",
                              "Ajustes de imagem aplicados enquanto grava. Cada filtro pode ser "
                              "ligado e desligado.")

        c = self._cartao(p, "Perfil rápido", "Escolha um ponto de partida e ajuste os detalhes "
                                             "abaixo como quiser.")
        self._linha(c, "Perfil", None,
                    self._segmentos("perfil", list(PERFIS), comando=self.aplicar_perfil))
        ctk.CTkLabel(c, text="Os filtros usam o processador. Vários ligados ao mesmo tempo em "
                             "1440p ou 120 FPS podem pesar em PCs mais fracos.",
                     font=F(12), text_color=AZUL_CLARO, anchor="w", justify="left",
                     wraplength=700).pack(fill="x", padx=24, pady=(4, 0))
        self._fim_cartao(c)

        pct = lambda v: f"{int(round(v * 100))}%"
        sinal = lambda v: f"{v:+.2f}"
        mult = lambda v: f"{v:.2f}x"

        c = self._cartao(p, "Correção de cor", "Brilho, contraste, saturação e gama.",
                         direita=self._switch("f_cor"))
        self._linha(c, "Brilho", None, self._slider("brilho", -0.3, 0.3, 60, sinal))
        self._linha(c, "Contraste", None, self._slider("contraste", 0.5, 1.5, 100, mult))
        self._linha(c, "Saturação", None, self._slider("saturacao", 0.0, 2.0, 40, mult))
        self._linha(c, "Gama", "Clareia as partes escuras sem estourar as claras.",
                    self._slider("gama", 0.5, 2.0, 30, mult))
        self._fim_cartao(c)

        c = self._cartao(p, "Cores vivas", "Realça as cores apagadas sem exagerar nas fortes.",
                         direita=self._switch("f_vibrancia"))
        self._linha(c, "Intensidade", None, self._slider("vibrancia", 0.0, 1.0, 20, pct))
        self._fim_cartao(c)

        c = self._cartao(p, "Nitidez", "Deixa contornos e textos mais definidos.",
                         direita=self._switch("f_nitidez"))
        self._linha(c, "Intensidade", None, self._slider("nitidez", 0.0, 1.5, 30, pct))
        self._fim_cartao(c)

        c = self._cartao(p, "Redução de ruído", "Suaviza granulado em cenas escuras. "
                                                "É o filtro mais pesado.",
                         direita=self._switch("f_ruido"))
        self._linha(c, "Intensidade", None,
                    self._slider("ruido", 1.0, 10.0, 9, lambda v: f"{v:.0f}"))
        self._fim_cartao(c)

        c = self._cartao(p, "Vinheta", "Escurece as bordas para um visual de cinema.",
                         direita=self._switch("f_vinheta"))
        self._linha(c, "Intensidade", None, self._slider("vinheta", 0.2, 1.0, 16, pct))
        self._fim_cartao(c)

        c = self._cartao(p, "Preto e branco", "Remove todas as cores do vídeo.",
                         direita=self._switch("f_pb"))
        self._fim_cartao(c)

    def _pagina_audio(self):
        p = self._nova_pagina("audio", "Áudio", "Som do jogo e do microfone, cada um com seu volume.")
        inicial = [self.cfg["sistema_disp"] or "Carregando…"]

        c = self._cartao(p, "Som do jogo", "Captura tudo que sai pelos seus fones ou caixas de som.",
                         direita=self._switch("sistema_on"))
        self.menu_sistema = self._linha(
            c, "Dispositivo", "Escolha \"Mixagem estéreo\" ou \"CABLE Output\".",
            self._opcoes("sistema_disp", inicial, largura=320))
        self._linha(c, "Volume", None, self._slider("vol_sistema", 0, 200, 40,
                                                    lambda v: f"{int(v)}%", tipo="int"))
        self._fim_cartao(c)

        c = self._cartao(p, "Microfone", "Sua voz junto com o jogo.",
                         direita=self._switch("mic_on"))
        self.menu_mic = self._linha(c, "Dispositivo", None,
                                    self._opcoes("mic_disp", [self.cfg["mic_disp"] or "Carregando…"],
                                                 largura=320))
        self._linha(c, "Volume", None, self._slider("vol_mic", 0, 200, 40,
                                                    lambda v: f"{int(v)}%", tipo="int"))
        self._linha(c, "Reduzir ruído do microfone", "Diminui chiado, ventoinha e barulho de fundo.",
                    self._switch("mic_ruido"))
        self._fim_cartao(c)

        c = self._cartao(p, "Geral")
        self._linha(c, "Qualidade do áudio", "160k é suficiente para a maioria dos clipes.",
                    self._segmentos("bitrate_audio", ["128k", "160k", "192k", "320k"]))
        self._linha(c, "Lista de dispositivos", "Use se você conectou um fone ou microfone "
                                                "depois de abrir o app.",
                    lambda pai: self._botao(pai, "Procurar de novo", self.recarregar_dispositivos,
                                            largura=160))
        ctk.CTkLabel(c, text="Não aparece \"Mixagem estéreo\"? Abra Painel de Controle > Som > "
                             "Gravação, clique com o botão direito, marque \"Mostrar dispositivos "
                             "desativados\" e ative. Se não existir no seu PC, instale o VB-Cable.",
                     font=F(12), text_color=AZUL_CLARO, anchor="w", justify="left",
                     wraplength=700).pack(fill="x", padx=24, pady=(6, 0))
        self._fim_cartao(c)

    def _pagina_atalhos(self):
        p = self._nova_pagina("atalhos", "Atalhos",
                              "Funcionam mesmo com o jogo em tela cheia.")
        c = self._cartao(p, "Teclas")
        for chave, titulo, desc in (
                ("tecla_salvar", "Salvar replay", "Salva os últimos segundos na hora."),
                ("tecla_buffer", "Ligar ou desligar o buffer", "Para não precisar sair do jogo.")):
            def construir(pai, k=chave):
                f = ctk.CTkFrame(pai, fg_color="transparent")
                tecla = ctk.CTkButton(f, text=fmt_tecla(self.cfg[k]), width=170, height=40,
                                      corner_radius=10, fg_color=CARTAO_2, hover_color=BORDA,
                                      border_width=1, border_color=AZUL, text_color=CIANO,
                                      font=F(15, True, True))
                tecla.configure(command=lambda: self.capturar_tecla(k))
                tecla.pack(side="left", padx=(0, 8))
                self._botao(f, "Alterar", lambda: self.capturar_tecla(k),
                            largura=90).pack(side="left")
                self.botoes_tecla[k] = tecla
                return f
            self._linha(c, titulo, desc, construir)
        ctk.CTkLabel(c, text="Clique em Alterar e aperte a combinação desejada, por exemplo "
                             "Alt + F10. Esc cancela. Alguns jogos com anti-cheat bloqueiam "
                             "atalhos; nesse caso, escolha outra combinação.",
                     font=F(12), text_color=AZUL_CLARO, anchor="w", justify="left",
                     wraplength=700).pack(fill="x", padx=24, pady=(6, 0))
        self._fim_cartao(c)

        c = self._cartao(p, "Aviso ao salvar")
        self._linha(c, "Tocar um som", "Um bipe curto confirma que o replay foi salvo, "
                                       "sem você precisar sair do jogo.",
                    self._switch("som_salvar"))
        self._fim_cartao(c)

    def _pagina_saida(self):
        p = self._nova_pagina("saida", "Pasta e início", "Onde os clipes ficam e como o app começa.")
        c = self._cartao(p, "Pasta dos clipes")

        def construir(pai):
            f = ctk.CTkFrame(pai, fg_color="transparent")
            self._botao(f, "Escolher pasta", self.escolher_pasta, principal=True,
                        largura=140).pack(side="right", padx=(8, 0))
            self._botao(f, "Abrir", self.abrir_pasta, largura=80).pack(side="right")
            return f
        self._linha(c, "Salvar em", None, construir)
        self.lbl_pasta = ctk.CTkLabel(c, text=self.cfg["pasta"], font=F(13), text_color=AZUL_CLARO,
                                      anchor="w", fg_color=CARTAO_2, corner_radius=10, height=38)
        self.lbl_pasta.pack(fill="x", padx=24, pady=(2, 8))
        self._fim_cartao(c)

        c = self._cartao(p, "Ao abrir o app")
        self._linha(c, "Ligar o buffer automaticamente", "Com isso, basta abrir o Z1UL e jogar.",
                    self._switch("iniciar_auto"))
        self._fim_cartao(c)

    # ------------------------------------------------------------ eventos de configuração
    def _mudou(self, chave):
        try:
            valor = self.vars[chave].get()
        except (tk.TclError, ValueError):
            return
        self.cfg[chave] = valor
        if chave in self.rotulos_valor:
            lbl, fmt = self.rotulos_valor[chave]
            lbl.configure(text=fmt(valor))
        self._agendar_salvar()
        if chave not in NAO_REINICIA and self.gravador and self.gravador.ativo:
            self.aviso.grid(row=0, column=0, sticky="ew", padx=(36, 30), pady=(18, 0))
        self.atualizar_resumo()
        if chave == "duracao":
            self._desenhar_timeline()

    def _agendar_salvar(self):
        if self.salvar_agendado:
            self.after_cancel(self.salvar_agendado)
        self.salvar_agendado = self.after(500, lambda: salvar_config(self.cfg))

    def aplicar_perfil(self, nome):
        for k, v in PERFIS[nome].items():
            if k in self.vars:
                self.vars[k].set(v)
            else:
                self.cfg[k] = v
        self.toast(f"Perfil {nome} aplicado.")

    def escolher_pasta(self):
        pasta = filedialog.askdirectory(initialdir=self.cfg["pasta"], title="Pasta dos clipes")
        if pasta:
            self.cfg["pasta"] = os.path.normpath(pasta)
            self.lbl_pasta.configure(text=self.cfg["pasta"])
            salvar_config(self.cfg)
            self.atualizar_clipes()
            self.toast("Pasta dos clipes alterada.")

    def abrir_pasta(self):
        os.makedirs(self.cfg["pasta"], exist_ok=True)
        os.startfile(self.cfg["pasta"])

    # ------------------------------------------------------------ atalhos de teclado
    def registrar_atalhos(self):
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        for chave, evento in (("tecla_salvar", "salvar"), ("tecla_buffer", "alternar")):
            tecla = self.cfg.get(chave)
            if not tecla:
                continue
            try:
                keyboard.add_hotkey(tecla, lambda e=evento: self.eventos.put((e,)))
            except Exception:
                self.eventos.put(("erro", f"O atalho {fmt_tecla(tecla)} não é válido."))

    def capturar_tecla(self, chave):
        self.botoes_tecla[chave].configure(text="Aperte as teclas…", fg_color=AZUL, text_color=TEXTO)
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass

        def ler():
            try:
                t = keyboard.read_hotkey(suppress=False)
            except Exception:
                t = None
            self.eventos.put(("tecla", chave, t))
        threading.Thread(target=ler, daemon=True).start()

    def _receber_tecla(self, chave, tecla):
        outra = "tecla_buffer" if chave == "tecla_salvar" else "tecla_salvar"
        if tecla and tecla != "esc":
            if tecla == self.cfg[outra]:
                self.toast("Essa combinação já está em uso no outro atalho.", "erro")
            else:
                self.cfg[chave] = tecla
                salvar_config(self.cfg)
                self.toast(f"Atalho alterado para {fmt_tecla(tecla)}.", "sucesso")
        self.botoes_tecla[chave].configure(text=fmt_tecla(self.cfg[chave]),
                                           fg_color=CARTAO_2, text_color=CIANO)
        self.registrar_atalhos()
        self.atualizar_resumo()

    # ------------------------------------------------------------ detecção
    def _detectar(self):
        self.eventos.put(("encoders", detectar_encoders(self.ffmpeg)))
        self.eventos.put(("dispositivos", listar_audio(self.ffmpeg)))
        self.eventos.put(("pronto",))

    def recarregar_dispositivos(self):
        if self.ffmpeg:
            threading.Thread(target=lambda: self.eventos.put(
                ("dispositivos", listar_audio(self.ffmpeg))), daemon=True).start()
            self.toast("Procurando dispositivos de áudio…")

    def _receber_encoders(self, lista):
        self.encoders_ok = lista
        nomes = ["Automático"] + [NOME_ENCODER[e] for e in lista if e in NOME_ENCODER]
        self.menu_encoder.configure(values=nomes)
        if self.cfg["encoder"] not in nomes:
            self.vars["encoder"].set("Automático")
        self.atualizar_resumo()

    def _receber_dispositivos(self, lista):
        self.dispositivos = lista
        valores = lista or [SEM_DISPOSITIVO]
        self.menu_sistema.configure(values=valores)
        self.menu_mic.configure(values=valores)
        if self.cfg["sistema_disp"] not in lista:
            palpite = adivinhar(lista, ["estéreo", "estereo", "stereo", "mix", "cable output",
                                        "what u hear", "virtual-audio"])
            self.vars["sistema_disp"].set(palpite or SEM_DISPOSITIVO)
        if self.cfg["mic_disp"] not in lista:
            palpite = adivinhar(lista, ["micro", "mic", "headset", "fone"],
                                evitar=self.cfg["sistema_disp"])
            if not palpite:
                palpite = next((n for n in lista if n != self.cfg["sistema_disp"]), "")
            self.vars["mic_disp"].set(palpite or SEM_DISPOSITIVO)

    def encoder_escolhido(self):
        escolhido = ENCODERS.get(self.cfg["encoder"])
        disponiveis = self.encoders_ok or []
        if escolhido and (self.encoders_ok is None or escolhido in disponiveis):
            return escolhido
        for e in ("h264_nvenc", "h264_amf", "h264_qsv"):
            if e in disponiveis:
                return e
        return "libx264"

    # ------------------------------------------------------------ buffer
    def alternar_buffer(self):
        if self.parando:
            return
        if self.gravador and self.gravador.ativo:
            self.parar()
        else:
            self.iniciar()

    def iniciar(self):
        if not self.gravador:
            messagebox.showerror(APP_NOME, "O FFmpeg não foi encontrado. Reinstale o app.")
            return
        if self.gravador.ativo:
            return
        try:
            self.gravador.iniciar(self.cfg, self.encoder_escolhido())
        except Exception as e:
            messagebox.showerror(APP_NOME, f"Não foi possível ligar o buffer.\n\n{e}")
            return
        self.aviso.grid_forget()
        self._atualizar_estado()
        self.toast(f"Buffer ligado. Aperte {fmt_tecla(self.cfg['tecla_salvar'])} para salvar.",
                   "sucesso")

    def parar(self, reiniciar=False):
        if not (self.gravador and self.gravador.ativo):
            return
        self.parando = True
        self.reiniciar_depois = reiniciar
        self._atualizar_estado()

        def tarefa():
            self.gravador.parar()
            self.eventos.put(("parado",))
        threading.Thread(target=tarefa, daemon=True).start()

    def reiniciar(self):
        self.aviso.grid_forget()
        if self.gravador and self.gravador.ativo:
            self.parar(reiniciar=True)
        else:
            self.iniciar()

    def salvar_replay(self):
        if not (self.gravador and self.gravador.ativo):
            self.toast("Ligue o buffer antes de salvar um replay.", "erro")
            return
        self.btn_salvar.configure(text="Salvando…", state="disabled")
        pasta = self.cfg["pasta"]

        def tarefa():
            try:
                self.eventos.put(("salvo", self.gravador.salvar(pasta)))
            except Exception as e:
                self.eventos.put(("erro_salvar", str(e)))
        threading.Thread(target=tarefa, daemon=True).start()

    def bipe(self):
        if winsound and self.cfg["som_salvar"]:
            def tocar():
                try:
                    winsound.Beep(988, 70)
                    winsound.Beep(1319, 120)
                except Exception:
                    pass
            threading.Thread(target=tocar, daemon=True).start()

    # ------------------------------------------------------------ atualização da tela
    def _atualizar_estado(self):
        ativo = bool(self.gravador and self.gravador.ativo)
        if self.parando:
            self.btn_buffer.configure(text="Desligando…", state="disabled")
            return
        if ativo:
            self.btn_buffer.configure(text="■   Desligar buffer", state="normal",
                                      fg_color="#240A14", hover_color="#36101E",
                                      border_width=1, border_color=VERMELHO, text_color="#FFD3DC")
            self.lbl_status.configure(text="Gravando no buffer")
            self.lbl_mini.configure(text="●  Gravando", text_color=VERMELHO)
        else:
            self.btn_buffer.configure(text="▶   Ligar buffer", state="normal", fg_color=AZUL,
                                      hover_color=AZUL_HOVER, border_width=0, text_color=TEXTO)
            self.lbl_status.configure(text="Buffer desligado")
            self.lbl_ponto.configure(text_color=APAGADO)
            self.lbl_tempo.configure(text="Ligue o buffer para começar a guardar os últimos "
                                          "segundos da tela.")
            self.lbl_mini.configure(text="●  Buffer desligado", text_color=APAGADO)
        self._desenhar_timeline()

    def _desenhar_timeline(self):
        c = self.timeline
        c.delete("all")
        largura = max(c.winfo_width(), 200)
        altura = 58
        dur = int(self.cfg["duracao"])
        blocos = max(10, min(60, math.ceil(dur / SEG)))
        espaco = 4
        lb = (largura - espaco * (blocos - 1)) / blocos

        cheio = 0.0
        if self.gravador and self.gravador.ativo and not self.parando:
            decorrido = time.time() - self.gravador.inicio
            cheio = min(decorrido / dur, 1.0) * blocos

        for i in range(blocos):
            x0 = i * (lb + espaco)
            x1 = x0 + lb
            # blocos preenchem da direita (agora) para a esquerda (passado)
            posicao = blocos - i
            if posicao <= cheio:
                recente = posicao <= 1.5
                cor = CIANO if recente else AZUL
                h = altura if recente else altura - 10
            else:
                cor, h = VAZIO, altura - 26
            y0 = (altura - h) / 2
            c.create_rectangle(x0, y0, x1, y0 + h, fill=cor, outline="")
        self.lbl_inicio_tl.configure(text=f"{fmt_dur(dur)} atrás")

    def _tique(self):
        if self.gravador and self.gravador.ativo and not self.parando:
            self.pulso = not self.pulso
            self.lbl_ponto.configure(text_color=VERMELHO if self.pulso else "#5C1426")
            decorrido = time.time() - self.gravador.inicio
            dur = int(self.cfg["duracao"])
            guardado = min(int(decorrido), dur)
            self.lbl_tempo.configure(
                text=f"Ligado há {fmt_relogio(decorrido)}. {guardado} de {dur} segundos já "
                     f"estão guardados.")
            self._desenhar_timeline()
        self.after(500, self._tique)

    def atualizar_resumo(self):
        if not hasattr(self, "chips"):
            return
        enc = self.encoder_escolhido()
        nome = NOME_ENCODER.get(enc, enc)
        if self.encoders_ok is None:
            detalhe = "Verificando sua placa…"
        elif enc == "libx264":
            detalhe = "Usando o processador"
        else:
            detalhe = "Aceleração pela placa de vídeo"
        self.chips["encoder"][0].configure(text=nome.split(" (")[0])
        self.chips["encoder"][1].configure(text=detalhe)
        self.chips["video"][0].configure(text=f"{self.cfg['resolucao']}, {self.cfg['fps']} FPS")
        self.chips["video"][1].configure(text=f"Qualidade {self.cfg['qualidade'].lower()}")
        filtros = sum(self.cfg[k] for k in ("f_cor", "f_vibrancia", "f_nitidez", "f_ruido",
                                            "f_pb", "f_vinheta"))
        self.chips["replay"][0].configure(text=fmt_dur(self.cfg["duracao"]))
        self.chips["replay"][1].configure(
            text="Sem filtros" if not filtros else f"{filtros} filtro{'s' if filtros > 1 else ''} ligado{'s' if filtros > 1 else ''}")
        self.chips["atalho"][0].configure(text=fmt_tecla(self.cfg["tecla_salvar"]))
        self.chips["atalho"][1].configure(text=f"Buffer: {fmt_tecla(self.cfg['tecla_buffer'])}")

    def atualizar_clipes(self):
        for w in self.lista_clipes.winfo_children():
            w.destroy()
        pasta = self.cfg["pasta"]
        try:
            arquivos = sorted((os.path.join(pasta, f) for f in os.listdir(pasta)
                               if f.lower().endswith(".mp4")),
                              key=os.path.getmtime, reverse=True)[:12]
        except Exception:
            arquivos = []
        if not arquivos:
            ctk.CTkLabel(self.lista_clipes,
                         text=f"Nenhum clipe ainda. Ligue o buffer, jogue e aperte "
                              f"{fmt_tecla(self.cfg['tecla_salvar'])} depois de uma boa jogada.",
                         font=F(13), text_color=APAGADO, anchor="w", justify="left",
                         wraplength=700).pack(fill="x", padx=10, pady=10)
            return
        for arq in arquivos:
            linha = ctk.CTkFrame(self.lista_clipes, fg_color=CARTAO_2, corner_radius=12)
            linha.pack(fill="x", padx=8, pady=4)
            ctk.CTkLabel(linha, text="▶", width=40, height=40, corner_radius=10, fg_color=AZUL,
                         text_color=TEXTO, font=F(15, True)).pack(side="left", padx=12, pady=10)
            self._botao(linha, "Mostrar na pasta", lambda a=arq: self.mostrar_arquivo(a),
                        largura=140).pack(side="right", padx=(6, 12))
            self._botao(linha, "Assistir", lambda a=arq: os.startfile(a), principal=True,
                        largura=90).pack(side="right")
            info = ctk.CTkFrame(linha, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(info, text=os.path.basename(arq), font=F(13, True), text_color=TEXTO,
                         anchor="w").pack(fill="x")
            try:
                quando = datetime.fromtimestamp(os.path.getmtime(arq)).strftime("%d/%m/%Y às %H:%M")
                tamanho = os.path.getsize(arq) / (1024 * 1024)
                detalhe = f"{quando}, {tamanho:.1f} MB"
            except Exception:
                detalhe = ""
            ctk.CTkLabel(info, text=detalhe, font=F(12), text_color=APAGADO,
                         anchor="w").pack(fill="x")

    def mostrar_arquivo(self, caminho):
        subprocess.Popen(f'explorer /select,"{os.path.normpath(caminho)}"')

    def toast(self, texto, tipo="info"):
        cor = {"info": AZUL, "sucesso": VERDE, "erro": VERMELHO}[tipo]
        try:
            if self.toast_atual:
                self.toast_atual.destroy()
        except Exception:
            pass
        t = ctk.CTkFrame(self, fg_color=CARTAO, corner_radius=14, border_width=1, border_color=cor)
        ctk.CTkLabel(t, text="●", text_color=cor, font=F(14)).pack(side="left", padx=(16, 8), pady=12)
        ctk.CTkLabel(t, text=texto, text_color=TEXTO, font=F(13), wraplength=380,
                     justify="left").pack(side="left", padx=(0, 18), pady=12)
        t.place(relx=1.0, rely=1.0, x=-26, y=-26, anchor="se")
        self.toast_atual = t
        self.after(3800, lambda: t.destroy() if t.winfo_exists() else None)

    # ------------------------------------------------------------ fila de eventos
    def _processar_eventos(self):
        try:
            while True:
                ev = self.eventos.get_nowait()
                nome = ev[0]
                if nome == "salvar":
                    self.salvar_replay()
                elif nome == "alternar":
                    self.alternar_buffer()
                elif nome == "salvo":
                    self.btn_salvar.configure(text="Salvar replay", state="normal")
                    self.toast(f"Replay salvo: {os.path.basename(ev[1])}", "sucesso")
                    self.bipe()
                    self.atualizar_clipes()
                elif nome == "erro_salvar":
                    self.btn_salvar.configure(text="Salvar replay", state="normal")
                    self.toast(ev[1], "erro")
                elif nome == "erro":
                    self.toast(ev[1], "erro")
                elif nome == "parado":
                    self.parando = False
                    self._atualizar_estado()
                    if self.reiniciar_depois:
                        self.reiniciar_depois = False
                        self.iniciar()
                    else:
                        self.toast("Buffer desligado.")
                elif nome == "caiu":
                    self._atualizar_estado()
                    messagebox.showerror(
                        APP_NOME, "A gravação parou sozinha. Detalhes do FFmpeg:\n\n" + ev[1]
                        + "\n\nDica: tente outro encoder na aba Vídeo ou confira o "
                          "dispositivo de áudio.")
                elif nome == "encoders":
                    self._receber_encoders(ev[1])
                elif nome == "dispositivos":
                    self._receber_dispositivos(ev[1])
                elif nome == "tecla":
                    self._receber_tecla(ev[1], ev[2])
                elif nome == "pronto" and self.cfg["iniciar_auto"]:
                    self.iniciar()
        except queue.Empty:
            pass
        self.after(100, self._processar_eventos)

    def fechar(self):
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        if self.gravador and self.gravador.ativo:
            self.gravador.parar()
        salvar_config(self.cfg)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
