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
import socket
import struct
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
VERSAO = "1.4.0"

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
PRIORIDADE_BAIXA = 0x00004000 if os.name == "nt" else 0
PASTA_APP = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Z1UL Gravador")
ARQ_CONFIG = os.path.join(PASTA_APP, "config.json")
PASTA_BUFFER = os.path.join(tempfile.gettempdir(), "z1ul_buffer")
ARQ_LOG = os.path.join(PASTA_APP, "log.txt")


def registrar_log(texto):
    try:
        os.makedirs(PASTA_APP, exist_ok=True)
        if os.path.exists(ARQ_LOG) and os.path.getsize(ARQ_LOG) > 512 * 1024:
            os.remove(ARQ_LOG)
        with open(ARQ_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%d/%m %H:%M:%S}] {texto}\n")
    except Exception:
        pass

ENCODERS = {
    "Automático": None,
    "NVIDIA (NVENC)": "h264_nvenc",
    "AMD (AMF)": "h264_amf",
    "Intel (Quick Sync)": "h264_qsv",
    "Processador (x264)": "libx264",
}
NOME_ENCODER = {v: k for k, v in ENCODERS.items() if v}
QUALIDADES = {"Leve": 28, "Média": 24, "Alta": 21, "Ultra": 18}
RESOLUCOES = {"Igual ao jogo": None, "1280x720": (1280, 720), "1600x900": (1600, 900),
              "1920x1080": (1920, 1080), "2560x1440": (2560, 1440), "3840x2160": (3840, 2160)}
MODOS = ["Jogo", "Janela", "Tela inteira"]
AJUSTES = ["Preencher", "Esticar", "Encaixar"]
METODOS = ["Automático", "Janela do jogo", "Compatível"]
WIN11 = os.name == "nt" and sys.getwindowsversion().build >= 22000
FILTROS_CPU = ("f_cor", "f_vibrancia", "f_nitidez", "f_ruido", "f_pb", "f_vinheta")
BOTOES_MOUSE = {"mouse1": 0x0001, "mouse2": 0x0004, "mouse3": 0x0010,
                "mouse4": 0x0040, "mouse5": 0x0100}  # bits de "botão apertado" do Raw Input
NOMES_MOUSE = {"mouse1": "Mouse 1 (esquerdo)", "mouse2": "Mouse 2 (direito)",
               "mouse3": "Mouse 3 (rodinha)", "mouse4": "Mouse 4 (lateral)",
               "mouse5": "Mouse 5 (lateral)"}
PADRAO_AUDIO = "Padrão do Windows"
PREV_W, PREV_H = 640, 360
MONITORES = ["Monitor 1", "Monitor 2", "Monitor 3", "Monitor 4"]
SEM_DISPOSITIVO = "Nenhum dispositivo encontrado"

PADRAO = {
    "duracao": 30, "fps": "60", "monitor": "Monitor 1", "cursor": True,
    "resolucao": "1920x1080", "qualidade": "Alta", "encoder": "Automático",
    "modo_captura": "Jogo", "janela_alvo": "", "janela_exe": "", "previa": True,
    "ajuste": "Preencher", "virar": False, "metodo": "Automático",
    "perfil": "Nenhum",
    "f_cor": False, "brilho": 0.0, "contraste": 1.0, "saturacao": 1.0, "gama": 1.0,
    "f_vibrancia": False, "vibrancia": 0.3,
    "f_nitidez": False, "nitidez": 0.6,
    "f_ruido": False, "ruido": 4.0,
    "f_pb": False,
    "f_vinheta": False, "vinheta": 0.5,
    "sistema_on": True, "sistema_disp": PADRAO_AUDIO, "vol_sistema": 100,
    "mic_on": True, "mic_disp": PADRAO_AUDIO, "vol_mic": 100, "mic_ruido": True,
    "bitrate_audio": "160k",
    "tecla_salvar": "alt+f10", "tecla_buffer": "alt+f9",
    "som_salvar": True, "iniciar_auto": False,
    "pasta": os.path.join(os.path.expanduser("~"), "Videos", "Z1UL Replays"),
}

OPCOES_VALIDAS = {
    "fps": ["30", "60", "120"], "monitor": MONITORES, "modo_captura": MODOS, "ajuste": AJUSTES, "metodo": METODOS, "resolucao": list(RESOLUCOES),
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
NAO_REINICIA = {"tecla_salvar", "tecla_buffer", "som_salvar", "iniciar_auto", "pasta", "perfil",
                "modo_captura", "janela_alvo", "janela_exe", "monitor", "previa", "metodo"}


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
        cfg["modo_captura"] = "Jogo"
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


def iniciar_com():
    if os.name == "nt":
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0)
        except Exception:
            pass


def _pa():
    import pyaudiowpatch as pa
    return pa


def _wasapi(p, pa):
    w = p.get_host_api_info_by_type(pa.paWASAPI)
    lista = [p.get_device_info_by_host_api_device_index(w["index"], i)
             for i in range(w["deviceCount"])]
    return w, lista


def listar_audio():
    """(saídas, microfones, erro). Chamar fora da thread da interface."""
    iniciar_com()
    erros = []
    try:
        pa = _pa()
        p = pa.PyAudio()
        try:
            _, lista = _wasapi(p, pa)
            saidas = [d["name"] for d in lista
                      if d["maxOutputChannels"] > 0 and not d.get("isLoopbackDevice")]
            entradas = [d["name"] for d in lista
                        if d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice")]
            return saidas, entradas, ""
        finally:
            p.terminate()
    except Exception as e:
        erros.append(f"WASAPI: {e}")
    try:
        import soundcard as sc
        return [d.name for d in sc.all_speakers()], [d.name for d in sc.all_microphones()], ""
    except Exception as e:
        erros.append(f"soundcard: {e}")
    return [], [], " | ".join(erros)


def sondar_audio(tipo, nome):
    """Descobre o aparelho, a taxa e os canais. Retorna (info, erro)."""
    iniciar_com()
    try:
        pa = _pa()
    except Exception:
        return {"backend": "soundcard", "taxa": 48000, "canais": 2, "nome": nome}, ""
    try:
        p = pa.PyAudio()
        try:
            w, lista = _wasapi(p, pa)
            escolhido = nome if nome not in ("", PADRAO_AUDIO) else None
            if tipo == "sistema":
                alvo = escolhido or p.get_device_info_by_index(w["defaultOutputDevice"])["name"]
                loops = [d for d in lista if d.get("isLoopbackDevice")]
                d = (next((x for x in loops if x["name"].startswith(alvo)), None)
                     or next((x for x in loops if alvo in x["name"]), None))
                if not d:
                    raise RuntimeError(f"não encontrei o som de \"{alvo}\"")
                canais = int(d["maxInputChannels"])
            else:
                d = None
                if escolhido:
                    d = next((x for x in lista if x["name"] == escolhido
                              and x["maxInputChannels"] > 0
                              and not x.get("isLoopbackDevice")), None)
                if d is None:
                    if w["defaultInputDevice"] < 0:
                        raise RuntimeError("nenhum microfone encontrado")
                    d = p.get_device_info_by_index(w["defaultInputDevice"])
                canais = min(2, int(d["maxInputChannels"]))
            return {"backend": "wasapi", "index": int(d["index"]),
                    "taxa": int(d["defaultSampleRate"]), "canais": max(1, canais),
                    "nome": d["name"]}, ""
        finally:
            p.terminate()
    except Exception as e:
        return None, str(e)


def em_thread(funcao, tempo=5):
    resultado = [None]

    def rodar_():
        try:
            resultado[0] = funcao()
        except Exception:
            pass
    t = threading.Thread(target=rodar_, daemon=True)
    t.start()
    t.join(tempo)
    return resultado[0]


def tem_gfxcapture(ffmpeg):
    r = rodar([ffmpeg, "-hide_banner", "-nostdin", "-h", "filter=gfxcapture"])
    texto = (r.stdout + r.stderr).decode("utf-8", "replace")
    return r.returncode == 0 and "Unknown filter" not in texto and "gfxcapture" in texto


def porta_livre():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s_:
        s_.bind(("127.0.0.1", 0))
        return s_.getsockname()[1]


def fmt_dur(v):
    s = int(v)
    if s < 60:
        return f"{s} s"
    return f"{s // 60} min {s % 60:02d} s" if s % 60 else f"{s // 60} min"


def fmt_relogio(s):
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def fmt_tecla(t):
    if t in NOMES_MOUSE:
        return NOMES_MOUSE[t]
    return " + ".join(p.capitalize() if len(p) > 1 else p.upper() for p in t.split("+")) if t else "Nenhum"


def F(tamanho, negrito=False, titulo=False):
    return ctk.CTkFont(family=FONTE_TITULO if titulo else FONTE, size=tamanho,
                       weight="bold" if negrito else "normal")


# ================================================================== janelas e jogos
NO_WINDOWS = os.name == "nt"
if NO_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    dwmapi = ctypes.windll.dwmapi
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                         ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MONITORENUMPROC,
                                           wintypes.LPARAM]
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p,
                                             wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                     wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

