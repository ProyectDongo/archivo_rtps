"""
Helpers de threading de correos (estilo Gmail).

Threading robusto basado en headers MIME `In-Reply-To` y `References`, con
fallback a heurística de asunto normalizado para correos sin headers.

Flujo de asignación de `Correo.thread`:
1. Parsear headers (`parse_threading_headers`).
2. Buscar correo padre por mensaje_id → heredar su thread.
3. Si no hay padre, buscar por asunto normalizado + buzon → reusar thread.
4. Si nada matchea, crear Thread nuevo.

Mantenimiento del cache:
- `recompute_thread_cache(thread)`: recalcula count/fechas/asunto. Llamar
  después de agregar/quitar correos.
- `attach_correo_to_thread(correo, msg=None)`: hace todo el flujo de
  asignación en una sola llamada.
"""
from __future__ import annotations

import re
from typing import Optional

from .models import Correo, Thread


# Re: / Fwd: / RV: / Fw: repetidos al inicio del asunto.
_RE_ASUNTO_PREFIJO = re.compile(r'^\s*(re|fwd?|rv|fw)\s*:\s*', re.IGNORECASE)

# Extrae mensaje_ids de un header References. Formato típico:
#   References: <id1@dominio> <id2@dominio> <id3@dominio>
# Pero hay clientes que ponen comas, espacios raros, comillas. Esta regex
# captura cualquier `<...>` no vacío.
_RE_MSGID_EN_HEADER = re.compile(r'<([^<>]+)>')


def normalizar_asunto(asunto: str) -> str:
    """
    Quita prefijos Re:/Fwd:/RV:/Fw: repetidos del inicio. Lowercase + trim.

    Ejemplos:
      'Re: Re: Fwd: Hola' → 'hola'
      'RV: Saludos'       → 'saludos'
      'Hola'              → 'hola'
    """
    s = (asunto or '').strip()
    while True:
        m = _RE_ASUNTO_PREFIJO.match(s)
        if not m:
            break
        s = s[m.end():].strip()
    return s.lower()


def parse_threading_headers(msg) -> tuple[str, str]:
    """
    Devuelve (in_reply_to, references) limpios y truncados.

    `msg` es un `email.message.Message` (ya parseado de bytes).

    - in_reply_to: el `<id>` del header (sin angle brackets). Si hay
      múltiples, se queda con el primero. Truncado a 500 chars.
    - references: string con TODOS los `<id1> <id2> ...` separados por
      espacio (sin angle brackets). Truncado a 5000 chars.
    """
    irt_raw = (msg.get('In-Reply-To', '') or '').strip()
    irt = ''
    m = _RE_MSGID_EN_HEADER.search(irt_raw)
    if m:
        irt = m.group(1).strip()
    elif irt_raw and not irt_raw.startswith('<'):
        # Algunos clientes mandan el id pelado sin <>. Aceptamos si es razonable.
        irt = irt_raw.strip().split()[0] if irt_raw.strip() else ''

    refs_raw = (msg.get('References', '') or '').strip()
    refs_ids = _RE_MSGID_EN_HEADER.findall(refs_raw)
    refs = ' '.join(refs_ids)

    return irt[:500], refs[:5000]


