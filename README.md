#  Production Performance Calculator

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/PySide6-Qt6-41CD52?style=for-the-badge&logo=qt&logoColor=white" alt="PySide6">
  <img src="https://img.shields.io/badge/SQLite-Database-003B57?style=for-the-badge&logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" alt="License">
</p>

Aplicación de escritorio para registrar, calcular y analizar la eficiencia de producción en tiempo real. Diseñada para equipos que manejan casos de diferentes regiones y tipos, permitiendo el seguimiento detallado del rendimiento.

---

##  Tabla de Contenidos

- [Características](#-características)
- [Requisitos Previos](#-requisitos-previos)
- [Instalación](#-instalación)
- [Uso](#-uso)
- [Estructura del Proyecto](#-estructura-del-proyecto)
- [Atajos de Teclado](#-atajos-de-teclado)
- [Compilar a Ejecutable](#-compilar-a-ejecutable)
- [Licencia](#-licencia)

---

##  Características

| Funcionalidad | Descripción |
|---------------|-------------|
|  **Registro de Casos** | Registro rápido con captura automática de hora de inicio |
|  **Cálculo de Eficiencia** | Cálculo automático basado en tiempos estándar configurables |
|  **Producción Diaria** | Barra de progreso en tiempo real |
|  **Gestión de Overtime** | Separación de casos regulares y horas extra |
|  **Historial** | Búsqueda y exportación de datos a CSV |
|  **Configuración Flexible** | Edición de estándares por región y tipo de caso |

---

##  Requisitos Previos

Antes de instalar, asegúrate de tener:

- **Python 3.10+** (recomendado 3.13)
- **pip** (gestor de paquetes de Python)
- **Git** (opcional, para clonar el repositorio)

### Verificar instalación de Python

```bash
python --version
```

Si no tienes Python instalado, descárgalo desde [python.org](https://www.python.org/downloads/)

---

##  Instalación

### 1. Clonar el repositorio

```bash
git clone https://github.com/tu-usuario/local_production_calc.git
cd local_production_calc
```

O descarga el ZIP y extrae los archivos.

### 2. Crear entorno virtual (recomendado)

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**macOS/Linux:**
```bash
python -m venv venv
source venv/bin/activate
```

### 3. Instalar dependencias

```bash
pip install PySide6 qtawesome
```

O si existe un archivo `requirements.txt`:
```bash
pip install -r requirements.txt
```

### 4. Ejecutar la aplicación

```bash
python main.py
```

---

##  Uso

### Flujo de trabajo típico

1. **Iniciar la aplicación** - Ejecuta `python main.py`

2. **Registrar un caso** (Tab Register):
   - Ingresa el Case ID → la hora se captura automáticamente
   - Selecciona la región → los tipos de caso se actualizan
   - Selecciona el tipo y doctor
   - Al terminar, ingresa la hora de fin
   - Click en **Save**

3. **Revisar progreso** - La barra de producción diaria muestra el % completado

4. **Casos en Overtime** - Usa el tab **OT** para casos fuera de horario regular

5. **Consultar historial** - Tab **History** para buscar, filtrar y exportar a CSV

6. **Configurar estándares** - Tab **Standards** para modificar tiempos por región/tipo

### Pestañas de la aplicación

| Tab | Función |
|-----|---------|
| **Register** | Registrar nuevos casos |
| **OT** | Registrar casos de horas extra |
| **Production** | Ver estadísticas y editar/eliminar casos |
| **History** | Buscar y exportar historial |
| **Standards** | Configurar tiempos estándar |

### Entendiendo la eficiencia

```
Eficiencia (%) = (Tiempo Estándar / Tiempo Real) × 100
```

-  **OK** = Eficiencia ≥ 100% (completado a tiempo o antes)
-  **LOW** = Eficiencia < 100% (tardó más de lo esperado)

---

##  Estructura del Proyecto

```
local_production_calc/
├── main.py                 # Punto de entrada de la aplicación
├── README.md               # Este archivo
├── LICENSE                 # Licencia del proyecto
│
├── db/
│   └── database.py         # Conexión y esquema SQLite
│
├── tabs/
│   ├── tab_register.py     # Registro de casos
│   ├── tab_production.py   # Vista de producción y estadísticas
│   ├── tab_history.py      # Historial y exportación
│   ├── tab_overtime.py     # Gestión de horas extra
│   ├── tab_standards.py    # Configuración de estándares
│   ├── downtime_manager.py # Gestión de tiempos muertos
│   └── toggle_switch.py    # Componente UI personalizado
│
└── data/
    ├── standards.json      # Tiempos estándar por región/tipo
    ├── units_eq.json       # Equivalencias de unidades
    └── cases.db            # Base de datos SQLite (se crea automáticamente)
```

---

##  Atajos de Teclado

| Atajo | Acción |
|-------|--------|
| `Ctrl + Shift + :` | Insertar hora actual |
| `Ctrl + Shift + ;` | Insertar fecha actual |

---

##  Compilar a Ejecutable

Para crear un archivo `.exe` standalone:

### 1. Instalar PyInstaller

```bash
pip install pyinstaller
```

### 2. Generar el ejecutable

```bash
pyinstaller --onefile --windowed --name "ProductionCalculator" main.py
```

El ejecutable se generará en la carpeta `dist/`.

### Opciones adicionales

```bash
# Con icono personalizado
pyinstaller --onefile --windowed --icon=app_icon.ico main.py

# Incluyendo archivos de datos
pyinstaller --onefile --windowed --add-data "data;data" main.py
```

---

##  Solución de Problemas

### Error: "ModuleNotFoundError: No module named 'PySide6'"
```bash
pip install PySide6
```

### Error: "ModuleNotFoundError: No module named 'qtawesome'"
```bash
pip install qtawesome
```

### La aplicación no inicia
Verifica que estés usando Python 3.10 o superior:
```bash
python --version
```

### Los datos no se guardan
Asegúrate de tener permisos de escritura en la carpeta del proyecto.

---

##  Contribuir

1. Fork el repositorio
2. Crea una rama para tu feature (`git checkout -b feature/nueva-funcionalidad`)
3. Commit tus cambios (`git commit -m 'Agregar nueva funcionalidad'`)
4. Push a la rama (`git push origin feature/nueva-funcionalidad`)
5. Abre un Pull Request

---

##  Licencia

Este proyecto está bajo la Licencia MIT. Ver el archivo [LICENSE](LICENSE) para más detalles.

---

## 👤 Autor

**Gerardo**  
Febrero 2026

---

<p align="center">
  Hecho con ❤️ y Python
</p>