# trechos do nome do processo de jogos conhecidos (FiveM, emuladores de Free Fire etc.)
JOGOS_CONHECIDOS = (
    "gtaprocess", "gta5", "gta5_enhanced", "ragemp", "altv",                 # GTA / FiveM
    "hd-player", "bluestacks", "dnplayer", "ldplayer", "nox.exe", "memu",    # emuladores
    "androidemulator", "aow_exe", "msi app player",
    "valorant", "cs2.exe", "csgo", "fortniteclient", "robloxplayer", "r5apex",
    "league of legends", "rocketleague", "overwatch", "eldenring", "rdr2",
    "genshinimpact", "pubg", "tslgame", "cod.exe", "modernwarfare", "minecraft",
    "fc24", "fc25", "fc26", "rainbowsix", "deadbydaylight", "warzone",
)
# programas que nunca são tratados como jogo, mesmo em tela cheia
NUNCA_JOGO = (
    "explorer.exe", "applicationframehost.exe", "textinputhost.exe", "searchhost.exe",
    "shellexperiencehost.exe", "startmenuexperiencehost.exe", "lockapp.exe",
    "systemsettings.exe", "z1ul gravador.exe", "z1ul_setup.exe", "chrome.exe", "msedge.exe",
    "firefox.exe", "opera.exe", "brave.exe", "vlc.exe", "wmplayer.exe", "discord.exe",
    "obs64.exe", "fivem.exe",
)


JOB = None
if NO_WINDOWS:
    class _Basico(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("a", "b", "c", "d", "e", "f")]

    class _Estendido(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _Basico), ("IoInfo", _IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    try:
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                     ctypes.c_void_p, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        JOB = kernel32.CreateJobObjectW(None, None)
        _info = _Estendido()
        _info.BasicLimitInformation.LimitFlags = 0x2000  # o FFmpeg fecha junto com o app
        kernel32.SetInformationJobObject(JOB, 9, ctypes.byref(_info), ctypes.sizeof(_info))
    except Exception:
        JOB = None


def prender_ao_app(proc):
    if JOB:
        try:
            kernel32.AssignProcessToJobObject(JOB, int(proc._handle))
        except Exception:
            pass


MODIFICADORES = {"alt": 1, "left alt": 1, "right alt": 1, "alt gr": 1, "ctrl": 2,
                 "left ctrl": 2, "right ctrl": 2, "control": 2, "shift": 4, "left shift": 4,
                 "right shift": 4, "windows": 8, "left windows": 8, "right windows": 8}
TECLAS_VK = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "backspace": 0x08,
             "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23, "page up": 0x21,
             "page down": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
             "print screen": 0x2C, "pause": 0x13, "scroll lock": 0x91, "caps lock": 0x14,
             "num lock": 0x90}


def converter_atalho(texto):
    """"alt+f10" -> (modificadores, código da tecla) para o RegisterHotKey do Windows."""
    mods, vk = 0, 0
    for parte in texto.split("+"):
        nome = parte.strip().lower()
        if nome in MODIFICADORES:
            mods |= MODIFICADORES[nome]
        elif len(nome) == 1 and nome.isascii() and nome.isalnum():
            vk = ord(nome.upper())
        elif nome.startswith("f") and nome[1:].isdigit() and 1 <= int(nome[1:]) <= 24:
            vk = 0x70 + int(nome[1:]) - 1
        elif nome in TECLAS_VK:
            vk = TECLAS_VK[nome]
        else:
            try:
                vk = user32.MapVirtualKeyW(keyboard.key_to_scan_codes(nome)[0], 3)
            except Exception:
                return None
    return (mods, vk) if vk else None


