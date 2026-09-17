"""
Genera el PDF de guia de usuario del Production Calculator (en español).
Ejecutar desde la raiz del proyecto:
    python tools/generate_user_guide.py
Salida: docs/Guia_Usuario_Production_Calculator.pdf
"""

import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)

# ── Paleta de colores (igual que el tema oscuro de la app) ────────────────────
BLUE       = colors.HexColor("#2d89ef")
DARK_BG    = colors.HexColor("#1e1e1e")
ACCENT     = colors.HexColor("#4aa3ff")
GREEN      = colors.HexColor("#4CAF50")
ORANGE     = colors.HexColor("#FF9800")
GREY       = colors.HexColor("#888888")
WHITE      = colors.white
RED        = colors.HexColor("#F44336")

W, H = A4  # 595 x 842 pts

# ── Estilos ────────────────────────────────────────────────────────────────────

def S(name, **kw):
    return ParagraphStyle(name, **kw)

h1_style = S("H1",
    fontName="Helvetica-Bold", fontSize=16, textColor=ACCENT,
    spaceBefore=14, spaceAfter=6)

h2_style = S("H2",
    fontName="Helvetica-Bold", fontSize=12, textColor=BLUE,
    spaceBefore=10, spaceAfter=4)

body_style = S("Body",
    fontName="Helvetica", fontSize=10, textColor=colors.black,
    leading=15, spaceAfter=4, alignment=TA_JUSTIFY)

bullet_style = S("Bullet",
    fontName="Helvetica", fontSize=10, textColor=colors.black,
    leading=14, spaceAfter=2, leftIndent=14, bulletIndent=4)

note_style = S("Note",
    fontName="Helvetica-Oblique", fontSize=9, textColor=GREY,
    leading=13, spaceAfter=3, leftIndent=10)

caption_style = S("Caption",
    fontName="Helvetica-Bold", fontSize=10, textColor=WHITE,
    leading=14)

# ── Constructores ──────────────────────────────────────────────────────────────

def hr(col=BLUE, thickness=0.8):
    return HRFlowable(width="100%", thickness=thickness, color=col, spaceAfter=4)

def sp(h=6):
    return Spacer(1, h)

def h1(text):
    return [sp(4), hr(ACCENT, 1.5), Paragraph(text, h1_style), hr(ACCENT, 0.4), sp(2)]

def h2(text):
    return [sp(2), Paragraph(text, h2_style)]

def p(text):
    return Paragraph(text, body_style)

def note(text):
    return Paragraph(f"<i>Nota: {text}</i>", note_style)

def bullets(items):
    return [Paragraph(f"• {item}", bullet_style) for item in items]

