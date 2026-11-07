"""Importa un archivo de memoria (Open Interpreter u otro) a la memoria de Jarvis.

Uso: arrastra el archivo encima de importar_memoria.bat
     o: python importar_memoria.py ruta\\al\\archivo

- Las líneas con contraseñas, tokens o claves van a la bóveda (cifradas), no a la memoria.
- El resto se añade a datos/memoria.md, que puedes revisar y editar después.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import boveda
import memoria

SECRETO = re.compile(
    r"^\s*[-*•]?\s*(?P<nombre>[^:=\n]{2,60}?(?:password|contraseña|clave|pass|pwd|token|api[ _-]?key|"
    r"secret|secreto|apikey|key)[^:=\n]{0,40}?)\s*[:=]\s*(?P<valor>\S{4,})\s*$", re.I)
PARECE_CLAVE = re.compile(r"^(sk-|ghp_|github_pat_|xox[bp]-|AKIA|AIza|hf_|eyJ)|^[A-Za-z0-9_\-]{24,}$")


def _texto_de(ruta):
    raw = Path(ruta).read_text(encoding="utf-8", errors="ignore")
    suf = Path(ruta).suffix.lower()
    if suf in (".yaml", ".yml"):
        try:
            import yaml
            datos = yaml.safe_load(raw)
            partes = []
            for k in ("custom_instructions", "system_message", "memory", "memoria", "instructions"):
                v = datos.get(k) if isinstance(datos, dict) else None
                if v:
                    partes.append(str(v))
            if partes:
                return "\n".join(partes)
        except Exception:
            pass
    if suf == ".json":
        try:
            datos = json.loads(raw)
            if isinstance(datos, list):  # conversación de Open Interpreter
                return "\n".join(m.get("content", "") for m in datos
                                 if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str))
            return json.dumps(datos, ensure_ascii=False, indent=1)
        except Exception:
            pass
    return raw


def importar(ruta):
    texto = _texto_de(ruta)
    puede_guardar = boveda.desbloqueada()
    if not puede_guardar:
        print("AVISO: la bóveda no existe o está bloqueada en este equipo; las claves que encuentre NO se importarán.\n"
              "Ábrela antes desde Jarvis (clic derecho > Bóveda de claves) y vuelve a importar.\n")
    secretos, datos = [], []
    for linea in texto.splitlines():
        limpia = linea.strip()
        if not limpia or limpia.startswith(("```", "---")):
            continue
        m = SECRETO.match(limpia)
        if m and (PARECE_CLAVE.search(m.group("valor")) or "pass" in m.group("nombre").lower()
                  or "contrase" in m.group("nombre").lower() or "clave" in m.group("nombre").lower()):
            nombre = m.group("nombre").strip(" -*•").strip()
            tipo = "token" if re.search(r"token|key|api", nombre, re.I) else "contraseña"
            if puede_guardar:
                boveda.guardar(nombre, m.group("valor"), tipo, "Importado")
                secretos.append(nombre)
                datos.append(f"Tiene guardada en la bóveda la clave «{nombre}»")
            continue  # nunca a la memoria en claro
        limpia = limpia.lstrip("-*•# ").strip()
        if len(limpia) > 3:
            datos.append(limpia)

    nuevos = sum(memoria.recordar(d) for d in datos)
    print(f"\nImportado de {Path(ruta).name} ({datetime.now():%d/%m/%Y %H:%M}):")
    print(f"  {nuevos} datos nuevos añadidos a la memoria ({memoria.ARCHIVO})")
    print(f"  {len(secretos)} claves movidas a la bóveda: {', '.join(secretos) or '-'}")
    print("\nRevisa datos/memoria.md por si hay algo que sobre (clic derecho en Jarvis > Editar memoria).")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
    else:
        for r in sys.argv[1:]:
            importar(r)