if NO_WINDOWS:
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                 wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

    class RAWINPUTDEVICE(ctypes.Structure):
        _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                    ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]

    class RAWINPUTHEADER(ctypes.Structure):
        _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                    ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                      wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                       wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                       wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT,
                                               wintypes.UINT]
    user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                       ctypes.POINTER(wintypes.UINT), wintypes.UINT]
    user32.GetRawInputData.restype = wintypes.UINT
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                    wintypes.LPARAM]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                   wintypes.UINT]
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class EntradaGlobal:
    """Atalhos de teclado pelo próprio Windows (RegisterHotKey) e botões do mouse por Raw Input.
    Nenhum dos dois é um "hook": não atrasa o mouse nem o teclado dentro do jogo."""
    WM_APLICAR = 0x8001

    def __init__(self, ao_atalho, ao_mouse_captura):
        self.ao_atalho = ao_atalho
        self.ao_captura = ao_mouse_captura
        self.hwnd = None
        self.atalhos = {}
        self.pedidos = []
        self.mouse = {}
        self.capturando = False
        self.mouse_registrado = False
        self.falhas = []
        self.resultado = threading.Event()
        self.pronto = threading.Event()
        self._buf = ctypes.create_string_buffer(128)
        self._cab = ctypes.sizeof(RAWINPUTHEADER)
        threading.Thread(target=self._rodar, daemon=True).start()
        self.pronto.wait(3)

    def aplicar(self, pedidos, mouse_map, capturando=False):
        self.pedidos, self.mouse, self.capturando = pedidos, mouse_map, capturando
        if not self.hwnd:
            return [p[0] for p in pedidos]
        self.resultado.clear()
        user32.PostMessageW(self.hwnd, self.WM_APLICAR, 0, 0)
        self.resultado.wait(2)
        return list(self.falhas)

    def _rodar(self):
        try:
            self._proc_ref = WNDPROC(self._proc)
            instancia = kernel32.GetModuleHandleW(None)
            classe = WNDCLASSW()
            classe.lpfnWndProc = self._proc_ref
            classe.hInstance = instancia
            classe.lpszClassName = "Z1ULEntrada"
            user32.RegisterClassW(ctypes.byref(classe))
            somente_mensagens = wintypes.HWND(ctypes.c_size_t(-3).value)
            self.hwnd = user32.CreateWindowExW(0, "Z1ULEntrada", "Z1UL", 0, 0, 0, 0, 0,
                                               somente_mensagens, None, instancia, None)
        except Exception as e:
            registrar_log(f"Entrada global: {e!r}")
            self.hwnd = None
        self.pronto.set()
        if not self.hwnd:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _aplicar_agora(self):
        for i in list(self.atalhos):
            user32.UnregisterHotKey(self.hwnd, i)
        self.atalhos, self.falhas = {}, []
        if not self.capturando:
            for n, (evento, mods, vk) in enumerate(self.pedidos, start=1):
                if user32.RegisterHotKey(self.hwnd, n, mods | 0x4000, vk):
                    self.atalhos[n] = evento
                else:
                    self.falhas.append(evento)
        precisa = self.capturando or bool(self.mouse)
        if precisa != self.mouse_registrado:
            disp = RAWINPUTDEVICE(1, 2, 0x100 if precisa else 0x1, self.hwnd if precisa else None)
            if user32.RegisterRawInputDevices(ctypes.byref(disp), 1, ctypes.sizeof(disp)):
                self.mouse_registrado = precisa
        self.resultado.set()

    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == self.WM_APLICAR:
                self._aplicar_agora()
                return 0
            if msg == 0x0312:  # WM_HOTKEY
                evento = self.atalhos.get(int(wparam))
                if evento:
                    self.ao_atalho(evento)
                return 0
            if msg == 0x00FF:  # WM_INPUT
                self._entrada(lparam)
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _entrada(self, lparam):
        tam = wintypes.UINT(len(self._buf))
        lido = user32.GetRawInputData(lparam, 0x10000003, self._buf, ctypes.byref(tam), self._cab)
        if lido in (0, 0xFFFFFFFF) or struct.unpack_from("<I", self._buf, 0)[0] != 0:
            return
        bits = struct.unpack_from("<H", self._buf, self._cab + 4)[0]
        if not bits:
            return  # só movimento: sai rápido
        for codigo, bit in BOTOES_MOUSE.items():
            if bits & bit:
                if self.capturando:
                    if codigo != "mouse1":
                        self.ao_captura(codigo)
                elif codigo in self.mouse:
                    self.ao_atalho(self.mouse[codigo])


def nome_processo(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        tam = wintypes.DWORD(520)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(tam)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def info_janela(hwnd):
    if not NO_WINDOWS or not hwnd or not user32.IsWindow(hwnd):
        return None
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    ponto = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(ponto))
    return {"hwnd": hwnd, "titulo": buf.value, "pid": pid.value, "exe": nome_processo(pid.value),
            "x": ponto.x, "y": ponto.y, "w": r.right, "h": r.bottom,
            "minimizada": bool(user32.IsIconic(hwnd))}


def janela_oculta(hwnd):
    valor = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(valor), ctypes.sizeof(valor))
    return valor.value != 0


def listar_janelas():
    if not NO_WINDOWS:
        return []
    resultado = []
    meu_pid = os.getpid()

    def cb(hwnd, _):
        try:
            if (user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0
                    and not janela_oculta(hwnd)
                    and not user32.GetWindowLongW(hwnd, -20) & 0x80):
                i = info_janela(hwnd)
                if i and i["pid"] != meu_pid and i["exe"] not in NUNCA_JOGO[:8] and (
                        i["minimizada"] or (i["w"] >= 320 and i["h"] >= 240)):
                    resultado.append(i)
        except Exception:
            pass
        return True
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return resultado


def listar_monitores():
    if not NO_WINDOWS:
        return [{"x": 0, "y": 0, "w": 1920, "h": 1080, "primario": True}]
    lista = []

    def cb(hmon, hdc, prect, _):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r = info.rcMonitor
        lista.append({"x": r.left, "y": r.top, "w": r.right - r.left, "h": r.bottom - r.top,
                      "primario": bool(info.dwFlags & 1)})
        return True
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    lista.sort(key=lambda m: (not m["primario"], m["x"], m["y"]))
    return lista or [{"x": 0, "y": 0, "w": 1920, "h": 1080, "primario": True}]


def eh_jogo_conhecido(i):
    exe = i["exe"]
    return exe not in NUNCA_JOGO and any(j in exe for j in JOGOS_CONHECIDOS)


def em_tela_cheia(i, monitores):
    return any(i["x"] == m["x"] and i["y"] == m["y"] and i["w"] == m["w"] and i["h"] == m["h"]
               for m in monitores)


def achar_jogo(hwnd_atual):
    """Escolhe o jogo a gravar. Fica preso ao mesmo jogo enquanto ele estiver aberto."""
    monitores = listar_monitores()
    atual = info_janela(hwnd_atual) if hwnd_atual else None
    if atual and (atual["minimizada"] or eh_jogo_conhecido(atual)
                  or (atual["exe"] not in NUNCA_JOGO and em_tela_cheia(atual, monitores))):
        return atual
    frente = user32.GetForegroundWindow() if NO_WINDOWS else None
    conhecidos = []
    for j in listar_janelas():
        if j["minimizada"]:
            continue
        conhecido = eh_jogo_conhecido(j)
        if j["hwnd"] == frente and (conhecido or (j["exe"] not in NUNCA_JOGO
                                                  and em_tela_cheia(j, monitores))):
            return j
        if conhecido:
            conhecidos.append(j)
    return conhecidos[0] if conhecidos else None


def achar_janela(titulo, exe):
    if not titulo and not exe:
        return None
    janelas = listar_janelas()
    for j in janelas:
        if j["titulo"] == titulo:
            return j
    for j in janelas:
        if exe and j["exe"] == exe:
            return j
    return None


def calcular_regiao(i):
    """Converte a área da janela em coordenadas do monitor onde ela está (para o ddagrab)."""
    monitores = listar_monitores()
    cx, cy = i["x"] + i["w"] // 2, i["y"] + i["h"] // 2
    idx, m = 0, monitores[0]
    for n, mon in enumerate(monitores):
        if mon["x"] <= cx < mon["x"] + mon["w"] and mon["y"] <= cy < mon["y"] + mon["h"]:
            idx, m = n, mon
            break
    x, y = max(i["x"], m["x"]), max(i["y"], m["y"])
    x2, y2 = min(i["x"] + i["w"], m["x"] + m["w"]), min(i["y"] + i["h"], m["y"] + m["h"])
    w, h = (x2 - x) // 2 * 2, (y2 - y) // 2 * 2
    if w < 64 or h < 64:
        return None
    return {"monitor": idx, "x": x - m["x"], "y": y - m["y"], "w": w, "h": h,
            "titulo": i["titulo"] or i["exe"], "hwnd": i["hwnd"]}


def chave_regiao(r):
    if r is None:
        return None
    if r["tipo"] == "janela":
        return ("janela", r["hwnd"], r["w"], r["h"])
    if r["tipo"] == "monitor":
        return ("monitor", r["monitor"])
    return ("regiao", r["monitor"], r["x"], r["y"], r["w"], r["h"])