def field_table(rows, col_widths=(5*cm, 11.5*cm)):
    style = TableStyle([
        ("BACKGROUND",      (0,0), (-1,0), BLUE),
        ("TEXTCOLOR",       (0,0), (-1,0), WHITE),
        ("FONTNAME",        (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",        (0,0), (-1,0), 10),
        ("ALIGN",           (0,0), (-1,0), "CENTER"),
        ("FONTNAME",        (0,1), (0,-1), "Helvetica-Bold"),
        ("FONTSIZE",        (0,1), (-1,-1), 9),
        ("ALIGN",           (0,1), (0,-1), "LEFT"),
        ("VALIGN",          (0,0), (-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS",  (0,1), (-1,-1), [colors.white, colors.HexColor("#f0f4ff")]),
        ("GRID",            (0,0), (-1,-1), 0.4, colors.HexColor("#cccccc")),
        ("TOPPADDING",      (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",   (0,0), (-1,-1), 5),
        ("LEFTPADDING",     (0,0), (-1,-1), 8),
    ])
    tbl_rows = []
    for r in rows:
        tbl_rows.append([
            Paragraph(str(r[0]),
                      caption_style if not tbl_rows
                      else S("FH", fontName="Helvetica-Bold", fontSize=9, textColor=colors.black)),
            Paragraph(str(r[1]),
                      S("FB", fontName="Helvetica", fontSize=9,
                        textColor=colors.black, leading=13)),
        ])
    t = Table(tbl_rows, colWidths=col_widths)
    t.setStyle(style)
    return t

def info_box(title, items, bg=colors.HexColor("#e8f0fe"), border=BLUE):
    content = f"<b>{title}</b><br/>" + "<br/>".join(f"• {i}" for i in items)
    style = TableStyle([
        ("BACKGROUND",      (0,0), (-1,-1), bg),
        ("LEFTPADDING",     (0,0), (-1,-1), 10),
        ("RIGHTPADDING",    (0,0), (-1,-1), 10),
        ("TOPPADDING",      (0,0), (-1,-1), 8),
        ("BOTTOMPADDING",   (0,0), (-1,-1), 8),
        ("BOX",             (0,0), (-1,-1), 1.2, border),
    ])
    t = Table([[Paragraph(content,
                          S("IB", fontName="Helvetica", fontSize=9,
                            textColor=colors.black, leading=14))]],
              colWidths=[16.5*cm])
    t.setStyle(style)
    return t


# ── Encabezado del documento (sustituye a la portada) ─────────────────────────

def doc_header():
    title_s = S("DocTitle",
        fontName="Helvetica-Bold", fontSize=22, textColor=ACCENT,
        alignment=TA_CENTER, spaceAfter=4)
    sub_s = S("DocSub",
        fontName="Helvetica", fontSize=11, textColor=GREY,
        alignment=TA_CENTER, spaceAfter=2)
    return [
        Paragraph("Calculadora de Produccion — Guia de Usuario", title_s),
        Paragraph("Production Performance Calculator · v1.1 · Febrero 2026", sub_s),
        hr(ACCENT, 1.5),
        sp(4),
    ]


# ── Secciones ──────────────────────────────────────────────────────────────────

def section_overview():
    story = []
    story += h1("1. Descripcion general")
    story.append(p(
        "La <b>Calculadora de Produccion</b> es una aplicacion de escritorio que permite a cada "
        "designer registrar los casos trabajados durante el dia, calcular eficiencia y unidades "
        "equivalentes (UE), y revisar el historial personal de produccion. Todo se guarda en una "
        "base de datos local sincronizada a traves de OneDrive."
    ))
    story.append(sp(6))
    story.append(info_box("Que calcula la app por ti", [
        "Case Value % — que fraccion de la jornada estandar representa un caso",
        "Efficiency % — que tan rapido trabajaste vs. el tiempo estandar del tipo de caso",
        "Unidades Equivalentes (UE) — metrica de produccion normalizada para todas las regiones",
        "Totales diarios y por periodo, graficas y linea de tendencia en el Dashboard",
    ]))
    story.append(sp(8))
    story += h2("Navegacion")
    story.append(p(
        "La app tiene <b>6 pestanas</b> en la parte superior. Haz clic en cualquiera para cambiar. "
        "Las pestanas son: <b>Register · OT · Production · History · Standards · Dashboard</b>."
    ))
    story.append(sp(6))
    story += h2("Controles de la barra de estado (parte inferior)")
    story.append(p("Tres controles pequenos en la esquina inferior derecha:"))
    tbl = [
        ["Control",     "Funcion"],
        ["Light",       "Alterna entre modo oscuro (por defecto) y modo claro"],
        ["A+  /  A-",   "Aumenta o reduce el tamano de la fuente"],
    ]
    story.append(sp(4))
    story.append(field_table(tbl))
    return story


def section_register():
    story = []
    story += h1("2. Pestana Register — Casos del dia")
    story.append(p(
        "Usa esta pestana cada vez que termines de trabajar un <b>caso en horario regular</b>. "
        "Rellena el formulario y pulsa <b>Calculate</b> y luego <b>Save Case</b>."
    ))
    story.append(sp(6))

    story += h2("Paso a paso")
    story += bullets([
        "<b>Start time</b> — pon la hora en que abriste el caso. "
        "Se rellena automaticamente con la hora actual al cargar la pestana.",
        "<b>Al pulsar Calculate, la hora End se establece automaticamente</b> a la hora actual.",
        "Rellena <b>Case ID</b>, <b>Region</b> y <b>Type</b> (obligatorios).",
        "<b>Doctor</b> y <b>Comments</b> son opcionales.",
        "<b>Count Production</b> normalmente es 1. Si una sola tarea cuenta como varias "
        "unidades de produccion, pon el numero correcto.",
        "Pulsa <b>Calculate</b> — el panel de resultados muestra Efficiency %, "
        "Case Value % y UE.",
        "Pulsa <b>Save Case</b> para guardar. La barra de progreso se actualiza al momento.",
    ])
    story.append(sp(8))

    story += h2("Referencia de campos del formulario")
    tbl = [
        ["Campo",               "Descripcion"],
        ["Case ID",             "Identificador unico del caso (ej. CLEX-12345). No es obligatorio, pero muy recomendable."],
        ["Region",              "Selecciona la region propietaria del caso. Determina que tiempo estandar se usa."],
        ["Type",                "Tipo de caso dentro de esa region (ej. Primary, Stage RX CR). Se actualiza automaticamente al cambiar Region."],
        ["Doctor",              "Nombre del doctor tratante — opcional, solo para trazabilidad."],
        ["Date",                "Fecha en que se trabajo el caso. Por defecto es hoy."],
        ["Start",               "Hora en que empezaste a trabajar el caso."],
        ["End",                 "Hora en que terminaste. Se establece automaticamente al pulsar Calculate."],
        ["Count Production",    "Multiplicador (por defecto 1). Usa >= 2 si este registro cuenta como varias unidades."],
        ["Comments",            "Notas de texto libre. No afectan al calculo."],
    ]
    story.append(sp(4))
    story.append(field_table(tbl))
    story.append(sp(8))

    story += h2("Panel de resultados")
    tbl2 = [
        ["Metrica",         "Formula / Significado"],
        ["Efficiency %",    "(Std Time / Tiempo real) x 100  ->  >100% significa mas rapido que el estandar"],
        ["Case Value %",    "(Std Time / 408,3 min) x 100  ->  fraccion de una jornada completa"],
        ["Equiv. Units",    "Case Value % x Daily Rate / 100  ->  produccion normalizada"],
        ["Badge de estado", "OK (>=100%) · WARN (>=95%) · LOW (<95%)"],
    ]
    story.append(field_table(tbl2))
    story.append(sp(8))

    story += h2("Barra de progreso diario")
    story.append(p(
        "La barra debajo del panel de resultados se va llenando conforme aumenta el total "
        "de Case Value del dia. Representa el porcentaje de una jornada estandar completa "
        "que has producido. 100% = produccion equivalente a una jornada estandar entera."
    ))
    story.append(sp(6))

    story += h2("Importar desde portapapeles  (Ctrl + Shift + I)")
    story.append(p(
        "Si el caso esta abierto en tu navegador, puedes copiar la pagina completa "
        "(<b>Ctrl+A</b> y luego <b>Ctrl+C</b> en el navegador) y pulsar "
        "<b>Ctrl+Shift+I</b> dentro de la app estando en la pestana Register. "
        "La app rellenara automaticamente el Case ID, Region y Type desde el portapapeles."
    ))
    story.append(note(
        "Si la deteccion automatica no acierta, corrige los campos manualmente antes de guardar."
    ))
    return story


def section_ot():
    story = []
    story += h1("3. Pestana OT — Casos de horas extra")
    story.append(p(
        "La <b>pestana OT</b> funciona exactamente igual que Register, pero los casos se guardan "
        "por separado y se contabilizan como <b>produccion de horas extra</b>. Usala para "
        "cualquier caso trabajado fuera de tu horario regular."
    ))
    story += bullets([
        "Todos los campos son los mismos que en la pestana Register.",
        "Pulsa <b>Calculate</b> y luego <b>Save OT Case</b>.",
        "La importacion desde portapapeles (<b>Ctrl+Shift+I</b>) tambien funciona aqui.",
        "Los totales OT aparecen por separado en las pestanas Production y Dashboard.",
        "El <b>spinner OT Hours</b> (0,25–12 h) indica cuantas horas extra trabajaste ese dia, "
        "usado para calcular la tasa de produccion ajustada por OT.",
    ])
    story.append(sp(6))
    story.append(info_box("Diferencia clave con Register", [
        "Los casos guardados en OT van a la tabla ot_cases (separada de la tabla de casos regulares).",
        "La produccion OT se muestra en naranja en las graficas y etiquetada por separado en todos los resumenes.",
        "Para editar o eliminar un caso OT, ve a la pestana History (cambia el selector Reg/OT).",
    ], bg=colors.HexColor("#fff3e0"), border=ORANGE))
    return story


def section_production():
    story = []
    story += h1("4. Pestana Production")
    story.append(p(
        "La pestana Production muestra un <b>resumen de tus casos para cualquier fecha seleccionada</b>. "
        "Usala para revisar lo que produjiste en un dia concreto."
    ))
    story += bullets([
        "Elige una fecha con el selector de fecha en la parte superior.",
        "Cambia entre <b>Regular</b> y <b>OT</b> con los dos botones.",
        "La tabla lista cada caso: ID, Type, Efficiency %, Case Value %, UE, Estado.",
        "Los totales (casos, Case Value %, UE) aparecen al pie de la tabla.",
        "Haz clic en una fila y pulsa <b>Edit</b> o <b>Delete</b> para modificar un caso guardado.",
    ])
    story.append(sp(4))
    story.append(note(
        "Editar un caso te lleva a la pestana Register (u OT) con todos los campos ya rellenos. "
        "Haz los cambios y vuelve a pulsar Save Case para sobreescribir."
    ))
    return story


def section_history():
    story = []
    story += h1("5. Pestana History")
    story.append(p(
        "La pestana History ofrece un <b>registro completo de todos los casos</b> en todas las fechas. "
        "Es el lugar principal para buscar, revisar, editar o eliminar entradas pasadas."
    ))
    story += bullets([
        "Usa los selectores de <b>rango de fechas</b> (Start / End) para filtrar por periodo.",
        "Usa el <b>campo de busqueda</b> para filtrar por Case ID, Doctor, Region o Type.",
        "El <b>selector Reg / OT</b> cambia entre las dos tablas de casos.",
        "Haz clic en cualquier fila para seleccionarla y usa <b>Edit</b> o <b>Delete</b>.",
        "La barra inferior muestra el total de casos y el Case Value total de las filas visibles.",
    ])
    return story


def section_standards():
    story = []
    story += h1("6. Pestana Standards")
    story.append(p(
        "La pestana Standards permite <b>ver y editar los tiempos estandar y unidades equivalentes</b> "
        "de cada region y tipo de caso. Normalmente solo necesitaras usarla si se introducen "
        "nuevos tipos de caso o se revisan oficialmente los tiempos existentes."
    ))
    story += h2("Estructura del arbol")
    story += bullets([
        "<b>Filas de nivel superior</b> = Regiones (ej. ICON, Ukin, EMEA...)",
        "<b>Filas hijo</b> = Tipos de caso dentro de esa region (ej. Primary, Stage RX CR)",
        "Cada fila hijo muestra: Std Time (min) y Equiv. Units (tasa diaria)",
    ])
    story.append(sp(6))
    story += h2("Editar un valor")
    story += bullets([
        "<b>Doble clic</b> en cualquier fila de tipo de caso para abrir el dialogo de edicion.",
        "Actualiza <b>Std Time (min)</b> y/o <b>Equiv. Units</b>.",
        "Pulsa <b>OK</b> y luego <b>Save Changes</b> para guardar.",
        "Las pestanas Register y OT se actualizan automaticamente — sin necesidad de reiniciar.",
    ])
    story.append(sp(6))
    story += h2("Anadir / eliminar")
    story += bullets([
        "<b>Add Region</b> — crea una nueva region de nivel superior.",
        "<b>Add Type</b> — agrega un nuevo tipo de caso bajo la region seleccionada.",
        "<b>Delete</b> — elimina el elemento seleccionado (confirmar en el dialogo).",
        "<b>Import / Export JSON</b> — respalda o restaura el archivo completo de estandares.",
    ])
    story.append(sp(4))
    story.append(info_box("Precaucion", [
        "Cambiar un tiempo estandar modifica como se calcula el Case Value % en TODOS los casos mostrados.",
        "Solo modifica los tiempos estandar cuando tu team lead lo indique o cuando cambien los estandares oficiales.",
        "Usa Export JSON para hacer una copia de seguridad antes de hacer cambios masivos.",
    ], bg=colors.HexColor("#fff8e1"), border=RED))
    return story


def section_dashboard():
    story = []
    story += h1("7. Pestana Dashboard")
    story.append(p(
        "El Dashboard ofrece una <b>vista visual de tu produccion</b> a lo largo del tiempo. "
        "Todas las graficas se actualizan automaticamente tras cada caso guardado."
    ))
    story += h2("Selector de periodo")
    story.append(p(
        "Usa los selectores de fecha <b>Start / End</b> en la parte superior y pulsa <b>Refresh</b> "
        "para cambiar el periodo mostrado en todas las graficas."
    ))
    story.append(sp(6))
    story += h2("Graficas y metricas")
    tbl = [
        ["Elemento",                        "Que muestra"],
        ["Grafica de barras diaria",         "Tu Efficiency % por dia. Linea objetivo en el 100%."],
        ["Barras apiladas de UE diarias",    "Unidades equivalentes por dia, separadas en Regular (azul) y OT (naranja)."],
        ["Tarjetas de resumen del periodo",  "Total de casos, Case Value % total, UE total y Efficiency promedio del periodo."],
        ["Linea de tendencia",               "Media movil de 7 dias de eficiencia — ayuda a identificar patrones frente al ruido diario."],
    ]
    story.append(sp(4))
    story.append(field_table(tbl))
    return story


def section_concepts():
    story = []
    story += h1("8. Conceptos clave y formulas")

    story += h2("Tiempo estandar (Std Time)")
    story.append(p(
        "Cada tipo de caso tiene un tiempo estandar predefinido en minutos: "
        "el tiempo esperado para completar ese tipo de caso. "
        "Se definen en la pestana Standards."
    ))

    story += h2("Case Value %")
    story.append(p("Que fraccion de una jornada completa representa este caso:"))
    story.append(Paragraph(
        "Case Value % = (Std Time / 408,3) x 100",
        S("Formula", fontName="Courier-Bold", fontSize=11,
          textColor=BLUE, alignment=TA_CENTER, spaceBefore=4, spaceAfter=4)))
    story.append(p(
        "408,3 minutos es la base de una jornada de produccion completa. "
        "Un caso con Std Time = 40,83 min -> Case Value aprox. 10%."
    ))

    story += h2("Efficiency %")
    story.append(p("Que tan rapido trabajaste respecto al estandar:"))
    story.append(Paragraph(
        "Efficiency % = (Std Time / Tiempo real) x 100",
        S("Formula2", fontName="Courier-Bold", fontSize=11,
          textColor=GREEN, alignment=TA_CENTER, spaceBefore=4, spaceAfter=4)))
    story.append(p(
        "100% = exactamente en estandar. 120% = 20% mas rapido que el estandar. "
        "Por debajo del 95% se activa la advertencia amarilla/roja."
    ))

    story += h2("Unidades Equivalentes (UE)")
    story.append(p("Metrica de produccion normalizada usada en todas las regiones:"))
    story.append(Paragraph(
        "UE = Case Value % x Daily Rate / 100",
        S("Formula3", fontName="Courier-Bold", fontSize=11,
          textColor=ORANGE, alignment=TA_CENTER, spaceBefore=4, spaceAfter=4)))
    story.append(p(
        "Daily Rate es el valor UE definido para cada region/tipo en la pestana Standards "
        "(columna 'Equiv. Units'). "
        "Ejemplo: Case Value 9,8% x Tasa ICON 13,0 / 100 aprox. 1,27 UE."
    ))

    story += h2("Badges de estado")
    tbl = [
        ["Badge",       "Condicion",            "Significado"],
        ["OK",          "Efficiency >= 100%",    "En estandar o por encima"],
        ["WARN",        "95% <= Eff < 100%",     "Ligeramente por debajo — aceptable"],
        ["LOW",         "Efficiency < 95%",      "Por debajo del estandar — revisar tiempos o tecnica"],
    ]
    style = TableStyle([
        ("BACKGROUND",      (0,0), (-1,0), BLUE),
        ("TEXTCOLOR",       (0,0), (-1,0), WHITE),
        ("FONTNAME",        (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",        (0,0), (-1,-1), 9),
        ("ALIGN",           (0,0), (-1,-1), "LEFT"),
        ("VALIGN",          (0,0), (-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS",  (0,1), (-1,-1), [colors.white, colors.HexColor("#f0f4ff")]),
        ("GRID",            (0,0), (-1,-1), 0.4, colors.HexColor("#cccccc")),
        ("TOPPADDING",      (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",   (0,0), (-1,-1), 5),
        ("LEFTPADDING",     (0,0), (-1,-1), 8),
    ])
    rows = []
    for r in tbl:
        is_header = not rows
        rows.append([
            Paragraph(r[0], S("TC",  fontName="Helvetica-Bold", fontSize=9,
                               textColor=WHITE if is_header else colors.black)),
            Paragraph(r[1], S("TC2", fontName="Helvetica",      fontSize=9,
                               textColor=WHITE if is_header else colors.black)),
            Paragraph(r[2], S("TC3", fontName="Helvetica",      fontSize=9,
                               textColor=WHITE if is_header else colors.black)),
        ])
    t = Table(rows, colWidths=[3.5*cm, 5.5*cm, 7.5*cm])
    t.setStyle(style)
    story.append(sp(4))
    story.append(t)
    return story


def section_quick_ref():
    story = []
    story += h1("9. Referencia rapida")

    story += h2("Atajo de teclado")
    tbl = [
        ["Atajo",               "Accion"],
        ["Ctrl + Shift + I",    "Importar caso desde el portapapeles (en pestana Register u OT)"],
    ]
    story.append(sp(4))
    story.append(field_table(tbl, col_widths=(5*cm, 11.5*cm)))

    story.append(sp(8))
    story += h2("Flujo diario tipico")
    steps = [
        ("1", "Abre la app y queda en la pestana <b>Register</b>."),
        ("2", "Cuando empieces un caso, la hora Start ya esta en la hora actual."),
        ("3", "Al terminar el caso, pulsa <b>Calculate</b> — la hora End se captura automaticamente."),
        ("4", "Verifica los resultados y pulsa <b>Save Case</b>."),
        ("5", "Repite para cada caso durante el dia."),
        ("6", "Al final del dia revisa la pestana <b>Dashboard</b> para ver tus totales."),
    ]
    step_style = TableStyle([
        ("VALIGN",          (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING",     (0,0), (0,-1), 4),
        ("RIGHTPADDING",    (0,0), (0,-1), 6),
        ("TOPPADDING",      (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",   (0,0), (-1,-1), 4),
        ("FONTNAME",        (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTSIZE",        (0,0), (-1,-1), 10),
        ("TEXTCOLOR",       (0,0), (0,-1), BLUE),
        ("ROWBACKGROUNDS",  (0,0), (-1,-1), [colors.white, colors.HexColor("#f0f4ff")]),
        ("GRID",            (0,0), (-1,-1), 0.3, colors.HexColor("#dddddd")),
    ])
    rows = [[
        Paragraph(n, S("StepN", fontName="Helvetica-Bold", fontSize=11, textColor=BLUE)),
        Paragraph(desc, S("StepD", fontName="Helvetica", fontSize=10,
                           textColor=colors.black, leading=14)),
    ] for n, desc in steps]
    t = Table(rows, colWidths=[1.2*cm, 15.3*cm])
    t.setStyle(step_style)
    story.append(sp(4))
    story.append(t)

    story.append(sp(8))
    story += h2("Preguntas frecuentes")
    faqs = [
        ("Pulse Calculate pero no paso nada.",
         "Asegurate de que tanto Region como Type esten seleccionados antes de pulsar Calculate."),
        ("Mi eficiencia aparece como negativa o dice 'Invalid time'.",
         "La hora End es anterior a la hora Start. Normalmente significa que Start se quedo en un valor futuro. Corrige la hora Start y vuelve a calcular."),
        ("Guarde un caso con el tipo equivocado.",
         "Ve a la pestana History (o Production), selecciona la fila y pulsa Edit. El formulario se abre con todos los valores actuales — corrigelos y guarda de nuevo."),
        ("La barra de progreso esta atascada en 0%.",
         "La fecha del formulario puede ser diferente a hoy. Cambia la fecha a hoy o selecciona la fecha correcta en la pestana Production."),
        ("El texto en la pestana Standards no se ve en modo claro.",
         "Cambia de vuelta al modo oscuro y luego al modo claro de nuevo — el refresco del tema corrige los colores."),
    ]
    for q, a in faqs:
        story.append(sp(4))
        story.append(Paragraph(f"<b>P: {q}</b>",
                                S("Q", fontName="Helvetica-Bold", fontSize=10,
                                  textColor=BLUE, spaceAfter=2)))
        story.append(Paragraph(f"R: {a}",
                                S("A", fontName="Helvetica", fontSize=10,
                                  textColor=colors.black, leading=14,
                                  leftIndent=10, spaceAfter=2)))

    return story


# ── Numeracion de paginas ──────────────────────────────────────────────────────

def on_page(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(BLUE)
    canvas.setLineWidth(0.6)
    canvas.line(2*cm, 1.6*cm, W - 2*cm, 1.6*cm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(GREY)
    canvas.drawCentredString(W / 2, 1.1*cm,
                             f"Calculadora de Produccion — Guia de Usuario · Pagina {doc.page}")
    canvas.restoreState()


# ── Generar ────────────────────────────────────────────────────────────────────

def build():
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "Guia_Usuario_Production_Calculator.pdf")

    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm,  bottomMargin=2.2*cm,
        title="Calculadora de Produccion — Guia de Usuario",
        author="Equipo de Produccion",
    )

    story = []
    story += doc_header()

    story += section_overview()
    story.append(PageBreak())
    story += section_register()
    story.append(PageBreak())
    story += section_ot()
    story += section_production()
    story.append(PageBreak())
    story += section_history()
    story += section_standards()
    story.append(PageBreak())
    story += section_dashboard()
    story += section_concepts()
    story.append(PageBreak())
    story += section_quick_ref()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    print(f"PDF generado: {out_path}")
    return out_path


if __name__ == "__main__":
    build()