def find_parent_thread(buzon, in_reply_to: str, references: str,
                       asunto: str) -> Optional[Thread]:
    """
    Encuentra el Thread al que pertenece un correo entrante, en este orden:

    1. **Por headers**: si `in_reply_to` o el último `references` matchean
       un Correo existente, devuelve su thread.
    2. **Por asunto normalizado**: si hay un Thread previo con el mismo
       asunto normalizado en el mismo buzón, devuelve ese.

    Retorna None si no hay match — el caller debe crear un Thread nuevo.
    """
    # Lista de mensaje_ids candidatos. El último de References suele ser
    # el padre directo, pero matcheamos cualquiera (a veces el padre directo
    # se perdió y solo está un ancestro).
    candidatos: list[str] = []
    if in_reply_to:
        candidatos.append(in_reply_to)
    if references:
        # Tomar de atrás para adelante: el ÚLTIMO referenced es el más cercano.
        for r in reversed(references.split()):
            if r and r not in candidatos:
                candidatos.append(r)

    if candidatos:
        padre = (Correo.objects
                 .filter(buzon=buzon, mensaje_id__in=candidatos,
                         thread__isnull=False)
                 .only('thread_id')
                 .first())
        if padre and padre.thread_id:
            return Thread.objects.filter(id=padre.thread_id).first()

    # Fallback: matchear por asunto normalizado en el mismo buzón.
    norm = normalizar_asunto(asunto)
    if norm and len(norm) >= 4:
        return (Thread.objects
                .filter(buzon=buzon, asunto__iexact=norm)
                .order_by('-fecha_ultimo')
                .first())

    return None


def create_thread_for(correo: Correo) -> Thread:
    """
    Crea un Thread nuevo con `correo` como raíz. Setea cache inicial.
    """
    return Thread.objects.create(
        buzon=correo.buzon,
        asunto=normalizar_asunto(correo.asunto),
        mensaje_id_raiz=correo.mensaje_id,
        fecha_primero=correo.fecha,
        fecha_ultimo=correo.fecha,
        count=1,
    )


def attach_correo_to_thread(correo: Correo, msg=None) -> Thread:
    """
    Pipeline completo: asigna `correo.thread` y guarda. Si `msg` viene
    (email.message.Message), parsea headers de ahí; si no, usa los headers
    ya guardados en `correo.in_reply_to` / `correo.references`.

    Devuelve el Thread asignado.

    NO actualiza el cache del thread (count/fecha_ultimo) — para eso llamar
    `recompute_thread_cache(thread)` o usar el backfill en bulk.
    """
    if msg is not None:
        irt, refs = parse_threading_headers(msg)
        # Persistimos los headers para que el backfill futuro pueda recompletar.
        if not correo.in_reply_to:
            correo.in_reply_to = irt
        if not correo.references:
            correo.references = refs

    parent = find_parent_thread(
        correo.buzon, correo.in_reply_to, correo.references, correo.asunto,
    )
    if parent is None:
        parent = create_thread_for(correo)
        # Si ya estaba la fila persistida, evitamos un UPDATE redundante.
        if correo.pk and correo.thread_id != parent.id:
            correo.thread = parent
            correo.save(update_fields=['thread', 'in_reply_to', 'references'])
        else:
            correo.thread = parent
        return parent

    correo.thread = parent
    if correo.pk:
        correo.save(update_fields=['thread', 'in_reply_to', 'references'])
    return parent


def recompute_thread_cache(thread: Thread) -> None:
    """
    Recalcula count/fecha_primero/fecha_ultimo/asunto/mensaje_id_raiz del
    Thread a partir de los Correos asignados. Llamar después de operaciones
    en bulk (backfill, importer).
    """
    correos = thread.correos.order_by('fecha').only(
        'id', 'mensaje_id', 'asunto', 'fecha'
    )
    correos_lista = list(correos)
    if not correos_lista:
        # Thread vacío — no lo borramos automáticamente (el caller decide).
        thread.count = 0
        thread.fecha_primero = None
        thread.fecha_ultimo = None
        thread.save(update_fields=['count', 'fecha_primero', 'fecha_ultimo'])
        return

    raiz = correos_lista[0]
    ultimo = correos_lista[-1]
    thread.count = len(correos_lista)
    thread.fecha_primero = raiz.fecha
    thread.fecha_ultimo = ultimo.fecha
    thread.asunto = normalizar_asunto(raiz.asunto)
    thread.mensaje_id_raiz = raiz.mensaje_id or thread.mensaje_id_raiz
    thread.save(update_fields=[
        'count', 'fecha_primero', 'fecha_ultimo', 'asunto', 'mensaje_id_raiz',
    ])