def janela_existe(hwnd):
    return bool(NO_WINDOWS and hwnd and user32.IsWindow(hwnd))


# ================================================================== FFmpeg
def usar_gpu_direto(cfg, fonte, encoder):
    """Sem filtros de CPU, a imagem vai da placa de vídeo direto para o encoder (menos FPS perdido)."""
    return (fonte["tipo"] == "janela" and encoder in ("h264_nvenc", "h264_amf")
            and not cfg["virar"] and not any(cfg[k] for k in FILTROS_CPU))


def recorte_preencher(w, h, alvo_w, alvo_h):
    """Quanto cortar de cada lado para a imagem preencher o formato do vídeo."""
    if not w or not h:
        return 0, 0, 0, 0
    if w * alvo_h > h * alvo_w:
        novo = h * alvo_w // alvo_h
        c = (w - novo) // 2
        return c, w - novo - c, 0, 0
    novo = w * alvo_h // alvo_w
    c = (h - novo) // 2
    return 0, 0, c, h - novo - c


def texto_fonte(cfg, fonte):
    fps = int(cfg["fps"])
    cursor = 1 if cfg["cursor"] else 0
    if fonte["tipo"] == "regiao":  # modo compatível: tela inteira, recortada depois
        return f"ddagrab=output_idx={fonte['monitor']}:framerate={fps}:draw_mouse={cursor}"
    s_ = (f"gfxcapture=hwnd={int(fonte['hwnd'])}:max_framerate={fps}"
          f":capture_cursor={cursor}:display_border=0")
    tamanho = RESOLUCOES.get(cfg["resolucao"])
    if tamanho:  # redimensiona na placa de vídeo, mais leve que no processador
        w, h = tamanho
        if cfg["ajuste"] == "Encaixar":
            s_ += f":width={w}:height={h}:resize_mode=scale_aspect"
        else:
            s_ += f":width={w}:height={h}:resize_mode=scale"
            if cfg["ajuste"] == "Preencher":
                l, r, t, b = recorte_preencher(fonte["w"], fonte["h"], w, h)
                s_ += f":crop_left={l}:crop_right={r}:crop_top={t}:crop_bottom={b}"
    else:
        s_ += ":width=-2:height=-2"
    return s_


def escala_cpu(cfg):
    tamanho = RESOLUCOES.get(cfg["resolucao"])
    if not tamanho:
        return ["scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    w, h = tamanho
    if cfg["ajuste"] == "Esticar":
        return [f"scale={w}:{h}:flags=bilinear"]
    if cfg["ajuste"] == "Encaixar":
        return [f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2"
                f":flags=bilinear", f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"]
    return [f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=bilinear",
            f"crop={w}:{h}"]


PREVIA = (f"fps=5,hwdownload,format=bgra,scale={PREV_W}:{PREV_H}"
          f":force_original_aspect_ratio=decrease,"
          f"pad={PREV_W}:{PREV_H}:(ow-iw)/2:(oh-ih)/2:color=black,format=rgb24[prev]")


def filtro_video(cfg, fonte, direto=False):
    fonte_txt = texto_fonte(cfg, fonte)
    if direto:
        return f"{fonte_txt},split=2[v][pv];[pv]{PREVIA}"
    f = [fonte_txt, "hwdownload", "format=bgra"]
    if fonte["tipo"] == "regiao":
        if fonte["w"]:
            f.append(f"crop={fonte['w']}:{fonte['h']}:{fonte['x']}:{fonte['y']}")
        f += escala_cpu(cfg)
    if cfg["virar"]:
        f.append("vflip")
    if cfg["f_cor"]:
        f.append(f"eq=brightness={cfg['brilho']:.2f}:contrast={cfg['contraste']:.2f}"
                 f":saturation={cfg['saturacao']:.2f}:gamma={cfg['gama']:.2f}")
    if cfg["f_vibrancia"]:
        f.append(f"vibrance=intensity={cfg['vibrancia']:.2f}")
    if cfg["f_nitidez"]:
        f.append(f"unsharp=5:5:{cfg['nitidez']:.2f}:5:5:0")
    if cfg["f_ruido"]:
        s_ = float(cfg["ruido"])
        f.append(f"hqdn3d={s_ / 2:.1f}:{s_ / 2:.1f}:{s_:.1f}:{s_:.1f}")
    if cfg["f_pb"]:
        f.append("hue=s=0")
    if cfg["f_vinheta"]:
        f.append(f"vignette=angle={cfg['vinheta']:.2f}")
    f += ["setsar=1", "format=yuv420p"]
    return (",".join(f) + ",split=2[v][pv];"
            f"[pv]fps=5,scale={PREV_W}:{PREV_H}:force_original_aspect_ratio=decrease,"
            f"pad={PREV_W}:{PREV_H}:(ow-iw)/2:(oh-ih)/2:color=black,format=rgb24[prev]")


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
        a = ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-crf", q]
    return a + ["-g", str(fps)]


def montar_comando(ffmpeg, cfg, encoder, fonte, portas, direto=False):
    """portas: [(tipo, porta, taxa)] - o áudio chega do próprio app por conexões locais."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    for _, porta, taxa in portas:
        cmd += ["-f", "f32le", "-ar", str(taxa), "-ac", "2", "-probesize", "32",
                "-analyzeduration", "0", "-thread_queue_size", "1024",
                "-i", f"tcp://127.0.0.1:{porta}?listen=1&listen_timeout=20000"]

    partes = [filtro_video(cfg, fonte, direto)]
    rotulos = []
    for i, (tipo, _, _) in enumerate(portas):
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
    cmd += ["-max_interleave_delta", "1000000",
            "-f", "segment", "-segment_time", str(SEG), "-segment_wrap", str(voltas),
            "-segment_format", "mpegts", "-reset_timestamps", "1",
            os.path.join(PASTA_BUFFER, "seg%03d.ts")]
    cmd += ["-map", "[prev]", "-c:v", "rawvideo", "-f", "rawvideo", "pipe:1"]
    return cmd


class BombaAudio:
    """Captura o som (do Windows ou do microfone) e entrega ao FFmpeg no ritmo do relógio.
    Quando não há som tocando, envia silêncio, para o áudio nunca sair de sincronia."""
    BYTES = 8           # 2 canais x float32
    PREFIXO = 2048      # silêncio inicial para o FFmpeg reconhecer o formato

    def __init__(self, tipo, dispositivo, porta, video_iniciou, eventos, info, erro, taxa):
        self.info = info
        self.erro = erro
        self.TAXA = taxa
        self.MAXIMO = taxa * self.BYTES // 4  # no máximo 0,25 s guardado
        self.tipo = tipo
        self.dispositivo = dispositivo
        self.porta = porta
        self.video_iniciou = video_iniciou
        self.eventos = eventos
        self.parar_ev = threading.Event()
        self.fila = deque()
        self.tamanho = 0
        self.trava = threading.Lock()
        self.sock = None

    def iniciar(self):
        threading.Thread(target=self._capturar, daemon=True).start()
        threading.Thread(target=self._enviar, daemon=True).start()

    def parar(self):
        self.parar_ev.set()
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass

    def _guardar(self, dados, np):
        if dados.ndim == 1:
            dados = dados[:, None]
        if dados.shape[1] == 1:
            dados = np.repeat(dados, 2, axis=1)
        elif dados.shape[1] > 2:
            dados = dados[:, :2]
        bloco_ = np.ascontiguousarray(dados, dtype="<f4").tobytes()
        with self.trava:
            self.fila.append(bloco_)
            self.tamanho += len(bloco_)
            while self.tamanho > self.MAXIMO and self.fila:
                self.tamanho -= len(self.fila.popleft())

    def _capturar(self):
        iniciar_com()
        try:
            if self.info is None:
                raise RuntimeError(self.erro or "dispositivo não encontrado")
            if self.info["backend"] == "wasapi":
                self._capturar_wasapi()
            else:
                self._capturar_soundcard()
        except Exception as e:
            if not self.parar_ev.is_set():
                nome = "Som do jogo" if self.tipo == "sistema" else "Microfone"
                registrar_log(f"{nome}: {e!r}")
                self.eventos.put(("aviso_audio", f"{nome} indisponível: {e}"))

    def _capturar_wasapi(self):
        import numpy as np
        pa = _pa()
        p = pa.PyAudio()
        canais = self.info["canais"]

        def receber(dados, n, info_tempo, estado):
            self._guardar(np.frombuffer(dados, dtype="<f4").reshape(-1, canais), np)
            return (None, pa.paContinue)
        try:
            fluxo = p.open(format=pa.paFloat32, channels=canais, rate=self.TAXA, input=True,
                           input_device_index=self.info["index"], frames_per_buffer=480,
                           stream_callback=receber)
            try:
                fluxo.start_stream()
                while not self.parar_ev.wait(0.2):
                    pass
            finally:
                try:
                    fluxo.stop_stream()
                    fluxo.close()
                except Exception:
                    pass
        finally:
            p.terminate()

    def _capturar_soundcard(self):
        import numpy as np
        import soundcard as sc
        escolhido = self.dispositivo if self.dispositivo not in ("", PADRAO_AUDIO) else None
        if self.tipo == "sistema":
            saida = None
            if escolhido:
                saida = next((d for d in sc.all_speakers() if d.name == escolhido), None)
            saida = saida or sc.default_speaker()
            disp, canais = sc.get_microphone(id=str(saida.name), include_loopback=True), 2
        else:
            mic = None
            if escolhido:
                mic = next((d for d in sc.all_microphones() if d.name == escolhido), None)
            disp, canais = (mic or sc.default_microphone()), 1
        with disp.recorder(samplerate=self.TAXA, channels=canais, blocksize=1024) as rec:
            while not self.parar_ev.is_set():
                self._guardar(rec.record(numframes=480), np)

    def _enviar(self):
        limite = time.time() + 20
        while not self.parar_ev.is_set() and time.time() < limite:
            try:
                self.sock = socket.create_connection(("127.0.0.1", self.porta), timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        if not self.sock:
            return
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.settimeout(None)
            self.sock.sendall(bytes(self.PREFIXO * self.BYTES))
            # espera o vídeo começar, para som e imagem saírem juntos
            self.video_iniciou.wait(3)
            with self.trava:
                self.fila.clear()
                self.tamanho = 0
            inicio = time.perf_counter()
            enviados = 0
            resto = b""
            while not self.parar_ev.is_set():
                falta = int((time.perf_counter() - inicio) * self.TAXA) - enviados
                if falta < 240:
                    time.sleep(0.004)
                    continue
                precisa = falta * self.BYTES
                partes, obtido = [resto], len(resto)
                with self.trava:
                    while obtido < precisa and self.fila:
                        b = self.fila.popleft()
                        self.tamanho -= len(b)
                        partes.append(b)
                        obtido += len(b)
                dados = b"".join(partes)
                if len(dados) >= precisa:
                    bloco, resto = dados[:precisa], dados[precisa:]
                else:
                    bloco, resto = dados + bytes(precisa - len(dados)), b""
                self.sock.sendall(bloco)
                enviados += falta
        except OSError:
            pass
        finally:
            try:
                self.sock.close()
            except OSError:
                pass


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
        self.regiao = None
        self.quadro = None
        self.quadro_n = 0
        self.bombas = []
        self.video_iniciou = threading.Event()

    @property
    def ativo(self):
        return self.proc is not None and self.proc.poll() is None

    def iniciar(self, cfg, encoder, fonte, direto=False):
        if self.ativo:
            return
        self.direto = direto
        self.cfg = dict(cfg)
        self.encoder = encoder
        self.regiao = fonte
        self.quadro = None
        shutil.rmtree(PASTA_BUFFER, ignore_errors=True)
        os.makedirs(PASTA_BUFFER, exist_ok=True)
        self.log.clear()
        self.parando = False
        self.video_iniciou = threading.Event()

        fontes_audio = []
        for tipo, ligado, chave in (("sistema", self.cfg["sistema_on"], "sistema_disp"),
                                    ("mic", self.cfg["mic_on"], "mic_disp")):
            if not ligado:
                continue
            disp = self.cfg[chave]
            res = em_thread(lambda t=tipo, d=disp: sondar_audio(t, d), 5) or (None, "tempo esgotado")
            info, erro = res
            taxa = int(info["taxa"]) if info else 48000
            fontes_audio.append((tipo, disp, porta_livre(), taxa, info, erro))
        portas = [(t, porta, taxa) for t, _, porta, taxa, _, _ in fontes_audio]

        self.proc = subprocess.Popen(
            montar_comando(self.ffmpeg, self.cfg, encoder, fonte, portas, direto),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=SEM_JANELA | PRIORIDADE_BAIXA)  # prioridade abaixo do normal: o jogo vem primeiro
        prender_ao_app(self.proc)
        registrar_log("Iniciando: " + " ".join(montar_comando(self.ffmpeg, self.cfg, encoder,
                                                              fonte, portas, direto)))
        self.inicio = time.time()
        self.bombas = []
        for tipo, disp, porta, taxa, info, erro in fontes_audio:
            bomba = BombaAudio(tipo, disp, porta, self.video_iniciou, self.eventos, info, erro, taxa)
            bomba.iniciar()
            self.bombas.append(bomba)
        threading.Thread(target=self._vigiar, args=(self.proc,), daemon=True).start()
        threading.Thread(target=self._ler_previa, args=(self.proc,), daemon=True).start()

    def _ler_previa(self, proc):
        tamanho = PREV_W * PREV_H * 3
        while True:
            dados = proc.stdout.read(tamanho)
            if not dados or len(dados) < tamanho:
                break
            self.quadro = dados
            self.quadro_n += 1
            self.video_iniciou.set()

    def _vigiar(self, proc):
        for linha in proc.stderr:
            self.log.append(linha.decode("utf-8", errors="replace").rstrip())
        proc.wait()
        for b in self.bombas:
            b.parar()
        if not self.parando:
            detalhes = "\n".join(list(self.log)[-8:]) or f"Código de saída {proc.returncode}."
            registrar_log("A gravação parou: " + detalhes)
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
        for b in self.bombas:
            b.parar()
        self.bombas = []
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
        self.armado = False          # o usuário ligou o buffer
        self.regiao_atual = None
        self.alvo_hwnd = None
        self.candidato = None
        self.cont_candidato = 0
        self.falhas = []
        self.mapa_janelas = {}
        self.foto = None
        self.ultimo_quadro = -1
        self.usar_wgc = True
        self.avisou_audio = False
        self.sem_gpu_direto = False
        self.direto_atual = False
        self.captura_id = 0
        self.captura_chave = None
        self.captura_inicio = 0.0
        self.encoder_falhou = False

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
        self.entrada = None
        if NO_WINDOWS:
            self.entrada = EntradaGlobal(lambda ev: self.eventos.put((ev,)),
                                         lambda cod: self.eventos.put(("tecla_mouse", cod)))
        self.registrar_atalhos()

        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.after(100, self._processar_eventos)
        self.after(500, self._tique)
        self.after(1500, self._vigia)
        self.after(100, self._atualizar_previa)

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

        # prévia ao vivo, igual ao OBS
        moldura = tk.Frame(hero, width=PREV_W, height=PREV_H, bg="#000000",
                           highlightthickness=1, highlightbackground=BORDA)
        moldura.pack(padx=28, pady=(14, 6))
        moldura.pack_propagate(False)
        self.tela_previa = tk.Label(moldura, bg="#000000", fg=APAGADO, font=(FONTE, 12),
                                    text="", justify="center")
        self.tela_previa.pack(fill="both", expand=True)
        self.lbl_captura = ctk.CTkLabel(hero, text="", font=F(12), text_color=AZUL_CLARO)
        self.lbl_captura.pack(padx=28)

        # linha do tempo do buffer
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

        c = self._cartao(p, "Captura de jogo")
        self._linha(c, "Método de captura",
                    "Automático e Janela do jogo gravam só a janela do jogo. No Windows 10, "
                    "o próprio Windows mostra uma borda amarela enquanto grava (no Windows 11 "
                    "ela não aparece). Compatível não tem borda, mas pode sair preto ou "
                    "invertido em algumas placas de vídeo.",
                    self._segmentos("metodo", METODOS))
        self._linha(c, "Quadros por segundo", "Para perder menos FPS no jogo, use 30 ou 60. "
                                              "120 exige bem mais do PC.",
                    self._segmentos("fps", ["30", "60", "120"]))
        self._linha(c, "Mostrar o cursor", "Desligue em jogos de tiro para o vídeo ficar limpo.",
                    self._switch("cursor"))
        self._linha(c, "Prévia no Painel", "Mostra ao vivo o que está sendo gravado.",
                    self._switch("previa"))
        ctk.CTkLabel(c, text="O Z1UL encontra sozinho FiveM, GTA V, emuladores de Free Fire "
                             "(BlueStacks, MSI App Player, LDPlayer, Gameloop), Valorant, CS2, "
                             "Fortnite, Roblox e qualquer jogo em tela cheia. Para perder menos "
                             "FPS: use o encoder da placa de vídeo (NVIDIA ou AMD) e deixe os "
                             "filtros desligados, assim a imagem nem passa pelo processador.",
                     font=F(12), text_color=AZUL_CLARO, anchor="w", justify="left",
                     wraplength=700).pack(fill="x", padx=24, pady=(6, 0))
        self._fim_cartao(c)

        c = self._cartao(p, "Qualidade da imagem")
        self._linha(c, "Resolução da gravação", "Tamanho final do vídeo, sempre em paisagem "
                                                "(16:9).",
                    self._opcoes("resolucao", list(RESOLUCOES)))
        self._linha(c, "Ajuste da imagem", "Preencher ocupa o vídeo todo sem faixas pretas "
                                           "(corta um pouco das bordas se o formato for "
                                           "diferente). Esticar deforma para caber. Encaixar "
                                           "mostra tudo, com faixas pretas.",
                    self._segmentos("ajuste", AJUSTES))
        self._linha(c, "Imagem de cabeça para baixo?", "Ligue só se o vídeo sair invertido "
                                                       "no seu PC. Usa um pouco mais do PC.",
                    self._switch("virar"))
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
        inicial = [self.cfg["sistema_disp"] or PADRAO_AUDIO]

        c = self._cartao(p, "Som do jogo", "Captura tudo que sai pelos seus fones ou caixas de som.",
                         direita=self._switch("sistema_on"))
        self.menu_sistema = self._linha(
            c, "Onde você ouve o jogo", "Fone ou caixa de som. Padrão do Windows funciona "
                                        "para a maioria. Não precisa de Mixagem estéreo.",
            self._opcoes("sistema_disp", inicial, largura=320))
        self._linha(c, "Volume", None, self._slider("vol_sistema", 0, 200, 40,
                                                    lambda v: f"{int(v)}%", tipo="int"))
        self._fim_cartao(c)

        c = self._cartao(p, "Microfone", "Sua voz junto com o jogo.",
                         direita=self._switch("mic_on"))
        self.menu_mic = self._linha(c, "Dispositivo", None,
                                    self._opcoes("mic_disp", [self.cfg["mic_disp"] or PADRAO_AUDIO],
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
        self.lbl_audio = ctk.CTkLabel(c, text="Procurando fones e microfones…", font=F(12),
                                      text_color=AZUL_CLARO, anchor="w", justify="left",
                                      wraplength=700)
        self.lbl_audio.pack(fill="x", padx=24, pady=(6, 0))
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
                ctk.CTkOptionMenu(
                    f, values=list(NOMES_MOUSE.values()), width=190, height=36,
                    corner_radius=10, dynamic_resizing=False, fg_color=CARTAO_2,
                    button_color=AZUL, button_hover_color=AZUL_HOVER, dropdown_fg_color=CARTAO,
                    dropdown_hover_color=CARTAO_2, dropdown_text_color=TEXTO, text_color=TEXTO,
                    font=F(13), dropdown_font=F(13),
                    variable=tk.StringVar(value="Botão do mouse…"),
                    command=lambda nome: self._escolher_mouse(k, nome)).pack(side="left", padx=(8, 0))
                self.botoes_tecla[k] = tecla
                return f
            self._linha(c, titulo, desc, construir)
        ctk.CTkLabel(c, text="Clique em Alterar e aperte uma tecla, uma combinação (por exemplo "
                             "Alt + F10) ou os botões 2 a 5 do mouse. Para usar qualquer botão do "
                             "mouse, de 1 a 5, escolha na lista ao lado. Esc cancela. Alguns jogos com anti-cheat bloqueiam "
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
        if chave == "previa" and self.foto is None:
            self.tela_previa.configure(text=self._texto_previa())
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
    def _atalho_teclado(self, tecla, evento):
        try:
            keyboard.add_hotkey(tecla, lambda e=evento: self.eventos.put((e,)))
        except Exception:
            self.eventos.put(("erro", f"O atalho {fmt_tecla(tecla)} não é válido."))

    def registrar_atalhos(self):
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        teclas = {"salvar": self.cfg.get("tecla_salvar"), "alternar": self.cfg.get("tecla_buffer")}
        pedidos, mouse_map = [], {}
        for evento, tecla in teclas.items():
            if not tecla:
                continue
            if tecla in BOTOES_MOUSE:
                mouse_map[tecla] = evento
                continue
            conv = converter_atalho(tecla) if NO_WINDOWS else None
            if conv:
                pedidos.append((evento,) + conv)
            else:
                self._atalho_teclado(tecla, evento)
        falhas = (self.entrada.aplicar(pedidos, mouse_map) if self.entrada
                  else [p[0] for p in pedidos])
        for evento in falhas:  # combinação já usada por outro programa: plano B
            self._atalho_teclado(teclas[evento], evento)

    def capturar_tecla(self, chave):
        self.captura_id += 1
        cid = self.captura_id
        self.captura_chave = chave
        self.captura_inicio = time.time()
        self.botoes_tecla[chave].configure(text="Aperte tecla ou mouse…", fg_color=AZUL,
                                           text_color=TEXTO)
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        if self.entrada:
            self.entrada.aplicar([], {}, capturando=True)

        def ler():
            try:
                t = keyboard.read_hotkey(suppress=False)
            except Exception:
                t = None
            self.eventos.put(("tecla", chave, t, cid))
        threading.Thread(target=ler, daemon=True).start()

    def _escolher_mouse(self, chave, nome):
        codigo = next((k for k, v in NOMES_MOUSE.items() if v == nome), None)
        if codigo:
            self.captura_id += 1
            self._receber_tecla(chave, codigo, self.captura_id)

    def _receber_tecla(self, chave, tecla, cid=None):
        if cid is not None and cid != self.captura_id:
            return  # resposta antiga de uma captura que já terminou
        self.captura_id += 1
        self.captura_chave = None
        outra = "tecla_buffer" if chave == "tecla_salvar" else "tecla_salvar"
        if tecla and tecla != "esc":
            if tecla == self.cfg[outra]:
                self.toast("Esse atalho já está em uso no outro campo.", "erro")
            else:
                self.cfg[chave] = tecla
                salvar_config(self.cfg)
                aviso = (" Atenção: cada clique esquerdo vai salvar um replay."
                         if tecla == "mouse1" else "")
                self.toast(f"Atalho alterado para {fmt_tecla(tecla)}.{aviso}", "sucesso")
        self.botoes_tecla[chave].configure(text=fmt_tecla(self.cfg[chave]),
                                           fg_color=CARTAO_2, text_color=CIANO)
        self.registrar_atalhos()
        self.atualizar_resumo()

    # ------------------------------------------------------------ detecção
    def _detectar(self):
        self.eventos.put(("gfxcapture", tem_gfxcapture(self.ffmpeg)))
        self.eventos.put(("encoders", detectar_encoders(self.ffmpeg)))
        self.eventos.put(("dispositivos", listar_audio()))
        self.eventos.put(("pronto",))

    def recarregar_dispositivos(self):
        if self.ffmpeg:
            threading.Thread(target=lambda: self.eventos.put(
                ("dispositivos", listar_audio())), daemon=True).start()
            self.toast("Procurando dispositivos de áudio…")

    def _receber_encoders(self, lista):
        self.encoders_ok = lista
        nomes = ["Automático"] + [NOME_ENCODER[e] for e in lista if e in NOME_ENCODER]
        self.menu_encoder.configure(values=nomes)
        if self.cfg["encoder"] not in nomes:
            self.vars["encoder"].set("Automático")
        self.atualizar_resumo()

    def _receber_dispositivos(self, listas):
        saidas, entradas, erro = listas
        self.menu_sistema.configure(values=[PADRAO_AUDIO] + saidas)
        self.menu_mic.configure(values=[PADRAO_AUDIO] + entradas)
        if self.cfg["sistema_disp"] not in saidas:
            self.vars["sistema_disp"].set(PADRAO_AUDIO)
        if self.cfg["mic_disp"] not in entradas:
            self.vars["mic_disp"].set(PADRAO_AUDIO)
        if erro:
            self.lbl_audio.configure(text=f"Não foi possível ler os aparelhos de som: {erro}",
                                     text_color=VERMELHO)
        else:
            self.lbl_audio.configure(text=f"Encontrados: {len(saidas)} saídas de som e "
                                          f"{len(entradas)} microfones.", text_color=AZUL_CLARO)

    def encoder_escolhido(self):
        escolhido = ENCODERS.get(self.cfg["encoder"])
        disponiveis = self.encoders_ok or []
        if self.encoder_falhou:
            escolhido = None
        if escolhido and (self.encoders_ok is None or escolhido in disponiveis):
            return escolhido
        if self.encoder_falhou:
            return "libx264"
        for e in ("h264_nvenc", "h264_amf", "h264_qsv"):
            if e in disponiveis:
                return e
        return "libx264"

    # ------------------------------------------------------------ buffer
    def alternar_buffer(self):
        if self.parando:
            return
        if self.armado:
            self.desligar()
        else:
            self.ligar()

    def ligar(self):
        if not self.gravador:
            messagebox.showerror(APP_NOME, "O FFmpeg não foi encontrado. Reinstale o app.")
            return
        self.armado = True
        self.falhas = []
        self.alvo_hwnd = None
        self.encoder_falhou = False
        self.sem_gpu_direto = False
        self._verificar_captura()
        if self.armado and not self.gravador.ativo:
            self.toast("Buffer ligado. Abra o jogo e o Z1UL começa a gravar sozinho.")
        self._atualizar_estado()

    def desligar(self):
        self.armado = False
        if self.gravador and self.gravador.ativo:
            self.parar_captura()
        else:
            self._atualizar_estado()
            self.toast("Buffer desligado.")

    def reiniciar(self):
        self.aviso.grid_forget()
        if self.gravador and self.gravador.ativo:
            self.parar_captura()          # a vigia liga de novo com as novas configurações
        elif not self.armado:
            self.ligar()

    def _motor(self):
        metodo = self.cfg["metodo"]
        if metodo == "Compatível" or not self.usar_wgc:
            return "dxgi"
        return "wgc"

    def _regiao_desejada(self):
        info = achar_jogo(self.alvo_hwnd)
        self.alvo_hwnd = info["hwnd"] if info else None
        if not info:
            return None
        if info["minimizada"]:
            # minimizado: continua a mesma gravação
            atual = self.regiao_atual
            if self.gravador.ativo and atual and atual.get("hwnd") == info["hwnd"]:
                return atual
            return None
        if self._motor() == "wgc":
            return {"tipo": "janela", "hwnd": info["hwnd"], "w": info["w"], "h": info["h"],
                    "titulo": info["titulo"] or info["exe"]}
        regiao = calcular_regiao(info)
        if regiao:
            regiao["tipo"] = "regiao"
        return regiao

    def _vigia(self):
        try:
            self._verificar_captura()
        except Exception as e:
            registrar_log(f"Erro na vigia: {e!r}")
        self.after(1500, self._vigia)

    def _verificar_captura(self):
        if not self.armado or self.parando or not self.gravador:
            return
        desejada = self._regiao_desejada()
        if desejada is None:
            if self.gravador.ativo:
                self.parar_captura()      # o jogo foi fechado
            else:
                self._atualizar_estado()
            return
        if not self.gravador.ativo:
            self._iniciar_captura(desejada)
            return
        if chave_regiao(desejada) != chave_regiao(self.regiao_atual):
            # a janela mudou de tamanho ou lugar: confirma duas vezes antes de reiniciar
            if chave_regiao(desejada) == self.candidato:
                self.cont_candidato += 1
            else:
                self.candidato, self.cont_candidato = chave_regiao(desejada), 1
            if self.cont_candidato >= 2:
                self.candidato = None
                self.parar_captura()
        else:
            self.candidato = None

    def _iniciar_captura(self, regiao):
        self.avisou_audio = False
        encoder = self.encoder_escolhido()
        self.direto_atual = (not self.sem_gpu_direto
                             and usar_gpu_direto(self.cfg, regiao, encoder))
        try:
            self.gravador.iniciar(self.cfg, encoder, regiao, self.direto_atual)
        except Exception as e:
            self.armado = False
            self._atualizar_estado()
            messagebox.showerror(APP_NOME, f"Não foi possível ligar o buffer.\n\n{e}")
            return
        self.regiao_atual = regiao
        self.aviso.grid_forget()
        self._atualizar_estado()
        self.toast(f"Gravando {regiao['titulo'][:40]}. Aperte "
                   f"{fmt_tecla(self.cfg['tecla_salvar'])} para salvar.", "sucesso")

    def parar_captura(self):
        if not (self.gravador and self.gravador.ativo):
            return
        self.parando = True
        self._atualizar_estado()

        def tarefa():
            self.gravador.parar()
            self.eventos.put(("parado",))
        threading.Thread(target=tarefa, daemon=True).start()

    def _atualizar_previa(self):
        g = self.gravador
        try:
            visivel = self.state() not in ("iconic", "withdrawn")
            if (visivel and self.cfg["previa"] and g and g.ativo and g.quadro is not None
                    and g.quadro_n != self.ultimo_quadro):
                self.ultimo_quadro = g.quadro_n
                cabecalho = f"P6 {PREV_W} {PREV_H} 255\n".encode()
                self.foto = tk.PhotoImage(data=cabecalho + g.quadro, format="ppm")
                self.tela_previa.configure(image=self.foto, text="")
            elif (not (g and g.ativo) or not self.cfg["previa"]) and self.foto is not None:
                self.foto = None
                self.tela_previa.configure(image="", text=self._texto_previa())
        except Exception as e:
            registrar_log(f"Prévia: {e!r}")
            self.foto = None
            self.tela_previa.configure(image="", text="A prévia não pôde ser exibida neste PC.\n"
                                                      "A gravação continua normalmente.")
        self.after(100, self._atualizar_previa)

    def _texto_previa(self):
        if not self.cfg["previa"]:
            return "Prévia desligada (aba Vídeo)."
        if self.armado:
            if self.cfg["modo_captura"] == "Janela":
                return "Aguardando a janela escolhida…"
            return "Aguardando um jogo…\nAbra o FiveM, o emulador do Free Fire\nou outro jogo."
        return "Ligue o buffer para ver a prévia."

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
                registrar_log(f"Erro ao salvar: {e}")
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
            self.btn_buffer.configure(text="Aguarde…", state="disabled")
            return
        if self.armado:
            self.btn_buffer.configure(text="■   Desligar buffer", state="normal",
                                      fg_color="#240A14", hover_color="#36101E",
                                      border_width=1, border_color=VERMELHO, text_color="#FFD3DC")
        else:
            self.btn_buffer.configure(text="▶   Ligar buffer", state="normal", fg_color=AZUL,
                                      hover_color=AZUL_HOVER, border_width=0, text_color=TEXTO)
        if ativo:
            r = self.regiao_atual or {}
            self.lbl_status.configure(text="Gravando no buffer")
            self.lbl_mini.configure(text="●  Gravando", text_color=VERMELHO)
            tamanho = f"{r.get('w')}x{r.get('h')}" if r.get("w") else "monitor inteiro"
            saida = self.cfg["resolucao"] if RESOLUCOES.get(self.cfg["resolucao"]) else tamanho
            self.lbl_captura.configure(
                text=f"Capturando {str(r.get('titulo', ''))[:45]} ({tamanho}). "
                     f"Saída {saida}, {self.cfg['fps']} FPS.")
        elif self.armado:
            self.lbl_status.configure(text="Aguardando um jogo")
            self.lbl_ponto.configure(text_color="#FFB020")
            self.lbl_tempo.configure(text="Assim que um jogo abrir, a gravação começa sozinha.")
            self.lbl_mini.configure(text="●  Aguardando jogo", text_color="#FFB020")
            self.lbl_captura.configure(text="")
        else:
            self.lbl_status.configure(text="Buffer desligado")
            self.lbl_ponto.configure(text_color=APAGADO)
            self.lbl_tempo.configure(text="Ligue o buffer para começar a guardar os últimos "
                                          "segundos do jogo.")
            self.lbl_mini.configure(text="●  Buffer desligado", text_color=APAGADO)
            self.lbl_captura.configure(text="")
        if self.foto is None:
            self.tela_previa.configure(image="", text=self._texto_previa())
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
                    self.regiao_atual = None
                    self._atualizar_estado()
                    if self.armado:
                        self.after(200, self._verificar_captura)
                    else:
                        self.toast("Buffer desligado.")
                elif nome == "caiu":
                    anterior = self.regiao_atual or {}
                    self.regiao_atual = None
                    if anterior.get("hwnd") and not janela_existe(anterior["hwnd"]):
                        self.alvo_hwnd = None
                        self._atualizar_estado()
                        self.toast("O jogo foi fechado. Aguardando o próximo.")
                        continue
                    if self.direto_atual and not self.sem_gpu_direto:
                        # a placa não aceitou o modo direto: tenta pelo caminho tradicional
                        self.sem_gpu_direto = True
                        registrar_log("Modo direto da placa falhou; usando o caminho normal.")
                        self._atualizar_estado()
                        continue
                    if (self.gravador.encoder or "libx264") != "libx264" and not self.encoder_falhou:
                        # o encoder da placa falhou: tenta pelo processador
                        self.encoder_falhou = True
                        registrar_log(f"Encoder {self.gravador.encoder} falhou; usando x264.")
                        self.toast("Ajustando o encoder para o seu PC…")
                        self._atualizar_estado()
                        continue
                    agora = time.time()
                    self.falhas = [t for t in self.falhas if agora - t < 60] + [agora]
                    if self.armado and len(self.falhas) < 3:
                        self.toast("A captura parou. Tentando de novo…", "erro")
                        self._atualizar_estado()
                        continue
                    self.armado = False
                    self._atualizar_estado()
                    messagebox.showerror(
                        APP_NOME, "A gravação parou sozinha. Detalhes:\n\n" + ev[1][-600:]
                        + f"\n\nRegistro completo em: {ARQ_LOG}"
                        + "\n\nDica: tente outro encoder na aba Vídeo ou confira o "
                          "dispositivo de áudio.")
                elif nome == "encoders":
                    self._receber_encoders(ev[1])
                elif nome == "gfxcapture":
                    self.usar_wgc = ev[1]
                elif nome == "aviso_audio":
                    if not self.avisou_audio:
                        self.avisou_audio = True
                        self.toast(ev[1], "erro")
                elif nome == "dispositivos":
                    self._receber_dispositivos(ev[1])
                elif nome == "tecla":
                    self._receber_tecla(ev[1], ev[2], ev[3] if len(ev) > 3 else None)
                elif nome == "tecla_mouse":
                    if self.captura_chave and time.time() - self.captura_inicio > 0.4:
                        self._receber_tecla(self.captura_chave, ev[1], self.captura_id)
                elif nome == "pronto" and self.cfg["iniciar_auto"]:
                    self.ligar()
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
    if NO_WINDOWS:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
    App().mainloop()
