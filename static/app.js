// Rol del usuario en sesión, inyectado por la plantilla. Gobierna qué partes de
// la interfaz se construyen. El servidor valida los permisos de nuevo en cada
// endpoint: esto es comodidad visual, no la barrera de seguridad.
const IS_ADMIN = document.body.dataset.role === 'admin';

// ---------------------------------------------------------------------------
// Idioma
//
// La plantilla llega ya traducida desde el servidor. Aquí solo hace falta para
// el texto que arma el propio JavaScript: avisos, confirmaciones y las tablas
// que se construyen sobre la marcha.
//
// La clave es el texto en español, que es el idioma de partida: si falta la
// traducción se lee en español, en lugar de mostrar un identificador en crudo.
// ---------------------------------------------------------------------------
const CATALOGO = window.CATALOGO_IDIOMA || {};

/**
 * Traduce una cadena.
 *
 * Acepta sustituciones con nombre para no partir las frases: el orden de las
 * palabras cambia de un idioma a otro, y concatenar trozos daría resultados
 * ilegibles fuera del español.
 *
 *     T('Vista «{nombre}» guardada', { nombre: 'Entradas' })
 */
function T(texto, valores) {
    let salida = CATALOGO[texto] || texto;
    if (valores) {
        for (const clave of Object.keys(valores)) {
            salida = salida.split(`{${clave}}`).join(valores[clave]);
        }
    }
    return salida;
}

document.addEventListener('DOMContentLoaded', () => {
    // La cuadrícula se restaura DESPUÉS de conocer las cámaras: al colocar un
    // stream se busca su ficha para pintar la cabecera y los controles PTZ, y
    // sin ella las celdas saldrían desnudas.
    fetchCameras().then(async () => {
        await restaurarComposicionGuardada();
        await cargarVistas();
    });
    fetchDisplaySettings();
    fetchCameraStatus();
    // El servidor comprueba cada 30 s; se consulta a mitad de ese ritmo para
    // que un cambio de estado tarde poco en verse sin recargar de más.
    setInterval(fetchCameraStatus, 15000);
    if (IS_ADMIN) fetchDetectionSettings();
    startSessionWatch();
    setLayout(4); // Build initial 4-cell grid

    // Sidebar toggle
    document.getElementById('toggle-sidebar').addEventListener('click', () => {
        document.getElementById('sidebar').classList.toggle('hidden');
    });

    // Buscador de cámaras por nombre
    document.getElementById('camera-search-input').addEventListener('input', renderCameraList);

    // Idioma de la interfaz
    const selectorIdioma = document.getElementById('select-idioma');
    if (selectorIdioma) selectorIdioma.addEventListener('change', cambiarIdioma);

    // Guardar la cuadrícula actual como una vista con nombre
    document.getElementById('vista-guardar').addEventListener('click', guardarVistaNueva);

    // Modal logic
    const modal = document.getElementById('modal-overlay');
    document.getElementById('add-camera-btn').addEventListener('click', () => openModal());
    document.getElementById('modal-cancel').addEventListener('click', () => modal.classList.remove('active'));
    document.getElementById('modal-save').addEventListener('click', saveCamera);

    // Modal PTZ toggle field listener
    document.getElementById('modal-is-ptz').addEventListener('change', (e) => {
        const fields = document.getElementById('onvif-fields');
        if (fields) fields.style.display = e.target.checked ? 'block' : 'none';
    });

    // Display toggles
    setupToggle('toggle-fps',    'show_fps');
    setupToggle('toggle-labels', 'show_labels');
    setupToggle('toggle-speed',  'show_speed');
    setupToggle('toggle-ghost',  'show_ghost_boxes');

    // Detection mode radios
    document.querySelectorAll('input[name="detection_mode"]').forEach(radio => {
        radio.addEventListener('change', async (e) => {
            await fetch('/api/detection_settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ detection_mode: e.target.value })
            });
        });
    });

    // Layout buttons
    document.querySelectorAll('.layout-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            setLayout(parseInt(btn.dataset.layout));
        });
    });

    // Clear all streams button
    document.getElementById('clear-all-streams-btn').addEventListener('click', () => {
        if (confirm('\u00bfLimpiar todos los paneles de video?')) {
            clearAllStreams();
        }
    });
});

let cameras = [];

async function fetchCameras() {
    const res = await fetch('/api/cameras');
    cameras = await res.json();
    renderCameraList();
}

async function fetchDetectionSettings() {
    const res = await fetch('/api/detection_settings');
    const data = await res.json();
    if (data.detection_mode === 'all') {
        const radioAll = document.getElementById('mode-all');
        if (radioAll) radioAll.checked = true;
    } else {
        const radioMon = document.getElementById('mode-monitored');
        if (radioMon) radioMon.checked = true;
    }
}

// ---- Estado en línea de las cámaras ----
// El servidor sondea en segundo plano; aquí solo se consulta lo que ya tiene
// cacheado, así que este sondeo es barato.

let cameraStatus = {};

const ETIQUETAS_ESTADO = {
    online:  { texto: 'En servicio',   clase: 'online' },
    offline: { texto: 'Fuera de servicio', clase: 'offline' },
    unknown: { texto: 'Comprobando…',  clase: 'unknown' },
};

/**
 * Estado de la cámara en palabras, para el tooltip de su ficha.
 *
 * El color del borde dice si está en servicio, pero no distingue "caída" de
 * "aún no comprobada", ni cuándo se miró por última vez.
 */
function descripcionDeEstado(salud) {
    const info = ETIQUETAS_ESTADO[salud.status] || ETIQUETAS_ESTADO.unknown;

    let detalle = info.texto;
    if (salud.streaming) {
        detalle = 'En servicio · emitiendo ahora';
    } else if (salud.status === 'online' && salud.latency_ms != null) {
        detalle = `En servicio · respondió en ${salud.latency_ms} ms`;
    }
    if (salud.checked_seconds_ago != null) {
        detalle += ` · comprobada hace ${salud.checked_seconds_ago} s`;
    }
    return detalle;
}

async function fetchCameraStatus() {
    try {
        const res = await fetch('/api/cameras/status');
        if (!res.ok) return;
        const data = await res.json();
        cameraStatus = data.cameras || {};
        renderCameraList();
        updatePanelStatusDots();
    } catch (e) {
        // Un fallo puntual de red no debe borrar los indicadores existentes
    }
}

/**
 * Actualiza el punto de estado (status-dot) de cada panel de vídeo activo
 * según el último estado recibido del servidor. Sin esto, el punto siempre
 * aparece verde aunque la cámara esté caída.
 */
function updatePanelStatusDots() {
    document.querySelectorAll('.video-cell .status-dot[data-cam-id]').forEach(dot => {
        const camId = dot.dataset.camId;
        const salud = cameraStatus[camId] || { status: 'unknown' };
        // Quitar clases anteriores y aplicar la actual
        dot.classList.remove('estado-online', 'estado-offline', 'estado-unknown');
        dot.classList.add(`estado-${salud.status in ETIQUETAS_ESTADO ? salud.status : 'unknown'}`);
        dot.title = descripcionDeEstado(salud);
    });

    // Actualizar también los marcadores del mapa Leaflet si está abierto
    if (MAPA && MAPA.marcadores) {
        cameras.forEach(cam => {
            const marcador = MAPA.marcadores[cam.id];
            if (!marcador) return;
            marcador.setIcon(iconoDeCamara(cam));
        });
    }
}

function renderCameraList() {
    const list = document.getElementById('camera-list');
    const filtro = (document.getElementById('camera-search-input')?.value || '')
        .trim().toLowerCase();

    list.innerHTML = '';

    // Se busca por nombre o por número. Escribir "3" encuentra la cámara 3
    // sin tener que recordar cómo se llama; escribir "entr" encuentra las que
    // lleven eso en el nombre.
    const visibles = filtro
        ? cameras.filter(c => (c.name || '').toLowerCase().includes(filtro)
                           || String(c.numero || '') === filtro
                           || `#${c.numero || ''}` === filtro)
        : cameras;

    if (visibles.length === 0) {
        const vacio = document.createElement('li');
        vacio.style.cssText = 'padding:14px 16px; color:var(--text-muted); font-size:13px;';
        vacio.textContent = filtro
            ? `Ninguna cámara coincide con "${filtro}"`
            : 'No hay cámaras registradas';

        list.appendChild(vacio);
        return;
    }

    visibles.forEach(cam => {
        const li = document.createElement('li');
        li.className = 'camera-item';
        li.draggable = true;
        li.ondragstart = (e) => e.dataTransfer.setData('text/plain', JSON.stringify(cam));

        const ptzBadge = cam.is_ptz ? '<span class="ptz-badge">PTZ</span>' : '';

        // El estado se pinta en el borde de la ficha entera, no en un punto
        // aparte: se ve de un vistazo y ahorra la altura que ocupaba.
        const salud = cameraStatus[cam.id] || { status: 'unknown' };
        li.classList.add(`estado-${(ETIQUETAS_ESTADO[salud.status]
                                    ? salud.status : 'unknown')}`);
        li.title = descripcionDeEstado(salud);
        // Editar y eliminar solo para administradores. El operador ve la lista
        // y puede arrastrar cámaras a la cuadrícula, pero no modificarlas.
        const acciones = IS_ADMIN ? `
            <div class="cam-actions">
                <button onclick="openModal('${cam.id}')">✎</button>
                <button onclick="deleteCamera('${cam.id}')">🗑</button>
            </div>
        ` : '';

        const numero = cam.numero
            ? `<span class="cam-numero" title="Número de cámara">${cam.numero}</span>`
            : '';

        li.innerHTML = `
            <div class="cam-info">
                <h4>${numero}${cam.name} ${ptzBadge}</h4>
            </div>
            ${acciones}
        `;
        list.appendChild(li);
    });
}

function openModal(id = null) {
    const modal = document.getElementById('modal-overlay');
    document.getElementById('modal-title').innerText = id ? "Editar Cámara" : "Agregar Cámara";
    const onvifFields = document.getElementById('onvif-fields');

    if (id) {
        const cam = cameras.find(c => c.id === id);
        document.getElementById('modal-cam-id').value = cam.id;
        document.getElementById('modal-name').value = cam.name || "";
        document.getElementById('modal-source').value = cam.source || "";
        document.getElementById('modal-ip').value = cam.ip || "";
        document.getElementById('modal-onvif-port').value = cam.onvif_port || 80;
        document.getElementById('modal-user').value = cam.user || "";
        document.getElementById('modal-password').value = cam.password || "";
        document.getElementById('modal-is-ptz').checked = !!cam.is_ptz;
    } else {
        document.getElementById('modal-cam-id').value = "";
        document.getElementById('modal-name').value = "";
        document.getElementById('modal-source').value = "";
        document.getElementById('modal-ip').value = "";
        document.getElementById('modal-onvif-port').value = 80;
        document.getElementById('modal-user').value = "";
        document.getElementById('modal-password').value = "";
        document.getElementById('modal-is-ptz').checked = false;
    }
    
    if (onvifFields) {
        onvifFields.style.display = document.getElementById('modal-is-ptz').checked ? 'block' : 'none';
    }

    modal.classList.add('active');
}

async function saveCamera() {
    const id = document.getElementById('modal-cam-id').value;
    const data = {
        name: document.getElementById('modal-name').value,
        // Ya no se dan de alta cámaras USB, así que el tipo es fijo. Se sigue
        // enviando porque el resto del sistema lo espera, y las cámaras USB
        // que ya estuvieran registradas siguen funcionando.
        type: 'IP',
        source: document.getElementById('modal-source').value,
        ip: document.getElementById('modal-ip').value,
        onvif_port: document.getElementById('modal-onvif-port').value,
        user: document.getElementById('modal-user').value,
        password: document.getElementById('modal-password').value,
        is_ptz: document.getElementById('modal-is-ptz').checked
    };

    const method = id ? 'PUT' : 'POST';
    const url = id ? `/api/cameras/${id}` : '/api/cameras';

    await fetch(url, {
        method: method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });

    document.getElementById('modal-overlay').classList.remove('active');
    fetchCameras();
}

async function deleteCamera(id) {
    if(confirm("¿Seguro que deseas eliminar esta cámara?")) {
        await fetch(`/api/cameras/${id}`, { method: 'DELETE' });
        fetchCameras();
    }
}

// Drag & Drop
function allowDrop(ev) {
    ev.preventDefault();
    ev.currentTarget.classList.add('drag-over');
}

function drop(ev) {
    ev.preventDefault();
    const cell = ev.currentTarget;
    cell.classList.remove('drag-over');
    
    try {
        const camData = JSON.parse(ev.dataTransfer.getData('text/plain'));
        const baseSrc = `/video_feed/${camData.id}`;
        
        // Remove existing video & header if any
        clearCellElements(cell);

        // Add new stream image and camera header
        _placeCellStream(cell, baseSrc, camData);

        // Persist to global streamState
        const cellIndex = parseInt(cell.dataset.index);
        streamState[cellIndex] = { src: baseSrc, camId: camData.id };
        guardarComposicionActual();

    } catch (e) {
        console.error("Drop parsing error", e);
    }
}

function clearCellElements(cell) {
    const existingImg = cell.querySelector('img');
    if (existingImg) {
        existingImg.src = '';
        existingImg.remove();
    }
    const existingHeader = cell.querySelector('.camera-header');
    if (existingHeader) existingHeader.remove();
    const existingZoom = cell.querySelector('.zoom-indicator');
    if (existingZoom) existingZoom.remove();
    const existingUbi = cell.querySelector('.cam-ubicacion');
    if (existingUbi) existingUbi.remove();
    const existingFit = cell.querySelector('.cam-fit-toggle');
    if (existingFit) existingFit.remove();
    cell.classList.remove('ptz-active', 'ptz-dragging', 'fit-cover');
}

// Delegated dragleave (works for dynamically created cells)
document.getElementById('grid-container').addEventListener('dragleave', (e) => {
    const cell = e.target.closest('.video-cell');
    if (cell) cell.classList.remove('drag-over');
});

// Clear Cell (Context Menu or button)
function clearCell(ev, index) {
    if (ev) ev.preventDefault();
    const cell = document.querySelector(`.video-cell[data-index="${index}"]`);
    if (cell) {
        clearCellElements(cell);
        const ph = cell.querySelector('.placeholder-text');
        if (ph) ph.style.display = 'block';
        cell.style.border = '';
    }
    streamState[index] = null;
    guardarComposicionActual();
}

// Double click fullscreen
function toggleFullscreen(cell) {
    const container = document.getElementById('grid-container');
    if (container.classList.contains('fullscreen')) {
        container.classList.remove('fullscreen');
        cell.classList.remove('active-full');
    } else {
        container.classList.add('fullscreen');
        cell.classList.add('active-full');
    }
}

// ---- Layout switcher ----
let currentLayout = 4;
// Global stream state: array of { src, camId } objects or null
let streamState = [];

function _placeCellStream(cell, baseSrc, camData = null) {
    clearCellElements(cell);

    // Extract cam_id from baseSrc if camData not directly provided
    if (!camData) {
        const parts = baseSrc.split('/video_feed/');
        if (parts.length > 1) {
            const camId = parts[1].split('?')[0];
            camData = cameras.find(c => c.id === camId);
        }
    }

    const img = document.createElement('img');
    img.src = `${baseSrc}?t=${Date.now()}`;
    img.ondblclick = () => toggleFullscreen(cell);
    img.draggable = false;
    img.addEventListener('dragstart', (e) => e.preventDefault());
    cell.appendChild(img);

    const ph = cell.querySelector('.placeholder-text');
    if (ph) ph.style.display = 'none';
    cell.style.border = 'none';

    // Inject Camera Header Overlay
    if (camData) {
        renderCellHeader(cell, camData);
        renderFitToggle(cell, camData);
        renderBotonUbicacion(cell, camData);
        const isPtz = camData.is_ptz === true || camData.is_ptz === 'true' || camData.is_ptz == 1;
        if (isPtz) {
            attachPTZListeners(cell, camData);
        }
    }
}

// ---- Encuadre del vídeo dentro del panel ----
// La elección se guarda por cámara en el navegador: depende del tamaño y la
// proporción de la pantalla que se esté usando, así que no tiene sentido
// llevarla al servidor y compartirla entre equipos distintos.

const FIT_CONTAIN = 'contain';   // Transmisión original, con franjas si sobra
const FIT_COVER = 'cover';       // Rellena el panel recortando los bordes
const FIT_STORAGE_KEY = 'camFitModes';

function loadFitModes() {
    try {
        return JSON.parse(localStorage.getItem(FIT_STORAGE_KEY)) || {};
    } catch (e) {
        return {};   // Almacenamiento corrupto o deshabilitado
    }
}

function getFitMode(camId) {
    return loadFitModes()[camId] === FIT_COVER ? FIT_COVER : FIT_CONTAIN;
}

function setFitMode(camId, modo) {
    try {
        const modos = loadFitModes();
        modos[camId] = modo;
        localStorage.setItem(FIT_STORAGE_KEY, JSON.stringify(modos));
    } catch (e) {
        // Navegación privada o almacenamiento lleno: el cambio se aplica
        // igualmente en esta sesión, solo no se recuerda.
    }
}

const ICONO_CONTAIN = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="1.5" y="3.5" width="13" height="9" rx="1"/><path d="M4.5 3.5v9M11.5 3.5v9"/></svg>';
const ICONO_COVER = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="1.5" y="2.5" width="13" height="11" rx="1"/></svg>';

function applyFitMode(cell, modo) {
    cell.classList.toggle('fit-cover', modo === FIT_COVER);
    const btn = cell.querySelector('.cam-fit-toggle');
    if (!btn) return;

    const esCover = modo === FIT_COVER;
    btn.innerHTML = (esCover ? ICONO_COVER : ICONO_CONTAIN)
        + `<span>${esCover ? 'Rellenar' : 'Original'}</span>`;
    btn.title = esCover
        ? 'Rellenando el panel: se recortan los bordes. Pulsa para ver la transmisión completa.'
        : 'Transmisión original completa. Pulsa para rellenar el panel recortando los bordes.';
    btn.setAttribute('aria-pressed', String(esCover));
}

function renderFitToggle(cell, cam) {
    const btn = document.createElement('button');
    btn.className = 'cam-fit-toggle';
    btn.type = 'button';
    // Sin esto, el clic llegaría a la celda y activaría el arrastre o el PTZ
    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const nuevo = cell.classList.contains('fit-cover') ? FIT_CONTAIN : FIT_COVER;
        setFitMode(cam.id, nuevo);
        applyFitMode(cell, nuevo);
    });
    btn.addEventListener('dblclick', (e) => e.stopPropagation());
    cell.appendChild(btn);
    applyFitMode(cell, getFitMode(cam.id));
}

/**
 * Botón que lleva del panel de vídeo a la ubicación de esa cámara en el mapa.
 *
 * Va abajo a la izquierda, en espejo del ajuste de imagen que ocupa la
 * derecha, y aparece al pasar por encima como el resto de controles de la
 * celda: en una sala de control lo que importa es la imagen, y los mandos solo
 * cuando se buscan.
 */
function renderBotonUbicacion(cell, cam) {
    const btn = document.createElement('button');
    btn.className = 'cam-ubicacion';
    btn.type = 'button';
    btn.title = `Ver ${cam.name} en el mapa`;
    btn.setAttribute('aria-label', `Ver ${cam.name} en el mapa`);

    // Sin esto el clic llega a la celda y activa el arrastre o el control PTZ
    btn.addEventListener('click', e => {
        e.stopPropagation();
        abrirMapa(cam.id);
    });
    btn.addEventListener('dblclick', e => e.stopPropagation());
    cell.appendChild(btn);
}

function renderCellHeader(cell, cam) {
    const header = document.createElement('div');
    header.className = 'camera-header';
    
    const isPtz = cam.is_ptz === true || cam.is_ptz === 'true' || cam.is_ptz == 1;
    const ptzBadge = isPtz ? '<span class="ptz-badge">PTZ</span>' : '';
    const index = cell.dataset.index;

    // El estado inicial se toma del caché actual; se actualizará en el
    // próximo ciclo de fetchCameraStatus vía updatePanelStatusDots().
    const salud = cameraStatus[cam.id] || { status: 'unknown' };
    const estadoClase = `estado-${salud.status in ETIQUETAS_ESTADO ? salud.status : 'unknown'}`;
    const estadoTitulo = descripcionDeEstado(salud);

    header.innerHTML = `
        <div class="camera-header-left">
            <span class="status-dot ${estadoClase}" data-cam-id="${cam.id}" title="${estadoTitulo}"></span>
            <span class="camera-title">${cam.name}</span>
            ${ptzBadge}
        </div>
        <div class="camera-header-actions">
            <button class="cam-panel-btn" title="Limpiar panel" onclick="clearCell(event, ${index})">✖</button>
        </div>
    `;

    cell.appendChild(header);

    // Medir la cabecera y publicar su alto real, que es donde empieza la
    // imagen. Un valor fijo en el CSS se desajustaría con otro tamaño de
    // fuente o con el zoom del navegador, y volvería a tapar la transmisión.
    const alto = Math.ceil(header.getBoundingClientRect().height);
    if (alto > 0) {
        cell.style.setProperty('--cam-header-h', `${alto}px`);
    }
}

// --- PTZ Mouse Interaction Logic ---
function attachPTZListeners(cell, cam) {
    cell.classList.add('ptz-active');
    
    let isDragging = false;
    let startX = 0;
    let startY = 0;
    let lastMoveTime = 0;
    const THROTTLE_MS = 200;
    const MAX_RADIUS = 150.0;
    let zoomDebounceTimer = null;

    const img = cell.querySelector('img');
    if (!img) return;

    img.draggable = false;
    img.addEventListener('dragstart', (e) => e.preventDefault());

    // --- Virtual Joystick Drag & Drop ---
    img.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return; // Left click only
        e.preventDefault(); // Prevent native browser drag / text selection
        isDragging = true;
        startX = e.clientX;
        startY = e.clientY;
        cell.classList.add('ptz-dragging');
    });

    window.addEventListener('mousemove', (e) => {
        if (!isDragging) return;
        
        const now = Date.now();
        if (now - lastMoveTime < THROTTLE_MS) return;
        lastMoveTime = now;

        const deltaX = e.clientX - startX;
        const deltaY = e.clientY - startY;
        const dist = Math.hypot(deltaX, deltaY);

        if (dist < 5) return; // Deadzone

        const normDist = Math.min(dist, MAX_RADIUS);
        const speed = Math.max(0.1, Math.min(1.0, normDist / MAX_RADIUS));

        const pan = deltaX / dist;
        const tilt = - (deltaY / dist); // Invert Y for screen coords

        fetch(`/api/camera/${cam.id}/ptz/move`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pan, tilt, speed })
        }).catch(err => console.error("PTZ move error:", err));
    });

    const stopDragging = () => {
        if (!isDragging) return;
        isDragging = false;
        cell.classList.remove('ptz-dragging');

        fetch(`/api/camera/${cam.id}/ptz/stop`, {
            method: 'POST'
        }).catch(err => console.error("PTZ stop error:", err));
    };

    window.addEventListener('mouseup', stopDragging);

    // --- Mouse Wheel Zoom ---
    cell.addEventListener('wheel', (e) => {
        e.preventDefault();

        const action = e.deltaY < 0 ? 'zoom_in' : 'zoom_out';
        showZoomIndicator(cell, action === 'zoom_in' ? '🔍 +' : '🔍 -');

        fetch(`/api/camera/${cam.id}/ptz`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action, speed: 0.5 })
        }).catch(err => console.error("PTZ zoom error:", err));

        if (zoomDebounceTimer) clearTimeout(zoomDebounceTimer);
        zoomDebounceTimer = setTimeout(() => {
            fetch(`/api/camera/${cam.id}/ptz/stop`, { method: 'POST' });
        }, 350);
    }, { passive: false });
}

function showZoomIndicator(cell, text) {
    let indicator = cell.querySelector('.zoom-indicator');
    if (!indicator) {
        indicator = document.createElement('div');
        indicator.className = 'zoom-indicator';
        cell.appendChild(indicator);
    }
    indicator.textContent = text;

    if (cell._zoomHideTimer) clearTimeout(cell._zoomHideTimer);
    cell._zoomHideTimer = setTimeout(() => {
        if (indicator) indicator.remove();
    }, 1000);
}

function setLayout(count) {
    currentLayout = count;
    const container = document.getElementById('grid-container');

    // 1. Sync current visible cells back into streamState before rebuilding
    container.querySelectorAll('.video-cell').forEach((cell, i) => {
        const img = cell.querySelector('img');
        const src = img && img.src && !img.src.endsWith('/') ? img.src.split('?')[0] : null;
        if (src) {
            const parts = src.split('/video_feed/');
            const camId = parts.length > 1 ? parts[1] : null;
            streamState[i] = { src, camId };
        }
    });

    // 2. Stop streams cleanly
    container.querySelectorAll('.video-cell').forEach(cell => clearCellElements(cell));

    // 3. Rebuild cells
    container.innerHTML = '';
    for (let i = 0; i < count; i++) {
        const div = document.createElement('div');
        div.className = 'video-cell';
        div.dataset.index = i;
        div.setAttribute('ondragover', 'allowDrop(event)');
        div.setAttribute('ondrop', 'drop(event)');
        div.setAttribute('oncontextmenu', `clearCell(event, ${i})`);
        const span = document.createElement('span');
        span.className = 'placeholder-text';
        span.textContent = 'Arrastra una c\u00e1mara aqu\u00ed';
        div.appendChild(span);
        container.appendChild(div);
    }

    // 4. Restore from global streamState
    const cells = container.querySelectorAll('.video-cell');
    let cellIndex = 0;
    for (let i = 0; i < streamState.length && cellIndex < cells.length; i++) {
        if (streamState[i] && streamState[i].src) {
            _placeCellStream(cells[cellIndex++], streamState[i].src);
        }
    }

    // 5. Update grid class and active button
    container.className = `grid-container layout-${count}`;
    document.querySelectorAll('.layout-btn').forEach(btn => {
        btn.classList.toggle('active', parseInt(btn.dataset.layout) === count);
    });

    guardarComposicionActual();
}

function clearAllStreams() {
    streamState = [];
    const container = document.getElementById('grid-container');
    container.querySelectorAll('.video-cell').forEach(cell => {
        clearCellElements(cell);
        const ph = cell.querySelector('.placeholder-text');
        if (ph) ph.style.display = 'block';
        cell.style.border = '';
    });
    guardarComposicionActual();
}


// ---- Display Settings ----

async function fetchDisplaySettings() {
    const res = await fetch('/api/display_settings');
    const settings = await res.json();
    document.getElementById('toggle-fps').checked    = settings.show_fps;
    document.getElementById('toggle-labels').checked = settings.show_labels;
    document.getElementById('toggle-speed').checked  = settings.show_speed;
    document.getElementById('toggle-ghost').checked  = settings.show_ghost_boxes;
}

function setupToggle(checkboxId, settingKey) {
    document.getElementById(checkboxId).addEventListener('change', async (e) => {
        await fetch('/api/display_settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ [settingKey]: e.target.checked })
        });
        // Refresh all active streams so they pick up the new setting immediately
        document.querySelectorAll('.video-cell img').forEach(img => {
            const base = img.src.split('?')[0];
            img.src = `${base}?t=${Date.now()}`;
        });
    });
}

// ---- Personalización por usuario ----
// El tema ya viene aplicado en el HTML que envía el servidor; aquí solo se
// sincroniza el interruptor y se guarda al cambiarlo.

function currentTheme() {
    return document.documentElement.dataset.theme || 'dark';
}

async function fetchPreferences() {
    const res = await fetch('/api/preferences');
    if (!res.ok) return;
    const prefs = await res.json();
    applyTheme(prefs.theme);
    document.getElementById('toggle-dark-mode').checked = prefs.theme === 'dark';
}

function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
}

async function saveTheme(theme) {
    const anterior = currentTheme();
    // Se aplica antes de la respuesta para que el cambio se vea inmediato
    applyTheme(theme);

    const res = await fetch('/api/preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme })
    });

    if (!res.ok) {
        // No se pudo guardar: se revierte para no mostrar un estado que el
        // servidor no tiene, y que se perdería al recargar.
        applyTheme(anterior);
        document.getElementById('toggle-dark-mode').checked = anterior === 'dark';
        showToast('No se pudo guardar la preferencia de tema.');
    }
}

document.getElementById('toggle-dark-mode').addEventListener('change', (e) => {
    saveTheme(e.target.checked ? 'dark' : 'light');
});

// ---- Modal de Configuración ----
// Agrupa el modo de detección y las superposiciones de video, que antes vivían
// en un panel fijo al pie de la barra lateral.

document.getElementById('open-settings-btn').addEventListener('click', () => {
    // Releer del servidor al abrir: los ajustes pueden haber cambiado desde
    // otra pestaña o desde otro equipo en modo remoto.
    fetchDisplaySettings();
    fetchPreferences();
    if (IS_ADMIN) {
        fetchDetectionSettings();
        fetchAlprSettings();
        fetchSecuritySettings();
        fetchUsers();
    }
    document.getElementById('settings-modal-overlay').classList.add('active');
});

document.getElementById('settings-close').addEventListener('click', () => {
    document.getElementById('settings-modal-overlay').classList.remove('active');
});

// Cerrar al pulsar fuera del cuadro, como en los demás paneles
document.getElementById('settings-modal-overlay').addEventListener('click', (e) => {
    if (e.target.id === 'settings-modal-overlay') {
        e.currentTarget.classList.remove('active');
    }
});

// ---- Formato de matrícula por país ----
// Los formatos los define el servidor (plate_format.py), de modo que añadir un
// país nuevo no exige tocar el frontend.

let alprFormats = [];

async function fetchAlprSettings() {
    const res = await fetch('/api/alpr_settings');
    const data = await res.json();
    alprFormats = data.available_formats || [];

    // Agrupar por región (América, Europa...) para que la lista sea navegable.
    // Se respeta el orden que envía el servidor en lugar de reordenar aquí.
    const select = document.getElementById('alpr-format-select');
    select.innerHTML = '';
    const grupos = new Map();
    alprFormats.forEach(fmt => {
        const region = fmt.region || 'Otros';
        if (!grupos.has(region)) {
            const grupo = document.createElement('optgroup');
            grupo.label = region;
            grupos.set(region, grupo);
            select.appendChild(grupo);
        }
        const opt = document.createElement('option');
        opt.value = fmt.key;
        opt.textContent = `${fmt.name} — ${fmt.example}`;
        grupos.get(region).appendChild(opt);
    });
    select.value = data.plate_format;
    updateFormatDescription();

    const pct = Math.round((data.min_confidence ?? 0.5) * 100);
    document.getElementById('alpr-confidence-range').value = pct;
    document.getElementById('alpr-confidence-value').textContent = `${pct}%`;
}

function updateFormatDescription() {
    const key = document.getElementById('alpr-format-select').value;
    const fmt = alprFormats.find(f => f.key === key);
    document.getElementById('alpr-format-desc').textContent = fmt ? fmt.description : '';
}

async function saveAlprSettings() {
    const plate_format = document.getElementById('alpr-format-select').value;
    const min_confidence = parseInt(document.getElementById('alpr-confidence-range').value, 10) / 100;

    const res = await fetch('/api/alpr_settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plate_format, min_confidence })
    });

    const msg = document.getElementById('alpr-settings-msg');
    if (res.ok) {
        const fmt = alprFormats.find(f => f.key === plate_format);
        msg.textContent = `✔ Formato aplicado: ${fmt ? fmt.name : plate_format}`;
        msg.style.color = '#4ade80';
    } else {
        const err = await res.json().catch(() => ({}));
        msg.textContent = `✖ ${err.message || 'No se pudo guardar'}`;
        msg.style.color = '#f87171';
    }
    msg.classList.add('visible');
    clearTimeout(saveAlprSettings._timer);
    saveAlprSettings._timer = setTimeout(() => msg.classList.remove('visible'), 2500);
}

document.getElementById('alpr-format-select').addEventListener('change', () => {
    updateFormatDescription();
    saveAlprSettings();
});

// El deslizador actualiza la etiqueta de forma continua, pero solo guarda al
// soltarlo: guardar en cada píxel de arrastre generaría decenas de escrituras.
document.getElementById('alpr-confidence-range').addEventListener('input', (e) => {
    document.getElementById('alpr-confidence-value').textContent = `${e.target.value}%`;
});
document.getElementById('alpr-confidence-range').addEventListener('change', saveAlprSettings);

// ---- ALPR Dashboard ----
let alprPollInterval = null;

document.getElementById('open-alpr-btn').addEventListener('click', () => {
    document.getElementById('alpr-modal-overlay').classList.add('active');
    // Los tipos dependen del país configurado, que pudo cambiar desde la última
    // vez que se abrió este panel.
    fetchAlprSettings().then(fetchPlateTypes);
    fetchLatestPlates();
    // Start polling every 2 seconds
    alprPollInterval = setInterval(fetchLatestPlates, 2000);
});

document.getElementById('alpr-close').addEventListener('click', () => {
    document.getElementById('alpr-modal-overlay').classList.remove('active');
    if(alprPollInterval) {
        clearInterval(alprPollInterval);
        alprPollInterval = null;
    }
});

document.getElementById('alpr-search-input').addEventListener('input', (e) => {
    const query = e.target.value;
    if (query.trim() === '') {
        // Resume polling if search is empty
        if(!alprPollInterval) alprPollInterval = setInterval(fetchLatestPlates, 2000);
        fetchLatestPlates();
    } else {
        // Stop polling while searching
        if(alprPollInterval) {
            clearInterval(alprPollInterval);
            alprPollInterval = null;
        }
        searchPlates(query);
    }
});

async function fetchLatestPlates() {
    const res = await fetch('/api/plates/latest');
    const plates = await res.json();
    renderPlates(plates);
}

async function searchPlates(query) {
    const res = await fetch(`/api/plates/search?q=${encodeURIComponent(query)}`);
    const plates = await res.json();
    renderPlates(plates);
}

function renderPlates(plates) {
    const tbody = document.getElementById('alpr-table-body');
    tbody.innerHTML = '';
    
    if (plates.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-muted);">No hay registros</td></tr>';
        return;
    }

    plates.forEach(p => {
        const tr = document.createElement('tr');
        
        // Find camera name
        const cam = cameras.find(c => c.source === p.camera_id || c.id === p.camera_id);
        const camName = cam ? cam.name : p.camera_id;

        tr.innerHTML = `
            <td><span class="alpr-plate-badge">${p.plate_text}</span></td>
            <td>${camName}</td>
            <td>${(p.confidence * 100).toFixed(1)}%</td>
            <td>${p.timestamp}</td>
        `;
        tbody.appendChild(tr);
    });
}

// ---- Face Recognition (FR) Dashboard & Alerts ----
let frPollInterval = null;
let lastFrTimestamp = null;

document.getElementById('open-fr-btn').addEventListener('click', () => {
    document.getElementById('fr-modal-overlay').classList.add('active');
    fetchLatestFaces();
    fetchKnownFaces();
});

document.getElementById('fr-close').addEventListener('click', () => {
    document.getElementById('fr-modal-overlay').classList.remove('active');
});

// --- Tab logic ---
document.querySelectorAll('.fr-tab').forEach(btn => {
    btn.addEventListener('click', () => {
        const modal = btn.closest('.modal');
        modal.querySelectorAll('.fr-tab').forEach(t => t.classList.remove('active'));
        modal.querySelectorAll('.fr-tab-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById(btn.dataset.tab).classList.add('active');
        
        if (btn.dataset.tab === 'fr-tab-manage') fetchKnownFaces();
        if (btn.dataset.tab === 'fr-tab-detections') fetchLatestFaces();
        if (btn.dataset.tab === 'alpr-tab-manage') fetchWatchedPlates();
        if (btn.dataset.tab === 'alpr-tab-detections') fetchLatestPlates();
        if (btn.dataset.tab === 'settings-tab-users') fetchUsers();
        if (btn.dataset.tab === 'settings-tab-session') fetchSecuritySettings();
        if (btn.dataset.tab === 'settings-tab-audit') fetchAuditLog();
        if (btn.dataset.tab === 'settings-tab-nvr') {
            fetchNvrSettings(); fetchNvrStatus(); fetchNvrCameras();
        }
    });
});

// Handle Register Form
document.getElementById('fr-register-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('fr-register-name').value;
    const fileInput = document.getElementById('fr-register-image');
    
    if (fileInput.files.length === 0) return;
    
    const formData = new FormData();
    formData.append('name', name);
    formData.append('image', fileInput.files[0]);
    
    const msgDiv = document.getElementById('fr-register-msg');
    msgDiv.textContent = "Registrando...";
    msgDiv.style.color = "var(--text-color)";
    
    try {
        const res = await fetch('/api/faces/register', {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        
        if (data.status === 'success') {
            msgDiv.textContent = data.message;
            msgDiv.style.color = "#4CAF50";
            document.getElementById('fr-register-form').reset();
        } else {
            msgDiv.textContent = data.message;
            msgDiv.style.color = "#F44336";
        }
    } catch (err) {
        msgDiv.textContent = "Error al conectar con el servidor.";
        msgDiv.style.color = "#F44336";
    }
});

async function fetchLatestFaces() {
    const res = await fetch('/api/faces/latest');
    const faces = await res.json();
    renderFaces(faces);
}

function renderFaces(faces) {
    const tbody = document.getElementById('fr-table-body');
    tbody.innerHTML = '';
    
    if (faces.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-muted);">No hay registros</td></tr>';
        return;
    }

    faces.forEach(f => {
        const tr = document.createElement('tr');
        
        // Find camera name
        const cam = cameras.find(c => c.source === f.camera_id || c.id === f.camera_id);
        const camName = cam ? cam.name : f.camera_id;

        tr.innerHTML = `
            <td><strong>${f.name}</strong></td>
            <td>${camName}</td>
            <td>${(f.confidence * 100).toFixed(1)}%</td>
            <td>${f.timestamp}</td>
        `;
        tbody.appendChild(tr);
    });
}

// Global polling for alerts
setInterval(async () => {
    try {
        const res = await fetch('/api/faces/latest?limit=1');
        const faces = await res.json();
        if (faces.length > 0) {
            const latest = faces[0];
            if (lastFrTimestamp !== null && latest.timestamp !== lastFrTimestamp) {
                // New face detected!
                const cam = cameras.find(c => c.source === latest.camera_id || c.id === latest.camera_id);
                const camName = cam ? cam.name : latest.camera_id;
                
                showToast(`👤 ${latest.name} detectado/a en ${camName}`);
                playAlertSound();
                
                // Update table if modal is open
                if (document.getElementById('fr-modal-overlay').classList.contains('active')) {
                    fetchLatestFaces();
                }
            }
            lastFrTimestamp = latest.timestamp;
        }
    } catch (e) {
        // ignore
    }
}, 2000);

function showToast(message) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.innerHTML = message;
    
    container.appendChild(toast);
    
    // Auto remove after 5s
    setTimeout(() => {
        toast.classList.add('fade-out');
        toast.addEventListener('animationend', () => {
            toast.remove();
        });
    }, 5000);
}

function playAlertSound() {
    const audio = document.getElementById('alert-sound');
    if (audio) {
        // Reset and play to handle rapid overlapping alerts
        audio.currentTime = 0;
        audio.play().catch(e => console.log("Audio autoplay blocked:", e));
    }
}

// ---- Known Faces Management ----
async function fetchKnownFaces() {
    const res = await fetch('/api/faces/known');
    const faces = await res.json();
    renderKnownFaces(faces);
}

function renderKnownFaces(faces) {
    const tbody = document.getElementById('fr-known-table-body');
    tbody.innerHTML = '';
    
    if (faces.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color:var(--text-muted);">No hay rostros registrados</td></tr>';
        return;
    }
    
    faces.forEach(f => {
        const tr = document.createElement('tr');
        tr.dataset.id = f.id;
        tr.innerHTML = `
            <td style="width: 50px; text-align: center;">
                <img src="/static/faces/${f.id}.jpg?t=${new Date().getTime()}" style="width: 40px; height: 40px; object-fit: cover; border-radius: 4px; display: inline-block; background: #333;" onerror="this.style.display='none'">
            </td>
            <td class="fr-name-cell">${f.name}</td>
            <td style="white-space:nowrap; text-align: right;">
                <button class="btn-edit" onclick="startRename(${f.id}, this)">✏ Editar</button>
                <button class="btn-delete" onclick="deleteFace(${f.id})">🗑</button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

function startRename(id, btn) {
    const tr = btn.closest('tr');
    const nameCell = tr.querySelector('.fr-name-cell');
    const currentName = nameCell.textContent;
    
    nameCell.innerHTML = `
        <input type="text" value="${currentName}" 
            style="width:100%; box-sizing:border-box; padding:4px 8px; background:var(--bg-card); 
            border:1px solid var(--primary-color); border-radius:4px; color:var(--text-color); font-size:0.95em; margin-bottom: 5px;">
        <br>
        <label style="font-size: 0.8em; color: var(--text-muted);">Nueva foto (opcional):</label>
        <input type="file" class="edit-image-file" accept="image/*" style="font-size: 0.8em; max-width: 150px;">
    `;
    
    const input = nameCell.querySelector('input');
    input.focus();
    input.select();
    
    // Replace buttons
    const actionsCell = btn.parentElement;
    actionsCell.innerHTML = `
        <button class="btn-edit" onclick="confirmRename(${id}, this)">✔ Guardar</button>
        <button class="btn-delete" onclick="cancelRename(${id})">✖ Cancelar</button>
    `;
}

async function confirmRename(id, btn) {
    const tr = btn.closest('tr');
    const input = tr.querySelector('.fr-name-cell input[type="text"]');
    const fileInput = tr.querySelector('.edit-image-file');
    const newName = input ? input.value.trim() : '';
    if (!newName) return;
    
    const formData = new FormData();
    formData.append('name', newName);
    if (fileInput && fileInput.files.length > 0) {
        formData.append('image', fileInput.files[0]);
    }
    
    await fetch(`/api/faces/known/${id}`, {
        method: 'PUT',
        body: formData
    });
    fetchKnownFaces();
}

function cancelRename() {
    fetchKnownFaces();
}

async function deleteFace(id) {
    if (!confirm('¿Eliminar este rostro?')) return;
    await fetch(`/api/faces/known/${id}`, {method: 'DELETE'});
    fetchKnownFaces();
}

document.getElementById('fr-delete-all-btn').addEventListener('click', async () => {
    if (!confirm('¿Eliminar TODOS los rostros registrados? Esta acción no se puede deshacer.')) return;
    await fetch('/api/faces/known', {method: 'DELETE'});
    fetchKnownFaces();
});

// ---- ALPR Watchlist Management ----
let lastAlprAlertTimestamp = null;

// ---- Tipos de placa ----
// Los tipos dependen del país configurado en los ajustes, así que se recargan
// cada vez que se abre el panel de placas en lugar de solo al iniciar.

let plateTypes = [];
let plateTypesCountry = '';

async function fetchPlateTypes() {
    const res = await fetch('/api/plate_types');
    const data = await res.json();
    plateTypes = data.types || [];
    plateTypesCountry = data.country || '';

    const select = document.getElementById('alpr-register-type');
    const previo = select.value;
    select.innerHTML = '';
    plateTypes.forEach(t => {
        const opt = document.createElement('option');
        opt.value = t.key;
        opt.textContent = t.color ? `${t.name} — ${t.color}` : t.name;
        select.appendChild(opt);
    });
    // Conservar la elección del usuario si el tipo sigue existiendo
    select.value = plateTypes.some(t => t.key === previo) ? previo : 'any';

    // Indicar de qué país son los tipos, y avisar cuando son los genéricos
    const etiqueta = document.getElementById('alpr-register-country');
    const fmt = (typeof alprFormats !== 'undefined')
        ? alprFormats.find(f => f.key === plateTypesCountry) : null;
    const nombrePais = fmt ? fmt.name : plateTypesCountry;
    etiqueta.textContent = data.specific
        ? `(${nombrePais})`
        : `(${nombrePais} — tipos genéricos)`;

    updatePlateTypeDescription();
}

function updatePlateTypeDescription() {
    const key = document.getElementById('alpr-register-type').value;
    const t = plateTypes.find(x => x.key === key);
    document.getElementById('alpr-register-type-desc').textContent = t ? t.description : '';
}

document.getElementById('alpr-register-type').addEventListener('change', updatePlateTypeDescription);

// Handle ALPR Register Form
document.getElementById('alpr-register-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const pattern = document.getElementById('alpr-register-pattern').value.toUpperCase();
    const note = document.getElementById('alpr-register-note').value;
    const plate_type = document.getElementById('alpr-register-type').value;

    const msgDiv = document.getElementById('alpr-register-msg');
    msgDiv.textContent = "Registrando...";
    msgDiv.style.color = "var(--text-color)";

    try {
        const res = await fetch('/api/watched_plates', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                plate_pattern: pattern, note: note,
                plate_type: plate_type, country: plateTypesCountry
            })
        });
        const data = await res.json();

        if (data.status === 'success') {
            if (data.warning) {
                // Se guardó, pero la matrícula no encaja con el tipo elegido.
                // Es un aviso, no un error: puede ser una variante legítima.
                msgDiv.textContent = `Registrada. Aviso: ${data.warning}`;
                msgDiv.style.color = "#fbbf24";
            } else {
                msgDiv.textContent = "Placa registrada correctamente.";
                msgDiv.style.color = "#4CAF50";
            }
            document.getElementById('alpr-register-form').reset();
            updatePlateTypeDescription();
        } else {
            msgDiv.textContent = data.message || "Error al registrar.";
            msgDiv.style.color = "#F44336";
        }
    } catch (err) {
        msgDiv.textContent = "Error al conectar con el servidor.";
        msgDiv.style.color = "#F44336";
    }
});

async function fetchWatchedPlates() {
    const res = await fetch('/api/watched_plates');
    const plates = await res.json();
    renderWatchedPlates(plates);
}

function renderWatchedPlates(plates) {
    const tbody = document.getElementById('alpr-watched-table-body');
    tbody.innerHTML = '';
    
    if (plates.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--text-muted);">No hay placas registradas</td></tr>';
        return;
    }

    plates.forEach(p => {
        const tr = document.createElement('tr');
        tr.dataset.id = p.id;
        // El tipo y el país se conservan al editar, aunque no se muestren como
        // campos editables en la fila.
        tr.dataset.plateType = p.plate_type || 'any';
        tr.dataset.country = p.country || '';

        const tipo = plateTypes.find(t => t.key === p.plate_type);
        const nombreTipo = tipo && tipo.key !== 'any' ? tipo.name : '—';

        tr.innerHTML = `
            <td class="alpr-pattern-cell"><strong>${p.plate_pattern}</strong></td>
            <td class="alpr-type-cell" style="color:var(--text-muted); font-size:0.9em;">${nombreTipo}</td>
            <td class="alpr-note-cell">${p.note || ''}</td>
            <td style="white-space:nowrap;">
                <button class="btn-edit" onclick="startAlprRename(${p.id}, this)">✏ Editar</button>
                <button class="btn-delete" onclick="deleteWatchedPlate(${p.id})">🗑</button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

function startAlprRename(id, btn) {
    const tr = btn.closest('tr');
    const patternCell = tr.querySelector('.alpr-pattern-cell');
    const typeCell = tr.querySelector('.alpr-type-cell');
    const noteCell = tr.querySelector('.alpr-note-cell');

    const currentPattern = patternCell.textContent;
    const currentNote = noteCell.textContent;
    const currentType = tr.dataset.plateType || 'any';

    patternCell.innerHTML = `<input type="text" class="edit-pattern" value="${currentPattern}"
        style="width:100%; box-sizing:border-box; padding:4px 8px; background:var(--bg-card);
        border:1px solid var(--primary-color); border-radius:4px; color:var(--text-color); font-size:0.95em; text-transform: uppercase;">`;

    // Desplegable con los tipos del país activo. Si la entrada se registró con
    // un tipo de otro país que ya no está en la lista, se añade igualmente para
    // no perderlo en silencio al editar cualquier otro campo.
    const opciones = plateTypes.map(t =>
        `<option value="${t.key}"${t.key === currentType ? ' selected' : ''}>${t.name}</option>`
    );
    if (!plateTypes.some(t => t.key === currentType)) {
        opciones.unshift(`<option value="${currentType}" selected>${currentType} (otro país)</option>`);
    }
    typeCell.innerHTML = `<select class="edit-type"
        style="width:100%; box-sizing:border-box; padding:4px 8px; background:var(--bg-card);
        border:1px solid var(--primary-color); border-radius:4px; color:var(--text-color); font-size:0.9em;">
        ${opciones.join('')}
    </select>`;

    noteCell.innerHTML = `<input type="text" class="edit-note" value="${currentNote}"
        style="width:100%; box-sizing:border-box; padding:4px 8px; background:var(--bg-card);
        border:1px solid var(--primary-color); border-radius:4px; color:var(--text-color); font-size:0.95em;">`;

    const input = patternCell.querySelector('input');
    input.focus();
    input.select();

    const actionsCell = btn.parentElement;
    actionsCell.innerHTML = `
        <button class="btn-edit" onclick="confirmAlprRename(${id}, this)">✔ Guardar</button>
        <button class="btn-delete" onclick="cancelAlprRename(${id})">✖ Cancelar</button>
    `;
}

async function confirmAlprRename(id, btn) {
    const tr = btn.closest('tr');
    const patternInput = tr.querySelector('.edit-pattern');
    const typeSelect = tr.querySelector('.edit-type');
    const noteInput = tr.querySelector('.edit-note');

    const newPattern = patternInput ? patternInput.value.trim().toUpperCase() : '';
    const newNote = noteInput ? noteInput.value.trim() : '';
    const newType = typeSelect ? typeSelect.value : (tr.dataset.plateType || 'any');
    if (!newPattern) return;

    // Las entradas anteriores a la columna de país no tienen ninguno guardado.
    // Al asignarles un tipo hay que fijar también el país activo, o el tipo no
    // podría validarse después.
    const country = tr.dataset.country || plateTypesCountry || '';

    const res = await fetch(`/api/watched_plates/${id}`, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            plate_pattern: newPattern, note: newNote,
            plate_type: newType, country: country
        })
    });
    const data = await res.json().catch(() => ({}));

    if (data.status === 'error') {
        alert(data.message || 'No se pudo guardar el cambio.');
        return;
    }
    if (data.warning) {
        showToast(`⚠ Guardado. ${data.warning}`);
    }
    fetchWatchedPlates();
}

function cancelAlprRename() {
    fetchWatchedPlates();
}

async function deleteWatchedPlate(id) {
    if (!confirm('¿Eliminar esta placa del registro?')) return;
    await fetch(`/api/watched_plates/${id}`, {method: 'DELETE'});
    fetchWatchedPlates();
}

document.getElementById('alpr-delete-all-btn').addEventListener('click', async () => {
    if (!confirm('¿Eliminar TODAS las placas registradas? Esta acción no se puede deshacer.')) return;
    await fetch('/api/watched_plates', {method: 'DELETE'});
    fetchWatchedPlates();
});

// Global polling for ALPR alerts
setInterval(async () => {
    try {
        const res = await fetch('/api/plate_alerts/latest?limit=1');
        const alerts = await res.json();
        if (alerts.length > 0) {
            const latest = alerts[0];
            if (lastAlprAlertTimestamp !== null && latest.timestamp !== lastAlprAlertTimestamp) {
                // New alert!
                const cam = cameras.find(c => c.source === latest.camera_id || c.id === latest.camera_id);
                const camName = cam ? cam.name : latest.camera_id;
                
                showToast(`🚨 Placa vigilada <strong>${latest.plate_text}</strong> detectada en ${camName}`);
                playAlertSound();
            }
            lastAlprAlertTimestamp = latest.timestamp;
        }
    } catch (e) {
        // ignore
    }
}, 2000);


// ===========================================================================
//  Sesión y usuarios
// ===========================================================================

// Cuando el servidor cierra la sesión por caducidad, cualquier llamada responde
// 401. Se intercepta fetch para llevar al usuario al inicio de sesión en cuanto
// ocurra, en lugar de dejar la interfaz fallando en silencio.
const _fetchOriginal = window.fetch;
window.fetch = async function (...args) {
    const res = await _fetchOriginal.apply(this, args);
    if (res.status === 401) {
        const data = await res.clone().json().catch(() => ({}));
        if (data.code === 'session_expired' || data.code === 'unauthenticated') {
            window.location.href = '/login';
        }
    }
    return res;
};

// Consulta periódica del estado de sesión. Sin esto, un puesto que quedara
// abierto sin streams no se enteraría de la caducidad hasta la siguiente acción.
function startSessionWatch() {
    setInterval(async () => {
        try {
            const res = await fetch('/api/session');
            if (!res.ok) return;          // El interceptor ya redirige si procede
            updateSessionStatus(await res.json());
        } catch (e) {
            // Corte de red momentáneo: se reintenta en el siguiente ciclo
        }
    }, 30000);
}

function updateSessionStatus(data) {
    const box = document.getElementById('session-status');
    if (!box) return;
    if (data.seconds_left === null) {
        box.textContent = 'La caducidad de sesión está desactivada.';
        return;
    }
    const min = Math.floor(data.seconds_left / 60);
    const seg = data.seconds_left % 60;
    box.textContent = `Sin visualizar cámaras desde hace ${data.idle_seconds} s. `
        + `La sesión se cerrará en ${min} min ${seg} s si no se abre ninguna.`;
}

// ---- Ajuste del tiempo de caducidad (solo administrador) ----

async function fetchSecuritySettings() {
    const res = await fetch('/api/security_settings');
    if (!res.ok) return;
    const data = await res.json();
    document.getElementById('session-timeout-range').value = data.session_timeout_minutes;
    renderTimeoutLabel(data.session_timeout_minutes);
}

function renderTimeoutLabel(min) {
    document.getElementById('session-timeout-value').textContent =
        Number(min) === 0 ? 'Desactivado' : `${min} min`;
}

async function saveSecuritySettings() {
    const minutes = parseInt(document.getElementById('session-timeout-range').value, 10);
    const res = await fetch('/api/security_settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_timeout_minutes: minutes })
    });
    const data = await res.json().catch(() => ({}));
    const msg = document.getElementById('session-settings-msg');

    if (res.ok) {
        msg.textContent = minutes === 0
            ? '✔ Caducidad de sesión desactivada'
            : `✔ La sesión se cerrará tras ${minutes} min sin visualizar cámaras`;
        msg.style.color = '#4ade80';
    } else {
        msg.textContent = `✖ ${data.message || 'No se pudo guardar'}`;
        msg.style.color = '#f87171';
    }
    msg.classList.add('visible');
    clearTimeout(saveSecuritySettings._t);
    saveSecuritySettings._t = setTimeout(() => msg.classList.remove('visible'), 2500);
}

// ---- Gestión de usuarios (solo administrador) ----

let currentUserId = null;
let usuariosCargados = [];

async function fetchUsers() {
    const res = await fetch('/api/users');
    if (!res.ok) return;
    const data = await res.json();
    currentUserId = data.current_user_id;
    usuariosCargados = data.users || [];
    renderUsers(data.users, data.roles, data.permissions || []);
}

function renderUsers(users, roles, permisos) {
    const tbody = document.getElementById('users-table-body');
    tbody.innerHTML = '';

    users.forEach(u => {
        const esUnoMismo = u.id === currentUserId;
        const esAdmin = u.role === 'admin';
        const opciones = roles.map(r =>
            `<option value="${r.key}"${r.key === u.role ? ' selected' : ''}>${r.label}</option>`
        ).join('');

        // El administrador los tiene todos por definición: se muestran marcados
        // y bloqueados en vez de ocultarlos, para que se vea qué alcance tiene
        // la cuenta sin dar a entender que se le puede recortar.
        const concedidos = new Set(u.permissions || []);
        const casillas = permisos.map(p => `
            <label style="display:flex; align-items:center; gap:6px; font-size:0.85em;
                          ${esAdmin ? 'opacity:0.55;' : 'cursor:pointer;'}"
                   title="${esAdmin ? 'El administrador tiene todos los permisos' : p.label}">
                <input type="checkbox" ${concedidos.has(p.key) ? 'checked' : ''}
                       ${esAdmin ? 'disabled' : ''}
                       onchange="changeUserPermission(${u.id}, '${p.key}', this.checked)">
                <span>${p.label}</span>
            </label>`).join('');

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${u.username}</strong>${esUnoMismo ? ' <span style="color:var(--text-muted); font-size:0.85em;">(tú)</span>' : ''}</td>
            <td>
                <select onchange="changeUserRole(${u.id}, this.value)"
                    style="padding:4px 8px; background:var(--bg-card); border:1px solid var(--border-color);
                           border-radius:4px; color:var(--text-color); font-size:0.9em;">
                    ${opciones}
                </select>
            </td>
            <td><div style="display:flex; flex-direction:column; gap:4px;">${casillas}</div></td>
            <td style="color:var(--text-muted); font-size:0.85em;">${(u.created_at || '').split('.')[0]}</td>
            <td style="text-align:right; white-space:nowrap;">
                <button class="btn-edit" title="Cambiar contraseña"
                        onclick="resetUserPassword(${u.id}, '${u.username}')">🔑</button>
                <button class="btn-delete" title="Eliminar usuario"
                        onclick="removeUser(${u.id}, '${u.username}')"
                        ${esUnoMismo ? 'disabled style="opacity:0.4; cursor:default;"' : ''}>🗑</button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

async function changeUserRole(id, role) {
    const res = await fetch(`/api/users/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role })
    });
    const data = await res.json().catch(() => ({}));

    if (!res.ok) {
        alert(data.message || 'No se pudo cambiar el rol.');
    } else if (id === currentUserId && role !== 'admin') {
        // El administrador se ha degradado a sí mismo: la interfaz que tiene
        // delante ya no corresponde a sus permisos, así que se recarga.
        alert('Has cambiado tu propio rol. Se recargará la interfaz.');
        window.location.reload();
        return;
    }
    fetchUsers();
}

async function changeUserPermission(id, permiso, concedido) {
    const usuario = usuariosCargados.find(u => u.id === id);
    if (!usuario) return;

    // El servidor espera la lista completa, no el cambio suelto: así una
    // petición define el estado final y no depende de en qué orden lleguen
    // varios clics seguidos.
    const permisos = new Set(usuario.permissions || []);
    concedido ? permisos.add(permiso) : permisos.delete(permiso);

    const res = await fetch(`/api/users/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ permissions: [...permisos] })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        alert(data.message || 'No se pudo cambiar el permiso.');
        fetchUsers();
        return;
    }
    usuario.permissions = [...permisos];
    showToast(`Permisos de ${usuario.username} actualizados`);
}

async function resetUserPassword(id, username) {
    const nueva = prompt(`Nueva contraseña para "${username}":`);
    if (!nueva) return;
    const res = await fetch(`/api/users/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: nueva })
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) showToast(`Contraseña actualizada para ${username}`);
    else alert(data.message || 'No se pudo cambiar la contraseña.');
}

async function removeUser(id, username) {
    if (!confirm(`¿Eliminar al usuario "${username}"?`)) return;
    const res = await fetch(`/api/users/${id}`, { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) alert(data.message || 'No se pudo eliminar.');
    fetchUsers();
}

// Estos controles solo existen en la interfaz de administrador
if (IS_ADMIN) {
    document.getElementById('user-create-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const msg = document.getElementById('user-create-msg');
        const res = await fetch('/api/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username: document.getElementById('user-new-name').value,
                password: document.getElementById('user-new-password').value,
                role: document.getElementById('user-new-role').value
            })
        });
        const data = await res.json().catch(() => ({}));

        if (res.ok) {
            msg.textContent = '✔ Usuario creado';
            msg.style.color = '#4ade80';
            document.getElementById('user-create-form').reset();
            fetchUsers();
        } else {
            msg.textContent = `✖ ${data.message || 'No se pudo crear'}`;
            msg.style.color = '#f87171';
        }
        msg.classList.add('visible');
        setTimeout(() => msg.classList.remove('visible'), 3000);
    });

    document.getElementById('session-timeout-range')
        .addEventListener('input', (e) => renderTimeoutLabel(e.target.value));
    document.getElementById('session-timeout-range')
        .addEventListener('change', saveSecuritySettings);
}


// ===========================================================================
//  Limpieza de detecciones y registro de auditoría
// ===========================================================================

/**
 * Pide confirmación indicando cuántos registros se van a borrar y lo ejecuta.
 * Se consulta el recuento antes de preguntar para que la confirmación diga una
 * cifra concreta en lugar de un genérico "¿seguro?".
 */
async function limpiarDetecciones(endpoint, clave, etiqueta, alRefrescar) {
    let cuantos = null;
    try {
        const res = await fetch('/api/detections/summary');
        if (res.ok) cuantos = (await res.json())[clave];
    } catch (e) { /* si falla el recuento se pregunta igual */ }

    if (cuantos === 0) {
        showToast(`No hay ${etiqueta} que borrar.`);
        return;
    }

    const cantidad = cuantos === null ? '' : ` (${cuantos})`;
    if (!confirm(`¿Borrar todo el historial de ${etiqueta}${cantidad}?\n\n`
               + 'Esta acción no se puede deshacer y queda anotada en el registro.')) {
        return;
    }

    const res = await fetch(endpoint, { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        showToast(`Se borraron ${data.deleted ?? 0} registros de ${etiqueta}.`);
        if (alRefrescar) alRefrescar();
    } else {
        alert(data.message || 'No se pudo borrar.');
    }
}

if (IS_ADMIN) {
    document.getElementById('alpr-clear-detections')
        .addEventListener('click', () => limpiarDetecciones(
            '/api/plates', 'plates', 'matrículas leídas', fetchLatestPlates));

    document.getElementById('fr-clear-detections')
        .addEventListener('click', () => limpiarDetecciones(
            '/api/faces/detections', 'faces', 'rostros detectados', fetchLatestFaces));
}

// ---- Registro de auditoría ----

let auditFiltroCargado = false;

async function fetchAuditLog() {
    const q = document.getElementById('audit-search').value.trim();
    const accion = document.getElementById('audit-action-filter').value;

    const params = new URLSearchParams({ limit: '200' });
    if (q) params.set('q', q);
    if (accion) params.set('action', accion);

    const res = await fetch(`/api/audit_log?${params}`);
    if (!res.ok) return;
    const data = await res.json();

    // El desplegable se llena una sola vez: recargarlo en cada búsqueda
    // perdería la selección del usuario mientras escribe.
    if (!auditFiltroCargado) {
        const sel = document.getElementById('audit-action-filter');
        (data.actions || []).forEach(a => {
            const opt = document.createElement('option');
            opt.value = a.key;
            opt.textContent = a.label;
            sel.appendChild(opt);
        });
        auditFiltroCargado = true;
    }

    document.getElementById('audit-count').textContent =
        `Mostrando ${data.entries.length} de ${data.total} entradas registradas.`;

    renderAuditLog(data.entries);
}

/**
 * Pasa la marca de tiempo de la base de datos a dd/mm/aaaa.
 *
 * En la base se guarda como aaaa-mm-dd porque así ordena correctamente al
 * comparar como texto; el cambio de formato es solo para mostrarlo. Se parte
 * la cadena en lugar de usar Date, que interpretaría la hora como UTC y
 * desplazaría la fecha según la zona horaria.
 */
function formatearFechaAuditoria(ts) {
    if (!ts) return '';
    const m = String(ts).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2}:\d{2})/);
    if (!m) return ts;
    const [, anio, mes, dia, hora] = m;
    return `${dia}/${mes}/${anio} ${hora}`;
}

function renderAuditLog(entradas) {
    const tbody = document.getElementById('audit-table-body');
    tbody.innerHTML = '';

    if (!entradas.length) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--text-muted);">'
                        + 'Sin entradas que coincidan</td></tr>';
        return;
    }

    entradas.forEach(e => {
        // Los detalles se guardan como JSON; se muestran en forma legible
        let detalle = e.target || '';
        if (e.details) {
            try {
                const d = JSON.parse(e.details);
                const partes = Object.entries(d)
                    .filter(([, v]) => v !== null && v !== undefined && v !== '')
                    .map(([k, v]) => `${k}: ${v}`);
                if (partes.length) {
                    detalle = detalle ? `${detalle} — ${partes.join(', ')}` : partes.join(', ');
                }
            } catch (err) {
                detalle = detalle ? `${detalle} — ${e.details}` : e.details;
            }
        }

        // Los accesos fallidos se destacan: son la señal que más importa revisar
        const fallo = e.action === 'session.login_failed';
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td style="white-space:nowrap; color:var(--text-muted); font-size:0.85em;">${formatearFechaAuditoria(e.timestamp)}</td>
            <td style="white-space:nowrap;"><strong>${e.username}</strong></td>
            <td style="${fallo ? 'color:#f87171; font-weight:600;' : ''}">${e.action_label}</td>
            <td style="color:var(--text-muted); font-size:0.9em;">${detalle}</td>
        `;
        tbody.appendChild(tr);
    });
}

if (IS_ADMIN) {
    // Buscar mientras se escribe, pero sin lanzar una consulta por tecla
    let auditTimer = null;
    document.getElementById('audit-search').addEventListener('input', () => {
        clearTimeout(auditTimer);
        auditTimer = setTimeout(fetchAuditLog, 300);
    });
    document.getElementById('audit-action-filter').addEventListener('change', fetchAuditLog);
}


// ===========================================================================
//  Servidor de grabaciones (NVR)
// ===========================================================================

function formatearBytes(n) {
    if (!n) return '—';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i >= 2 ? 1 : 0)} ${u[i]}`;
}

async function fetchNvrSettings() {
    const res = await fetch('/api/nvr/settings');
    if (!res.ok) return;
    const d = await res.json();
    document.getElementById('nvr-url').value = d.url || '';
    document.getElementById('nvr-enabled').checked = !!d.enabled;
    document.getElementById('nvr-token-state').textContent =
        d.has_token ? 'guardada' : 'sin configurar';
}

async function guardarNvrSettings() {
    const cuerpo = {
        url: document.getElementById('nvr-url').value.trim(),
        enabled: document.getElementById('nvr-enabled').checked,
        // Vacío significa "conserva la que hay", para poder cambiar solo la
        // dirección sin tener que volver a pegar la clave.
        token: document.getElementById('nvr-token').value.trim(),
    };
    const res = await fetch('/api/nvr/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cuerpo)
    });
    if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        mostrarEstadoNvr(false, d.message || 'No se pudo guardar');
        return;
    }
    // La clave ya está en el servidor: se limpia del formulario para no
    // dejarla visible en pantalla.
    document.getElementById('nvr-token').value = '';
    await fetchNvrSettings();
    await fetchNvrStatus();
    await fetchNvrCameras();
}

function mostrarEstadoNvr(ok, texto) {
    const el = document.getElementById('nvr-status');
    el.textContent = texto;
    el.style.color = ok ? '#4ade80' : '#f87171';
}

async function fetchNvrStatus() {
    mostrarEstadoNvr(true, 'Comprobando…');
    try {
        const d = await (await fetch('/api/nvr/status')).json();

        if (!d.connected) {
            mostrarEstadoNvr(false, `✖ ${d.message}`);
            return;
        }
        if (!d.authenticated) {
            // Distinguir esto de "no responde" importa: el problema y la
            // solución son completamente distintos.
            mostrarEstadoNvr(false, `✖ El servidor responde pero ${d.message}`);
            return;
        }

        const alm = d.storage || {};
        const grabando = (d.recorders || []).filter(r => r.recording).length;
        const libre = alm.disk_free_bytes
            ? `, ${formatearBytes(alm.disk_free_bytes)} libres en disco` : '';
        mostrarEstadoNvr(true,
            `✔ Conectado. ${grabando} cámara(s) grabando, `
            + `${formatearBytes(alm.total_bytes)} almacenados${libre}.`);

        // Un grabador que reintenta sin parar indica una cámara inalcanzable:
        // conviene verlo aquí y no solo en el registro del servidor.
        const fallando = (d.recorders || []).filter(r => !r.recording && r.retries > 0);
        if (fallando.length) {
            const el = document.getElementById('nvr-status');
            el.textContent += ` Con problemas: ${fallando.map(r => r.name).join(', ')}.`;
            el.style.color = '#fbbf24';
        }
    } catch (e) {
        mostrarEstadoNvr(false, '✖ No se pudo consultar el estado');
    }
}

let nvrCamaras = [];

async function fetchNvrCameras() {
    const res = await fetch('/api/nvr/cameras');
    if (!res.ok) return;
    const d = await res.json();
    nvrCamaras = d.cameras || [];
    renderNvrCameras(d.nvr_available, d.message);
}

function renderNvrCameras(disponible, mensaje) {
    const tbody = document.getElementById('nvr-cameras-body');
    tbody.innerHTML = '';

    if (!nvrCamaras.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted);">'
                        + 'No hay cámaras registradas</td></tr>';
        return;
    }

    nvrCamaras.forEach((c, i) => {
        const almacenado = c.days_recorded
            ? `${c.days_recorded} día(s) · ${formatearBytes(c.bytes)}`
              + (c.oldest_day ? `<br><span style="font-size:0.85em;">desde ${c.oldest_day}</span>` : '')
            : '<span style="color:var(--text-muted);">sin grabaciones</span>';

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td style="text-align:center;">
                <input type="checkbox" data-idx="${i}" class="nvr-rec-toggle"
                       ${c.recording ? 'checked' : ''} ${disponible ? '' : 'disabled'}>
            </td>
            <td><strong>${c.name}</strong></td>
            <td>
                <input type="number" min="1" max="365" value="${c.retention_days}"
                       data-idx="${i}" class="nvr-retention" ${disponible ? '' : 'disabled'}
                       style="width:70px; padding:5px 8px; background:var(--bg-card);
                              border:1px solid var(--border-color); border-radius:4px;
                              color:var(--text-color);">
            </td>
            <td style="color:var(--text-muted); font-size:0.9em;">${almacenado}</td>
            <td style="text-align:right;">
                <button class="btn-delete" title="Borrar las grabaciones de esta cámara"
                        onclick="borrarGrabaciones('${c.camera_id}', ${JSON.stringify(c.name).replace(/"/g, '&quot;')})"
                        ${c.days_recorded ? '' : 'disabled style="opacity:0.35; cursor:default;"'}>🗑</button>
            </td>
        `;
        tbody.appendChild(tr);
    });

    document.getElementById('nvr-save-cameras').disabled = !disponible;
    if (!disponible && mensaje) {
        const msg = document.getElementById('nvr-cameras-msg');
        msg.textContent = `Sin conexión con el servidor: ${mensaje}`;
        msg.style.color = '#f87171';
        msg.classList.add('visible');
    }
}

async function borrarGrabaciones(cameraId, nombre) {
    const cam = nvrCamaras.find(c => c.camera_id === cameraId);
    const cuanto = cam ? `${cam.days_recorded} día(s), ${formatearBytes(cam.bytes)}` : '';

    if (!confirm(`Se borrarán TODAS las grabaciones de "${nombre}"`
                 + (cuanto ? ` (${cuanto})` : '') + '.\n\n'
                 + 'La cámara sigue configurada y, si estaba grabando, continuará '
                 + 'haciéndolo. Esta acción no se puede deshacer.')) return;

    const msg = document.getElementById('nvr-cameras-msg');
    msg.textContent = `Borrando las grabaciones de ${nombre}…`;
    msg.style.color = 'var(--text-muted)';
    msg.classList.add('visible');

    const res = await fetch(`/api/nvr/cameras/${encodeURIComponent(cameraId)}/recordings`,
                            { method: 'DELETE' });
    const d = await res.json().catch(() => ({}));

    if (res.ok) {
        msg.textContent = `✔ Borradas las grabaciones de ${nombre}`
                        + ` (${d.days_deleted || 0} día(s), ${formatearBytes(d.bytes_freed || 0)} liberados).`;
        msg.style.color = '#4ade80';
        fetchNvrCameras();
        fetchNvrStatus();
    } else {
        msg.textContent = `✖ ${d.message || 'No se pudo borrar'}`;
        msg.style.color = '#f87171';
    }
    clearTimeout(borrarGrabaciones._t);
    borrarGrabaciones._t = setTimeout(() => msg.classList.remove('visible'), 6000);
}

async function guardarNvrCameras() {
    const seleccion = nvrCamaras.map((c, i) => {
        const chk = document.querySelector(`.nvr-rec-toggle[data-idx="${i}"]`);
        const dias = document.querySelector(`.nvr-retention[data-idx="${i}"]`);
        return {
            camera_id: c.camera_id,
            recording: chk ? chk.checked : false,
            retention_days: dias ? parseInt(dias.value, 10) || 3 : 3,
        };
    });

    const res = await fetch('/api/nvr/cameras', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cameras: seleccion })
    });
    const d = await res.json().catch(() => ({}));
    const msg = document.getElementById('nvr-cameras-msg');

    if (res.ok) {
        const n = seleccion.filter(c => c.recording).length;
        msg.textContent = `✔ Aplicado: ${n} cámara(s) en grabación.`;
        msg.style.color = '#4ade80';
        setTimeout(() => { fetchNvrCameras(); fetchNvrStatus(); }, 1500);
    } else {
        msg.textContent = `✖ ${d.message || 'No se pudo aplicar'}`;
        msg.style.color = '#f87171';
    }
    msg.classList.add('visible');
    clearTimeout(guardarNvrCameras._t);
    guardarNvrCameras._t = setTimeout(() => msg.classList.remove('visible'), 4000);
}

if (IS_ADMIN) {
    document.getElementById('nvr-save').addEventListener('click', guardarNvrSettings);
    document.getElementById('nvr-refresh').addEventListener('click', () => {
        fetchNvrStatus(); fetchNvrCameras();
    });
    document.getElementById('nvr-save-cameras').addEventListener('click', guardarNvrCameras);
}


// ===========================================================================
//  Reproductor de grabaciones
// ===========================================================================
//
// El vídeo está troceado en segmentos de varios minutos, pero el usuario
// razona en horas del día, no en archivos. Todo aquí traduce entre ambas
// cosas: la línea de tiempo, el reloj y los saltos trabajan con la hora
// absoluta, y se resuelve por debajo qué segmento la contiene y en qué
// posición dentro de él.

const PB = {
    camara: null,
    fecha: null,           // 'aaaa-mm-dd'
    segmentos: [],
    tramos: [],
    indiceActual: -1,
    velocidad: 1,
    rebobinando: null,     // identificador del temporizador de rebobinado
    arrastrando: false,
    camarasConGrabacion: [],
    // Ventana visible de la línea de tiempo: cuántos segundos abarca y
    // en qué momento del día está centrada.
    zoom: { span: 86400, centro: 43200 },
};

const PB_SEGUNDOS_DIA = 24 * 3600;

// ---- Fechas y horas -------------------------------------------------------
//
// Se parte la cadena en vez de usar new Date(texto): el constructor
// interpreta "aaaa-mm-dd hh:mm:ss" de forma inconsistente entre navegadores y
// puede aplicar UTC, desplazando la hora que se muestra.

function pbParsear(texto) {
    if (!texto) return null;
    const m = String(texto).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/);
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
}

function pbFormatearFechaHora(d) {
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} `
         + `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function pbHoraDelDia(d) {
    const p = n => String(n).padStart(2, '0');
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** Segundos transcurridos desde medianoche. Es la unidad de la línea de tiempo. */
function pbSegundosDesdeMedianoche(d) {
    return d.getHours() * 3600 + d.getMinutes() * 60 + d.getSeconds();
}

// ---- Estado del reproductor ----------------------------------------------

function pbVideo() { return document.getElementById('playback-video'); }

function pbMostrarCargando(visible) {
    document.getElementById('playback-loading').classList.toggle('visible', !!visible);
}

function pbInfo(texto) {
    document.getElementById('playback-info').textContent = texto || '';
}

/** Instante absoluto que se está viendo, o null si no hay nada cargado. */
function pbInstanteActual() {
    const seg = PB.segmentos[PB.indiceActual];
    if (!seg) return null;
    const inicio = pbParsear(seg.started_at);
    if (!inicio) return null;
    return new Date(inicio.getTime() + pbVideo().currentTime * 1000);
}

// ---- Lista de cámaras -----------------------------------------------------

async function pbCargarCamaras() {
    try {
        const d = await (await fetch('/api/nvr/cameras')).json();
        PB.camarasConGrabacion = d.cameras || [];
        if (!d.nvr_available) {
            document.getElementById('playback-cam-list').innerHTML =
                `<li class="playback-hint" style="padding:10px;">Sin conexión con el
                 servidor de grabaciones.<br>${d.message || ''}</li>`;
            return;
        }
    } catch (e) {
        PB.camarasConGrabacion = [];
    }
    pbRenderCamaras();
}

function pbRenderCamaras() {
    const filtro = document.getElementById('playback-cam-search').value.trim().toLowerCase();
    const lista = document.getElementById('playback-cam-list');
    lista.innerHTML = '';

    const visibles = PB.camarasConGrabacion.filter(
        c => !filtro || (c.name || '').toLowerCase().includes(filtro));

    if (!visibles.length) {
        lista.innerHTML = `<li class="playback-hint" style="padding:10px;">${
            filtro ? 'Ninguna cámara coincide' : 'No hay cámaras'}</li>`;
        return;
    }

    visibles.forEach(c => {
        const tiene = c.days_recorded > 0;
        const li = document.createElement('li');
        li.className = 'playback-cam'
            + (tiene ? '' : ' sin-grabacion')
            + (PB.camara && PB.camara.camera_id === c.camera_id ? ' active' : '');
        li.draggable = tiene;
        li.innerHTML = `
            <span class="cam-status-dot ${c.recording ? 'online' : 'offline'}"></span>
            <span class="playback-cam-name">${c.name}</span>
            <span class="playback-cam-days">${tiene ? c.days_recorded + 'd' : '—'}</span>`;

        if (tiene) {
            li.title = `${c.days_recorded} día(s) grabados`
                     + (c.oldest_day ? `, desde ${c.oldest_day}` : '');
            li.addEventListener('dragstart',
                e => e.dataTransfer.setData('text/plain', JSON.stringify(c)));
            li.addEventListener('dblclick', () => pbSeleccionarCamara(c));
        } else {
            li.title = 'Esta cámara no tiene grabaciones';
        }
        lista.appendChild(li);
    });
}

// ---- Selección de cámara y día -------------------------------------------

async function pbSeleccionarCamara(camara) {
    PB.camara = camara;
    pbRenderCamaras();
    pbInfo(camara.name);

    // Se abre por el día más reciente con grabación, que es lo que casi
    // siempre se quiere ver al entrar.
    let dia = camara.newest_day;
    try {
        const d = await (await fetch(
            `/api/nvr/recordings/days?camera_id=${encodeURIComponent(camara.camera_id)}`)).json();
        if (d.days && d.days.length) dia = d.days[0].day;
    } catch (e) { /* se usa newest_day */ }

    if (!dia) {
        pbInfo(`${camara.name} — sin grabaciones`);
        return;
    }
    document.getElementById('playback-date').value = dia;
    await pbCargarDia(dia, true);
}

async function pbCargarDia(dia, irAlFinal = false) {
    if (!PB.camara) return;
    PB.fecha = dia;
    // Cada día se abre completo: conservar el zoom del día anterior
    // dejaría al usuario mirando un tramo arbitrario sin grabación.
    PB.zoom = { span: 86400, centro: 43200 };
    pbMostrarCargando(true);

    try {
        const url = `/api/nvr/recordings/segments?camera_id=${
            encodeURIComponent(PB.camara.camera_id)}&day=${encodeURIComponent(dia)}`;
        const d = await (await fetch(url)).json();
        PB.segmentos = d.segments || [];
        PB.tramos = d.ranges || [];
    } catch (e) {
        PB.segmentos = []; PB.tramos = [];
    }

    pbDibujarLineaDeTiempo();
    pbMostrarCargando(false);

    if (!PB.segmentos.length) {
        pbInfo(`${PB.camara.name} — sin grabaciones el ${dia}`);
        pbVideo().classList.remove('visible');
        document.getElementById('playback-placeholder').style.display = 'block';
        return;
    }

    await pbCargarSegmento(irAlFinal ? PB.segmentos.length - 1 : 0, 0, false);
}

// ---- Carga y encadenado de segmentos -------------------------------------

async function pbCargarSegmento(indice, desplazamiento = 0, reproducir = true) {
    if (indice < 0 || indice >= PB.segmentos.length) return false;

    const seg = PB.segmentos[indice];
    PB.indiceActual = indice;
    const video = pbVideo();

    document.getElementById('playback-placeholder').style.display = 'none';
    video.classList.add('visible');
    pbMostrarCargando(true);

    video.src = `/api/nvr/segment/${seg.id}`;
    video.playbackRate = PB.velocidad;

    await new Promise(resolve => {
        const listo = () => { limpiar(); resolve(); };
        const fallo = () => { limpiar(); resolve(); };
        const limpiar = () => {
            video.removeEventListener('loadedmetadata', listo);
            video.removeEventListener('error', fallo);
        };
        video.addEventListener('loadedmetadata', listo, { once: true });
        video.addEventListener('error', fallo, { once: true });
        // Si el segmento no carga, no dejar el reproductor colgado para siempre
        setTimeout(listo, 8000);
    });

    if (desplazamiento > 0 && isFinite(video.duration)) {
        video.currentTime = Math.min(desplazamiento, Math.max(0, video.duration - 0.1));
    }
    pbMostrarCargando(false);

    if (reproducir) { try { await video.play(); } catch (e) { /* el navegador puede bloquearlo */ } }
    pbActualizarBotones();
    pbActualizarReloj();
    return true;
}

/**
 * Al terminar un segmento se encadena el siguiente automáticamente.
 * Sin esto, la reproducción se detendría cada pocos minutos en cada corte de
 * archivo, que es un detalle interno que el usuario no tiene por qué notar.
 */
async function pbSegmentoTerminado() {
    if (PB.indiceActual + 1 < PB.segmentos.length) {
        await pbCargarSegmento(PB.indiceActual + 1, 0, true);
    } else {
        pbPausar();
        pbInfo(`${PB.camara.name} — fin de las grabaciones del día`);
    }
}

// ---- Controles ------------------------------------------------------------

function pbReproduciendo() {
    const v = pbVideo();
    return !v.paused && !v.ended && v.readyState > 2;
}

async function pbAlternarReproduccion() {
    const v = pbVideo();
    if (!v.src) return;
    pbDetenerRebobinado();
    if (v.paused) { try { await v.play(); } catch (e) {} }
    else v.pause();
    pbActualizarBotones();
}

function pbPausar() {
    pbDetenerRebobinado();
    pbVideo().pause();
    pbActualizarBotones();
}

function pbFijarVelocidad(v) {
    PB.velocidad = v;
    pbVideo().playbackRate = v;
    document.querySelectorAll('.pb-speed').forEach(b =>
        b.classList.toggle('active', Number(b.dataset.speed) === v));
    // El rebobinado usa la velocidad como paso, así que se reinicia para que
    // el cambio se note de inmediato.
    if (PB.rebobinando) { pbDetenerRebobinado(); pbRebobinar(); }
}

/**
 * Rebobinado.
 *
 * El vídeo HTML no reproduce hacia atrás: playbackRate negativo no está
 * soportado. Se emula retrocediendo la posición a intervalos regulares, y al
 * llegar al principio del segmento se salta al final del anterior.
 */
function pbRebobinar() {
    if (PB.rebobinando) { pbDetenerRebobinado(); return; }
    const v = pbVideo();
    if (!v.src) return;

    v.pause();
    const paso = 0.25;                    // segundos de reloj entre saltos
    PB.rebobinando = setInterval(async () => {
        const salto = paso * PB.velocidad;
        if (v.currentTime - salto > 0.1) {
            v.currentTime -= salto;
        } else if (PB.indiceActual > 0) {
            pbDetenerRebobinado();
            await pbCargarSegmento(PB.indiceActual - 1, 1e6, false);
            const vv = pbVideo();
            if (isFinite(vv.duration)) vv.currentTime = Math.max(0, vv.duration - 0.2);
            pbRebobinar();
        } else {
            pbDetenerRebobinado();
        }
        pbActualizarReloj();
    }, paso * 1000);

    document.getElementById('pb-rewind').classList.add('activo');
    pbActualizarBotones();
}

function pbDetenerRebobinado() {
    if (PB.rebobinando) {
        clearInterval(PB.rebobinando);
        PB.rebobinando = null;
    }
    document.getElementById('pb-rewind').classList.remove('activo');
}

function pbSaltar(segundos) {
    const v = pbVideo();
    if (!v.src) return;
    const destino = v.currentTime + segundos;

    if (destino < 0 && PB.indiceActual > 0) {
        // Se sale por delante del segmento: continúa en el anterior
        pbCargarSegmento(PB.indiceActual - 1, 1e6, pbReproduciendo());
    } else if (isFinite(v.duration) && destino > v.duration
               && PB.indiceActual + 1 < PB.segmentos.length) {
        pbCargarSegmento(PB.indiceActual + 1, destino - v.duration, pbReproduciendo());
    } else {
        v.currentTime = Math.max(0, Math.min(destino, v.duration || 0));
    }
    pbActualizarReloj();
}

function pbActualizarBotones() {
    const v = pbVideo();
    const hay = !!v.src;
    document.getElementById('pb-play').textContent =
        (pbReproduciendo() || PB.rebobinando) ? '⏸' : '▶';
    ['pb-play', 'pb-back10', 'pb-fwd10', 'pb-rewind', 'pb-fullscreen']
        .forEach(id => { document.getElementById(id).disabled = !hay; });
    document.getElementById('pb-prev').disabled = PB.indiceActual <= 0;
    document.getElementById('pb-next').disabled =
        PB.indiceActual < 0 || PB.indiceActual >= PB.segmentos.length - 1;
}

function pbActualizarReloj() {
    const t = pbInstanteActual();
    document.getElementById('pb-clock').textContent = t ? pbHoraDelDia(t) : '--:--:--';
    pbSeguirCabezal();
    pbActualizarCabezal();
}

// ---- Línea de tiempo ------------------------------------------------------

// ---- Zoom de la línea de tiempo ------------------------------------------
//
// La línea muestra una ventana del día, no siempre las 24 horas. Con
// grabaciones de días enteros, un día completo en unos cientos de píxeles hace
// imposible situarse con precisión: cada píxel son casi dos minutos.
//
// Niveles pensados para bajar hasta el minuto sin pasos bruscos.
const PB_NIVELES_ZOOM = [86400, 43200, 21600, 10800, 3600, 1800, 900, 300, 120];

function pbNivelZoom() {
    // Se devuelve el índice del nivel más cercano al tramo visible actual
    let mejor = 0, dif = Infinity;
    PB_NIVELES_ZOOM.forEach((s, i) => {
        const d = Math.abs(s - PB.zoom.span);
        if (d < dif) { dif = d; mejor = i; }
    });
    return mejor;
}

/** Tramo del día que se está mostrando, en segundos desde medianoche. */
function pbVentana() {
    const span = Math.max(60, Math.min(PB_SEGUNDOS_DIA, PB.zoom.span));
    let inicio = PB.zoom.centro - span / 2;
    // Sujetar a los límites del día: sin esto se podría desplazar la ventana
    // fuera del día y quedarse mirando una franja vacía.
    inicio = Math.max(0, Math.min(inicio, PB_SEGUNDOS_DIA - span));
    return { inicio, fin: inicio + span, span };
}

function pbFormatearSpan(s) {
    if (s >= 3600) {
        const h = s / 3600;
        return `${Number.isInteger(h) ? h : h.toFixed(1)} h`;
    }
    return `${Math.round(s / 60)} min`;
}

function pbAplicarZoom(indice, centroDeseado = null) {
    indice = Math.max(0, Math.min(PB_NIVELES_ZOOM.length - 1, indice));
    PB.zoom.span = PB_NIVELES_ZOOM[indice];

    if (centroDeseado !== null) {
        PB.zoom.centro = centroDeseado;
    } else {
        // Al ampliar sin punto de referencia se centra en lo que se está
        // viendo, que es lo que el usuario quiere mirar de cerca.
        const t = pbInstanteActual();
        if (t) PB.zoom.centro = pbSegundosDesdeMedianoche(t);
    }

    pbDibujarLineaDeTiempo();
    pbActualizarControlesZoom();
}

/**
 * Mueve la ventana visible sin salirse del día.
 *
 * Se sujeta el centro, no solo el dibujo: si se dejara crecer sin límite, al
 * arrastrar más allá del final del día habría que desandar todo ese exceso
 * antes de que la línea volviera a moverse, y daría la sensación de haberse
 * quedado enganchada.
 */
function pbDesplazarVentana(centro) {
    const span = Math.max(60, Math.min(PB_SEGUNDOS_DIA, PB.zoom.span));
    const medio = span / 2;
    const nuevo = Math.max(medio, Math.min(PB_SEGUNDOS_DIA - medio, centro));
    if (nuevo === PB.zoom.centro) return;
    PB.zoom.centro = nuevo;
    pbRedibujarEnElSiguienteCuadro();
}

/**
 * Redibuja como mucho una vez por cuadro.
 *
 * Dibujar la línea de tiempo reconstruye los tramos, las marcas horarias y la
 * miniatura. Hacerlo en cada mousemove —que llegan más rápido que los cuadros—
 * multiplica ese trabajo sin que se vea ninguna diferencia.
 */
let pbRedibujadoPendiente = null;
function pbRedibujarEnElSiguienteCuadro() {
    if (pbRedibujadoPendiente !== null) return;
    pbRedibujadoPendiente = requestAnimationFrame(() => {
        pbRedibujadoPendiente = null;
        pbDibujarLineaDeTiempo();
    });
}

/**
 * Arrastrar la franja de horas para recorrer el día.
 *
 * Al ampliar, la línea muestra una porción pequeña y hacía falta el ratón sobre
 * la miniatura, o varios clics, para llegar a otro momento. Aquí se agarra la
 * escala y se tira de ella.
 *
 * Va en la franja de horas y no en la pista: allí el arrastre ya mueve el
 * cabezal de reproducción, y en modo recorte marca un tramo. Son tres gestos
 * distintos que no pueden compartir el mismo sitio.
 *
 * Se usan eventos de puntero en lugar de ratón, a diferencia del resto del
 * reproductor, por dos motivos: setPointerCapture mantiene el arrastre aunque
 * el puntero se salga de la franja, que es estrecha y se abandona enseguida, y
 * de paso funciona con pantallas táctiles.
 */
function pbActivarArrastreDeEscala() {
    const horas = document.getElementById('pb-hours');
    const linea = document.getElementById('pb-timeline');
    let arrastre = null;

    const sePuedeDesplazar = () => PB.zoom.span < PB_SEGUNDOS_DIA;

    horas.addEventListener('pointerdown', e => {
        // Con el día entero a la vista no hay a dónde desplazarse
        if (!sePuedeDesplazar() || e.button !== 0) return;
        // Sin esto, arrastrar selecciona el texto de las etiquetas
        e.preventDefault();
        horas.setPointerCapture(e.pointerId);
        arrastre = { x: e.clientX, centro: PB.zoom.centro };
        horas.classList.add('pb-hours-arrastrando');
    });

    horas.addEventListener('pointermove', e => {
        if (!arrastre) return;
        // La equivalencia píxel-segundo se toma de la pista, no de la franja:
        // así la escala acompaña exactamente al contenido que hay encima.
        const ancho = linea.getBoundingClientRect().width || 1;
        const segundosPorPixel = PB.zoom.span / ancho;
        // Se resta: al tirar hacia la derecha el contenido acompaña a la mano,
        // de modo que aparece lo anterior, como al desplazar un mapa.
        pbDesplazarVentana(arrastre.centro - (e.clientX - arrastre.x) * segundosPorPixel);
    });

    const soltar = e => {
        if (!arrastre) return;
        arrastre = null;
        horas.classList.remove('pb-hours-arrastrando');
        if (horas.hasPointerCapture(e.pointerId)) horas.releasePointerCapture(e.pointerId);
    };
    horas.addEventListener('pointerup', soltar);
    horas.addEventListener('pointercancel', soltar);
}

function pbActualizarControlesZoom() {
    const i = pbNivelZoom();
    document.getElementById('pb-zoom-label').textContent =
        pbFormatearSpan(PB.zoom.span);
    document.getElementById('pb-zoom-out').disabled = i <= 0;
    document.getElementById('pb-zoom-in').disabled = i >= PB_NIVELES_ZOOM.length - 1;
    // La miniatura solo aporta cuando no se ve el día entero
    const ampliada = PB.zoom.span < PB_SEGUNDOS_DIA;
    document.getElementById('pb-minimap').classList.toggle('visible', ampliada);

    // Y con el día entero a la vista tampoco hay a dónde desplazarse, así que
    // la escala deja de anunciarse como arrastrable.
    document.getElementById('pb-hours')
        .classList.toggle('pb-hours-desplazable', ampliada);
}

/**
 * Mantiene el cabezal dentro de la ventana mientras se reproduce.
 *
 * Ampliada la línea, el cabezal se saldría por la derecha en segundos y habría
 * que reencuadrar a mano constantemente.
 */
function pbSeguirCabezal() {
    if (PB.zoom.span >= PB_SEGUNDOS_DIA || PB.arrastrando) return;
    const t = pbInstanteActual();
    if (!t) return;

    const s = pbSegundosDesdeMedianoche(t);
    const v = pbVentana();
    const margen = v.span * 0.1;
    if (s < v.inicio + margen || s > v.fin - margen) {
        PB.zoom.centro = s;
        pbDibujarLineaDeTiempo();
    }
}

function pbDibujarLineaDeTiempo() {
    const v = pbVentana();
    const track = document.getElementById('pb-timeline-track');
    track.innerHTML = '';

    PB.tramos.forEach(r => {
        const ini = pbParsear(r.start), fin = pbParsear(r.end);
        if (!ini || !fin) return;
        const a = pbSegundosDesdeMedianoche(ini);
        const b = Math.max(a + 1, pbSegundosDesdeMedianoche(fin));
        // Recortar a la ventana visible; los tramos fuera no se dibujan
        const va = Math.max(a, v.inicio), vb = Math.min(b, v.fin);
        if (vb <= va) return;

        const div = document.createElement('div');
        div.className = 'pb-range';
        div.style.left = `${((va - v.inicio) / v.span) * 100}%`;
        div.style.width = `${((vb - va) / v.span) * 100}%`;
        div.title = `${pbHoraDelDia(ini)} — ${pbHoraDelDia(fin)}`;
        track.appendChild(div);
    });

    pbDibujarMarcasHorarias(v);
    pbDibujarMiniatura(v);
    pbActualizarCabezal();
    // La selección se dibuja en coordenadas de la ventana, así que hay
    // que rehacerla cada vez que la ventana cambia.
    if (typeof pbDibujarSeleccion === 'function') pbDibujarSeleccion();
}

/**
 * Marcas de tiempo adaptadas al zoom.
 *
 * Un intervalo fijo deja de servir al ampliar: con 2 minutos a la vista,
 * marcas cada 3 horas no muestran ninguna.
 */
function pbDibujarMarcasHorarias(v) {
    const contenedor = document.getElementById('pb-hours');
    contenedor.innerHTML = '';

    // Se busca el intervalo más amplio que aún produzca al menos 4 marcas.
    // Elegir sin más el mayor que "quepa" dejaba la escala vacía al ampliar:
    // con una hora a la vista y marcas cada tres, no caía ninguna dentro.
    const pasos = [10800, 3600, 1800, 900, 300, 120, 60, 30, 10, 5];
    const paso = pasos.find(p => v.span / p >= 4) || 5;

    const p = n => String(n).padStart(2, '0');
    const primera = Math.ceil(v.inicio / paso) * paso;

    for (let s = primera; s <= v.fin; s += paso) {
        const span = document.createElement('span');
        span.textContent = paso >= 3600
            ? `${p(Math.floor(s / 3600))}:00`
            : `${p(Math.floor(s / 3600))}:${p(Math.floor(s / 60) % 60)}`
              + (paso < 60 ? `:${p(s % 60)}` : '');
        contenedor.appendChild(span);
    }
}

/** Franja con el día completo y un recuadro sobre el tramo ampliado. */
function pbDibujarMiniatura(v) {
    const track = document.getElementById('pb-minimap-track');
    track.innerHTML = '';

    PB.tramos.forEach(r => {
        const ini = pbParsear(r.start), fin = pbParsear(r.end);
        if (!ini || !fin) return;
        const a = pbSegundosDesdeMedianoche(ini);
        const b = Math.max(a + 1, pbSegundosDesdeMedianoche(fin));
        const div = document.createElement('div');
        div.className = 'pb-minimap-range';
        div.style.left = `${(a / PB_SEGUNDOS_DIA) * 100}%`;
        div.style.width = `${((b - a) / PB_SEGUNDOS_DIA) * 100}%`;
        track.appendChild(div);
    });

    const ventana = document.getElementById('pb-minimap-window');
    ventana.style.left = `${(v.inicio / PB_SEGUNDOS_DIA) * 100}%`;
    ventana.style.width = `${(v.span / PB_SEGUNDOS_DIA) * 100}%`;
}

function pbActualizarCabezal() {
    const cabezal = document.getElementById('pb-playhead');
    const t = pbInstanteActual();
    if (!t) { cabezal.classList.remove('visible'); return; }

    const v = pbVentana();
    const s = pbSegundosDesdeMedianoche(t);
    // Fuera de la ventana no se dibuja, en lugar de pegarlo a un extremo y
    // dar a entender que está ahí.
    if (s < v.inicio || s > v.fin) { cabezal.classList.remove('visible'); return; }

    cabezal.classList.add('visible');
    cabezal.style.left = `${((s - v.inicio) / v.span) * 100}%`;
}

/** Hora del día correspondiente a una posición horizontal de la línea. */
function pbHoraEnPosicion(clientX) {
    const linea = document.getElementById('pb-timeline');
    const r = linea.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
    const v = pbVentana();
    const segundos = Math.floor(v.inicio + frac * v.span);
    const p = n => String(n).padStart(2, '0');
    return { segundos,
             texto: `${p(Math.floor(segundos / 3600))}:${p(Math.floor(segundos / 60) % 60)}:${p(segundos % 60)}` };
}

async function pbIrAHoraDelDia(segundos) {
    if (!PB.camara || !PB.fecha) return;
    const p = n => String(n).padStart(2, '0');
    const momento = `${PB.fecha} ${p(Math.floor(segundos / 3600))}:`
                  + `${p(Math.floor(segundos / 60) % 60)}:${p(segundos % 60)}`;
    await pbIrAInstante(momento);
}

async function pbIrAInstante(momento) {
    if (!PB.camara) return;
    pbMostrarCargando(true);
    try {
        const url = `/api/nvr/recordings/at?camera_id=${
            encodeURIComponent(PB.camara.camera_id)}&at=${encodeURIComponent(momento)}`;
        const d = await (await fetch(url)).json();

        if (!d.segment) {
            pbMostrarCargando(false);
            showToast('No hay grabación en ese momento.');
            return;
        }

        // El instante puede caer en otro día del que está cargado
        const diaDestino = d.segment.day;
        if (diaDestino !== PB.fecha) {
            document.getElementById('playback-date').value = diaDestino;
            PB.fecha = diaDestino;
            const su = `/api/nvr/recordings/segments?camera_id=${
                encodeURIComponent(PB.camara.camera_id)}&day=${encodeURIComponent(diaDestino)}`;
            const sd = await (await fetch(su)).json();
            PB.segmentos = sd.segments || [];
            PB.tramos = sd.ranges || [];
            pbDibujarLineaDeTiempo();
        }

        const idx = PB.segmentos.findIndex(s => s.id === d.segment.id);
        if (idx >= 0) await pbCargarSegmento(idx, d.offset || 0, pbReproduciendo());
    } catch (e) {
        showToast('No se pudo saltar a ese momento.');
    }
    pbMostrarCargando(false);
}

// ---- Enlazado de la interfaz ---------------------------------------------

(function pbInicializar() {
    const overlay = document.getElementById('playback-overlay');
    const video = pbVideo();
    const linea = document.getElementById('pb-timeline');
    const zona = document.getElementById('playback-drop');

    document.getElementById('open-playback-btn').addEventListener('click', () => {
        overlay.classList.add('active');
        pbCargarCamaras();
        pbActualizarControlesZoom();
    });

    document.getElementById('playback-close').addEventListener('click', () => {
        // Detener la descarga al cerrar: si no, el vídeo sigue bajando datos
        // del servidor con el panel oculto.
        pbPausar();
        video.removeAttribute('src');
        video.load();
        overlay.classList.remove('active');
    });

    document.getElementById('playback-cam-search')
        .addEventListener('input', pbRenderCamaras);

    // Arrastrar y soltar una cámara sobre el reproductor
    zona.addEventListener('dragover', e => {
        e.preventDefault();
        zona.classList.add('drag-over');
    });
    zona.addEventListener('dragleave', () => zona.classList.remove('drag-over'));
    zona.addEventListener('drop', e => {
        e.preventDefault();
        zona.classList.remove('drag-over');
        try {
            pbSeleccionarCamara(JSON.parse(e.dataTransfer.getData('text/plain')));
        } catch (err) { /* arrastre de otra cosa */ }
    });

    // Controles
    document.getElementById('pb-play').addEventListener('click', pbAlternarReproduccion);
    document.getElementById('pb-rewind').addEventListener('click', pbRebobinar);
    document.getElementById('pb-back10').addEventListener('click', () => pbSaltar(-10));
    document.getElementById('pb-fwd10').addEventListener('click', () => pbSaltar(10));
    document.getElementById('pb-prev').addEventListener('click',
        () => pbCargarSegmento(PB.indiceActual - 1, 0, pbReproduciendo()));
    document.getElementById('pb-next').addEventListener('click',
        () => pbCargarSegmento(PB.indiceActual + 1, 0, pbReproduciendo()));

    document.querySelectorAll('.pb-speed').forEach(b =>
        b.addEventListener('click', () => pbFijarVelocidad(Number(b.dataset.speed))));

    document.getElementById('pb-live').addEventListener('click', async () => {
        if (!PB.camara) return;
        await pbSeleccionarCamara(PB.camara);
        if (PB.segmentos.length) {
            await pbCargarSegmento(PB.segmentos.length - 1, 1e6, false);
        }
    });

    document.getElementById('pb-fullscreen').addEventListener('click', () => {
        const el = document.getElementById('playback-drop');
        if (document.fullscreenElement) document.exitFullscreen();
        else el.requestFullscreen?.();
    });

    // Fecha y hora
    document.getElementById('playback-date').addEventListener('change', e => {
        if (e.target.value) pbCargarDia(e.target.value, false);
    });
    document.getElementById('playback-goto').addEventListener('click', () => {
        const f = document.getElementById('playback-date').value;
        const h = document.getElementById('playback-time').value || '00:00:00';
        if (f) pbIrAInstante(`${f} ${h.length === 5 ? h + ':00' : h}`);
    });

    // Línea de tiempo: clic, arrastre y hora bajo el cursor
    const hover = document.getElementById('pb-hover-time');

    linea.addEventListener('mousemove', e => {
        const { texto } = pbHoraEnPosicion(e.clientX);
        const r = linea.getBoundingClientRect();
        hover.style.display = 'block';
        hover.style.left = `${e.clientX - r.left}px`;
        hover.textContent = texto;
        if (PB.arrastrando) pbActualizarCabezalTemporal(e.clientX);
    });
    linea.addEventListener('mouseleave', () => { hover.style.display = 'none'; });

    linea.addEventListener('mousedown', e => {
        // En modo de marcado, arrastrar define un tramo en vez de saltar
        if (PB.seleccion && PB.seleccion.modo) return;
        PB.arrastrando = true;
        pbActualizarCabezalTemporal(e.clientX);
    });

    // El soltar se escucha en el documento: al arrastrar rápido el puntero
    // suele salirse de la barra antes de levantar el botón.
    document.addEventListener('mouseup', e => {
        if (!PB.arrastrando) return;
        PB.arrastrando = false;
        const { segundos } = pbHoraEnPosicion(e.clientX);
        pbIrAHoraDelDia(segundos);
    });

    // --- Zoom de la línea de tiempo ---
    document.getElementById('pb-zoom-in').addEventListener('click',
        () => pbAplicarZoom(pbNivelZoom() + 1));
    document.getElementById('pb-zoom-out').addEventListener('click',
        () => pbAplicarZoom(pbNivelZoom() - 1));
    document.getElementById('pb-zoom-fit').addEventListener('click',
        () => pbAplicarZoom(0, PB_SEGUNDOS_DIA / 2));

    // Rueda del ratón: se amplía manteniendo fijo el instante bajo el cursor,
    // que es como se espera que funcione un zoom sobre una gráfica.
    linea.addEventListener('wheel', e => {
        e.preventDefault();
        const { segundos } = pbHoraEnPosicion(e.clientX);

        const nivel = Math.max(0, Math.min(PB_NIVELES_ZOOM.length - 1,
                                           pbNivelZoom() + (e.deltaY < 0 ? 1 : -1)));
        const nuevoSpan = PB_NIVELES_ZOOM[nivel];

        // Centrar en el cursor NO basta: desplazaría ese instante al medio de
        // la pantalla. Para dejarlo donde está hay que conservar su posición
        // relativa dentro de la ventana.
        const r = linea.getBoundingClientRect();
        const frac = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
        const centro = segundos + nuevoSpan * (0.5 - frac);

        pbAplicarZoom(nivel, centro);
    }, { passive: false });

    // Clic en la miniatura: desplaza la ventana ampliada a esa zona del día
    const mini = document.getElementById('pb-minimap');
    mini.addEventListener('click', e => {
        const r = mini.getBoundingClientRect();
        const frac = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
        PB.zoom.centro = frac * PB_SEGUNDOS_DIA;
        pbDibujarLineaDeTiempo();
    });

    // Arrastrar la escala de horas para recorrer el día
    pbActivarArrastreDeEscala();

    // Eventos del vídeo
    video.addEventListener('ended', pbSegmentoTerminado);
    video.addEventListener('timeupdate', pbActualizarReloj);
    video.addEventListener('play', pbActualizarBotones);
    video.addEventListener('pause', pbActualizarBotones);

    // Atajos de teclado, solo con el reproductor abierto y fuera de un campo
    document.addEventListener('keydown', e => {
        if (!overlay.classList.contains('active')) return;
        if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) return;

        const acciones = {
            ' ': () => pbAlternarReproduccion(),
            ArrowLeft: () => pbSaltar(-10),
            ArrowRight: () => pbSaltar(10),
            ArrowDown: () => pbSaltar(-60),
            ArrowUp: () => pbSaltar(60),
            '+': () => pbAplicarZoom(pbNivelZoom() + 1),
            '-': () => pbAplicarZoom(pbNivelZoom() - 1),
            '0': () => pbAplicarZoom(0, PB_SEGUNDOS_DIA / 2),
        };
        if (acciones[e.key]) { e.preventDefault(); acciones[e.key](); }
    });

    // Refresco periódico de la línea de tiempo para mostrar grabaciones nuevas
    setInterval(async () => {
        if (!overlay.classList.contains('active') || !PB.camara || !PB.fecha) return;
        try {
            const url = `/api/nvr/recordings/segments?camera_id=${
                encodeURIComponent(PB.camara.camera_id)}&day=${encodeURIComponent(PB.fecha)}`;
            const d = await (await fetch(url)).json();
            if (d.segments && PB.segmentos && d.segments.length > PB.segmentos.length) {
                PB.segmentos = d.segments;
                PB.tramos = d.ranges || [];
                pbDibujarLineaDeTiempo();
            }
        } catch (e) { /* silencioso */ }
    }, 5000);
})();

function pbActualizarCabezalTemporal(clientX) {
    const linea = document.getElementById('pb-timeline');
    const r = linea.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
    const cabezal = document.getElementById('pb-playhead');
    cabezal.classList.add('visible');
    cabezal.style.left = `${frac * 100}%`;
}


// ===========================================================================
//  Exportación de vídeo
// ===========================================================================

// Tramo marcado sobre la línea de tiempo, en segundos desde medianoche.
PB.seleccion = { activa: false, modo: false, desde: null, hasta: null, arrastrando: false };

function pbSegundosATexto(s) {
    const p = n => String(Math.floor(n)).padStart(2, '0');
    return `${p(s / 3600)}:${p((s / 60) % 60)}:${p(s % 60)}`;
}

function pbDibujarSeleccion() {
    const el = document.getElementById('pb-selection');
    const info = document.getElementById('pb-selection-info');
    const btn = document.getElementById('pb-export');

    if (!PB.seleccion.activa || PB.seleccion.desde === null || PB.seleccion.hasta === null) {
        el.classList.remove('visible');
        info.textContent = '';
        btn.disabled = !PB.camara;
        return;
    }

    const a = Math.min(PB.seleccion.desde, PB.seleccion.hasta);
    const b = Math.max(PB.seleccion.desde, PB.seleccion.hasta);
    const v = pbVentana();

    // Recortar a la ventana visible: al ampliar, la selección puede quedar
    // parcialmente fuera y dibujarla entera desbordaría la barra.
    const va = Math.max(a, v.inicio), vb = Math.min(b, v.fin);
    if (vb <= va) {
        el.classList.remove('visible');
    } else {
        el.classList.add('visible');
        el.style.left = `${((va - v.inicio) / v.span) * 100}%`;
        el.style.width = `${((vb - va) / v.span) * 100}%`;
    }

    const dur = b - a;
    info.textContent = `${pbSegundosATexto(a)} → ${pbSegundosATexto(b)} `
                     + `(${dur >= 60 ? Math.round(dur / 60) + ' min' : Math.round(dur) + ' s'})`;
    btn.disabled = !PB.camara;
}

function pbAlternarModoSeleccion() {
    PB.seleccion.modo = !PB.seleccion.modo;
    document.getElementById('pb-select-mode')
        .classList.toggle('activo', PB.seleccion.modo);
    document.getElementById('pb-timeline').style.cursor =
        PB.seleccion.modo ? 'crosshair' : 'pointer';

    if (!PB.seleccion.modo) {
        PB.seleccion.activa = false;
        PB.seleccion.desde = PB.seleccion.hasta = null;
        pbDibujarSeleccion();
    } else {
        showToast('Arrastra sobre la línea de tiempo para marcar el tramo a exportar.');
    }
}

// ---- Ventana de exportación ----------------------------------------------

function pbAbrirExportacion() {
    if (!PB.camara) { showToast('Primero elige una cámara.'); return; }

    let a, b;
    if (PB.seleccion.desde !== null && PB.seleccion.hasta !== null) {
        a = Math.min(PB.seleccion.desde, PB.seleccion.hasta);
        b = Math.max(PB.seleccion.desde, PB.seleccion.hasta);
    } else {
        const cur = pbInstanteActual();
        b = cur ? pbSegundosDesdeMedianoche(cur) : pbSegundosDesdeMedianoche(new Date());
        a = Math.max(0, b - 300);
    }

    // Se rellenan también los campos manuales: la selección de la línea es
    // aproximada al píxel y así se puede ajustar al segundo.
    document.getElementById('export-date-from').value = PB.fecha || '';
    document.getElementById('export-time-from').value = pbSegundosATexto(a);
    document.getElementById('export-time-to').value = pbSegundosATexto(b);

    const sel = document.getElementById('export-camera');
    sel.innerHTML = '';
    PB.camarasConGrabacion.filter(c => c.days_recorded > 0).forEach(c => {
        const o = document.createElement('option');
        o.value = c.camera_id;
        o.textContent = c.name;
        sel.appendChild(o);
    });
    sel.value = PB.camara.camera_id;

    pbActualizarNombreSugerido();
    pbActualizarResumenExportacion();

    document.getElementById('export-progress-wrap').style.display = 'none';
    document.getElementById('export-message').classList.remove('visible');
    document.getElementById('export-start').disabled = false;

    // Aviso honesto sobre dónde se puede guardar según el navegador
    const nativo = typeof window.showSaveFilePicker === 'function';
    document.getElementById('export-location-note').textContent = nativo
        ? 'Al exportar se abrirá el diálogo de Windows para elegir la carpeta, '
          + 'situado en Vídeos.'
        : 'Este navegador no permite elegir carpeta: el archivo irá a tu carpeta '
          + 'de descargas habitual.';

    document.getElementById('export-overlay').classList.add('active');
}

function pbActualizarNombreSugerido() {
    const sel = document.getElementById('export-camera');
    const nombreCam = sel.options[sel.selectedIndex]?.textContent || 'grabacion';
    const f = document.getElementById('export-date-from').value || '';
    const d = (document.getElementById('export-time-from').value || '').replace(/:/g, '-');
    const h = (document.getElementById('export-time-to').value || '').replace(/:/g, '-');

    // Windows no admite \ / : * ? " < > | en los nombres de archivo
    const base = nombreCam.replace(/[\\/:*?"<>|]/g, '-').replace(/\s+/g, '_');
    document.getElementById('export-filename').value = `${base}_${f}_${d}_a_${h}.mp4`;
}

function pbActualizarResumenExportacion() {
    const f = document.getElementById('export-date-from').value;
    const d = document.getElementById('export-time-from').value;
    const h = document.getElementById('export-time-to').value;
    const info = document.getElementById('export-range-info');

    const sd = pbTextoASegundos(d), sh = pbTextoASegundos(h);
    if (sd === null || sh === null || sh <= sd) {
        info.innerHTML = '<span style="color:#f87171;">El intervalo no es válido: '
                       + 'la hora final debe ser posterior a la inicial.</span>';
        return;
    }
    const dur = sh - sd;
    info.textContent = `Se exportará ${f} de ${d} a ${h}`
                     + ` — ${dur >= 60 ? (dur / 60).toFixed(1) + ' minutos' : dur + ' segundos'}.`;
}

function pbTextoASegundos(t) {
    if (!t) return null;
    const m = String(t).match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/);
    if (!m) return null;
    return (+m[1]) * 3600 + (+m[2]) * 60 + (+(m[3] || 0));
}

// ---- Ejecución de la exportación -----------------------------------------

async function pbEjecutarExportacion() {
    const camId = document.getElementById('export-camera').value;
    const fecha = document.getElementById('export-date-from').value;
    const desde = document.getElementById('export-time-from').value;
    const hasta = document.getElementById('export-time-to').value;
    const nombreArchivo = document.getElementById('export-filename').value.trim()
                          || 'grabacion.mp4';

    const sd = pbTextoASegundos(desde), sh = pbTextoASegundos(hasta);
    if (!fecha || sd === null || sh === null || sh <= sd) {
        pbMensajeExportacion(false, 'Revisa la fecha y las horas.');
        return;
    }

    const norm = t => (t.length === 5 ? t + ':00' : t);
    const cuerpo = {
        camera_id: camId,
        from: `${fecha} ${norm(desde)}`,
        to: `${fecha} ${norm(hasta)}`,
        name: document.getElementById('export-camera')
                      .options[document.getElementById('export-camera').selectedIndex]?.textContent || '',
    };

    document.getElementById('export-start').disabled = true;
    document.getElementById('export-progress-wrap').style.display = 'block';
    pbProgresoExportacion(0, 'Preparando…');
    pbMensajeExportacion(true, '');

    let job;
    try {
        const r = await fetch('/api/nvr/export', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(cuerpo)
        });
        const d = await r.json();
        if (!r.ok || d.status === 'error') {
            pbMensajeExportacion(false, d.message || 'No se pudo iniciar la exportación');
            document.getElementById('export-start').disabled = false;
            return;
        }
        job = d.job;
    } catch (e) {
        pbMensajeExportacion(false, 'No se pudo contactar con el servidor');
        document.getElementById('export-start').disabled = false;
        return;
    }

    // Se consulta el progreso en lugar de esperar a que termine la petición:
    // una exportación larga agotaría el tiempo de espera del navegador.
    const listo = await pbEsperarExportacion(job.id);
    if (!listo) {
        document.getElementById('export-start').disabled = false;
        return;
    }

    pbProgresoExportacion(100, 'Guardando…');
    await pbGuardarArchivo(job.id, nombreArchivo);
    document.getElementById('export-start').disabled = false;
}

async function pbEsperarExportacion(jobId) {
    const inicio = Date.now();
    while (Date.now() - inicio < 15 * 60 * 1000) {
        await new Promise(r => setTimeout(r, 800));
        try {
            const d = await (await fetch(`/api/nvr/export/${jobId}`)).json();
            const job = d.job;
            if (!job) { pbMensajeExportacion(false, d.message || 'Exportación perdida'); return false; }

            if (job.status === 'error') {
                pbMensajeExportacion(false, job.message || 'La exportación falló');
                return false;
            }
            pbProgresoExportacion(job.progress,
                job.status === 'procesando' ? 'Uniendo y recortando el vídeo…' : 'En cola…');
            if (job.status === 'listo') return true;
        } catch (e) {
            pbMensajeExportacion(false, 'Se perdió la conexión durante la exportación');
            return false;
        }
    }
    pbMensajeExportacion(false, 'La exportación tardó demasiado');
    return false;
}

/**
 * Descarga el resultado y lo guarda donde elija el usuario.
 *
 * Con showSaveFilePicker se abre el diálogo nativo de Windows, que permite
 * elegir carpeta y nombre; se parte de la carpeta Vídeos. Los navegadores que
 * no lo soportan solo pueden descargar a la carpeta habitual, así que se
 * recurre a la descarga normal conservando el nombre.
 */
async function pbGuardarArchivo(jobId, nombreArchivo) {
    const url = `/api/nvr/export/${jobId}/download`;

    if (typeof window.showSaveFilePicker === 'function') {
        let destino;
        try {
            destino = await window.showSaveFilePicker({
                suggestedName: nombreArchivo,
                startIn: 'videos',
                types: [{ description: 'Vídeo MP4', accept: { 'video/mp4': ['.mp4'] } }],
            });
        } catch (e) {
            // El usuario cerró el diálogo: no es un error que haya que reportar
            pbMensajeExportacion(true, 'Guardado cancelado. El archivo sigue disponible unos minutos.');
            return;
        }

        try {
            const resp = await fetch(url);
            if (!resp.ok) throw new Error('descarga');
            const escritor = await destino.createWritable();
            await resp.body.pipeTo(escritor);
            pbMensajeExportacion(true, `✔ Guardado como ${destino.name}`);
            setTimeout(() => document.getElementById('export-overlay')
                                     .classList.remove('active'), 1800);
        } catch (e) {
            pbMensajeExportacion(false, 'No se pudo escribir el archivo');
        }
        return;
    }

    // Descarga normal
    const a = document.createElement('a');
    a.href = url;
    a.download = nombreArchivo;
    document.body.appendChild(a);
    a.click();
    a.remove();
    pbMensajeExportacion(true, '✔ Descarga iniciada');
}

function pbProgresoExportacion(pct, texto) {
    document.getElementById('export-bar-fill').style.width = `${pct}%`;
    document.getElementById('export-progress-text').textContent =
        `${texto} ${pct > 0 ? Math.round(pct) + '%' : ''}`;
}

function pbMensajeExportacion(ok, texto) {
    const el = document.getElementById('export-message');
    el.textContent = texto;
    el.style.color = ok ? '#4ade80' : '#f87171';
    el.classList.toggle('visible', !!texto);
}

// ---- Enlazado -------------------------------------------------------------

(function pbInicializarExportacion() {
    document.getElementById('pb-select-mode')
        .addEventListener('click', pbAlternarModoSeleccion);
    document.getElementById('pb-export')
        .addEventListener('click', pbAbrirExportacion);

    ['export-close', 'export-cancel'].forEach(id =>
        document.getElementById(id).addEventListener('click',
            () => document.getElementById('export-overlay').classList.remove('active')));

    document.getElementById('export-start')
        .addEventListener('click', pbEjecutarExportacion);

    ['export-camera', 'export-date-from', 'export-time-from', 'export-time-to']
        .forEach(id => document.getElementById(id).addEventListener('change', () => {
            pbActualizarNombreSugerido();
            pbActualizarResumenExportacion();
        }));

    // Marcado sobre la línea de tiempo. Se registra en fase de captura para
    // poder detener el manejador de salto cuando el modo de marcar está activo.
    const linea = document.getElementById('pb-timeline');

    linea.addEventListener('mousedown', e => {
        if (!PB.seleccion.modo) return;
        e.stopPropagation();
        const { segundos } = pbHoraEnPosicion(e.clientX);
        PB.seleccion.arrastrando = true;
        PB.seleccion.activa = true;
        PB.seleccion.desde = segundos;
        PB.seleccion.hasta = segundos;
        pbDibujarSeleccion();
    }, true);

    linea.addEventListener('mousemove', e => {
        if (!PB.seleccion.arrastrando) return;
        PB.seleccion.hasta = pbHoraEnPosicion(e.clientX).segundos;
        pbDibujarSeleccion();
    }, true);

    document.addEventListener('mouseup', () => {
        if (!PB.seleccion.arrastrando) return;
        PB.seleccion.arrastrando = false;
        // Un clic suelto sin arrastrar no es una selección útil
        if (Math.abs(PB.seleccion.hasta - PB.seleccion.desde) < 1) {
            PB.seleccion.activa = false;
            PB.seleccion.desde = PB.seleccion.hasta = null;
        }
        pbDibujarSeleccion();
    }, true);
})();


// ===========================================================================
// Vistas de la cuadrícula en directo
//
// Dos cosas distintas sobre la misma información:
//
//   La vista actual se guarda sola en cuanto cambia, para devolver al usuario
//   exactamente lo que tenía delante al volver a entrar.
//
//   Las vistas guardadas son composiciones con nombre, para saltar entre
//   grupos de cámaras sin arrastrarlas otra vez una a una.
//
// Todo va asociado a la cuenta, no al navegador: quien entre desde otro equipo
// encuentra sus cámaras donde las dejó.
// ===========================================================================

let vistasGuardadas = [];

// Mientras se restaura una composición hay que evitar guardar los estados
// intermedios: al reconstruir la cuadrícula las celdas pasan por vacías, y
// guardar ese instante borraría justo lo que se está restaurando.
let restaurandoComposicion = false;

// Y hasta que no se haya leído lo guardado no se guarda nada. Al arrancar, la
// página construye una cuadrícula vacía de 4 celdas antes de saber qué había:
// ese estado disparaba un guardado que, si llegaba al servidor antes que la
// lectura, borraba justo lo que se estaba a punto de restaurar.
let composicionRestaurada = false;

/** Cámara de cada celda, en orden, con null en las vacías. */
function composicionActual() {
    const celdas = document.querySelectorAll('#grid-container .video-cell');
    return [...celdas].map(celda => {
        // Se lee del DOM y no de streamState porque es la única fuente fiable:
        // al cambiar de distribución las cámaras se recolocan y los índices de
        // streamState dejan de corresponder con las celdas.
        const img = celda.querySelector('img');
        if (!img || !img.src) return null;
        const partes = img.src.split('/video_feed/');
        return partes.length > 1 ? partes[1].split('?')[0] : null;
    });
}

/** Coloca una composición concreta: distribución y cámara de cada celda. */
function aplicarComposicion(layout, camaras) {
    restaurandoComposicion = true;
    try {
        clearAllStreams();
        setLayout(layout);

        const celdas = document.querySelectorAll('#grid-container .video-cell');
        (camaras || []).forEach((camId, i) => {
            if (!camId || i >= celdas.length) return;
            // Una cámara borrada del sistema deja su hueco vacío, en vez de
            // invalidar la vista entera o dejar un recuadro roto.
            if (!cameras.some(c => c.id === camId)) return;
            const src = `/video_feed/${camId}`;
            _placeCellStream(celdas[i], src);
            streamState[i] = { src, camId };
        });
    } finally {
        restaurandoComposicion = false;
    }
    marcarVistaActiva();
}

/**
 * Guarda la cuadrícula actual, agrupando los cambios seguidos.
 *
 * Soltar una cámara o cambiar de distribución dispara varios cambios en
 * cadena; sin agrupar se enviaría una petición por cada uno.
 */
let guardadoDeVistaPendiente = null;
function guardarComposicionActual() {
    if (restaurandoComposicion || !composicionRestaurada) return;
    clearTimeout(guardadoDeVistaPendiente);
    guardadoDeVistaPendiente = setTimeout(async () => {
        try {
            await fetch('/api/live_layout', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    layout: currentLayout,
                    cameras: composicionActual(),
                }),
            });
        } catch (e) {
            // Un corte de red no debe estropear nada: el siguiente cambio
            // vuelve a intentarlo con el estado ya actualizado.
        }
        marcarVistaActiva();
    }, 500);
}

async function restaurarComposicionGuardada() {
    try {
        const res = await fetch('/api/live_layout');
        if (!res.ok) return;
        const d = await res.json();
        if (!d || !d.layout) return;
        aplicarComposicion(d.layout, d.cameras || []);
    } catch (e) {
        // Sin nada que restaurar se deja la cuadrícula por defecto
    } finally {
        // También si no había nada guardado o falló la lectura: a partir de
        // aquí lo que haya en pantalla es decisión del usuario y debe guardarse.
        composicionRestaurada = true;
    }
}

// ---- Vistas con nombre ----

async function cargarVistas() {
    try {
        const res = await fetch('/api/views');
        if (!res.ok) return;
        vistasGuardadas = (await res.json()).views || [];
    } catch (e) {
        vistasGuardadas = [];
    }
    renderVistas();
}

function renderVistas() {
    const lista = document.getElementById('vistas-lista');
    if (!lista) return;
    lista.innerHTML = '';

    if (!vistasGuardadas.length) {
        const vacio = document.createElement('li');
        vacio.className = 'vistas-vacio';
        vacio.textContent = 'Coloca las cámaras como quieras y pulsa «+ Guardar» '
                          + 'para poder volver a esta disposición.';
        lista.appendChild(vacio);
        return;
    }

    vistasGuardadas.forEach(v => {
        const li = document.createElement('li');
        li.className = 'vista-item';
        li.dataset.id = v.id;
        li.title = `Aplicar «${v.name}»`;

        const nombre = document.createElement('span');
        nombre.className = 'vista-nombre';
        nombre.textContent = v.name;

        const detalle = document.createElement('span');
        detalle.className = 'vista-detalle';
        detalle.textContent = `${v.used}/${v.layout}`;
        detalle.title = `${v.used} cámara(s) en una cuadrícula de ${v.layout}`;

        const acciones = document.createElement('span');
        acciones.className = 'vista-acciones';
        acciones.appendChild(botonDeVista('⟳', 'Sustituir por la cuadrícula actual',
                                          () => actualizarVista(v.id, v.name)));
        acciones.appendChild(botonDeVista('✎', 'Cambiar el nombre',
                                          () => renombrarVista(v.id, v.name)));
        acciones.appendChild(botonDeVista('🗑', 'Eliminar la vista',
                                          () => borrarVista(v.id, v.name)));

        li.append(nombre, detalle, acciones);
        li.addEventListener('click', e => {
            // Los botones de acción viven dentro de la fila, que también es
            // pulsable: sin esto, borrar una vista la aplicaría antes.
            if (e.target.closest('.vista-acciones')) return;
            aplicarComposicion(v.layout, v.cameras);
            guardarComposicionActual();
        });
        lista.appendChild(li);
    });

    marcarVistaActiva();
}

function botonDeVista(texto, titulo, alPulsar) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = texto;
    b.title = titulo;
    b.addEventListener('click', e => { e.stopPropagation(); alPulsar(); });
    return b;
}

/** Resalta la vista que coincide con lo que hay en pantalla. */
function marcarVistaActiva() {
    const actual = composicionActual();
    document.querySelectorAll('.vista-item').forEach(li => {
        const v = vistasGuardadas.find(x => String(x.id) === li.dataset.id);
        const igual = v && v.layout === currentLayout
                   && v.cameras.length === actual.length
                   && v.cameras.every((c, i) => (c || null) === (actual[i] || null));
        li.classList.toggle('activa', !!igual);
    });
}

function mensajeDeVistas(texto, error = false) {
    const msg = document.getElementById('vistas-msg');
    if (!msg) return;
    msg.textContent = texto;
    msg.style.color = error ? '#f87171' : '#4ade80';
    msg.classList.add('visible');
    clearTimeout(mensajeDeVistas._t);
    mensajeDeVistas._t = setTimeout(() => msg.classList.remove('visible'), 3500);
}

async function guardarVistaNueva() {
    const actual = composicionActual();
    if (!actual.some(Boolean)) {
        mensajeDeVistas('Coloca al menos una cámara antes de guardar.', true);
        return;
    }
    const nombre = prompt('Nombre para esta vista:', `Vista ${vistasGuardadas.length + 1}`);
    if (nombre === null) return;

    const res = await fetch('/api/views', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: nombre, layout: currentLayout, cameras: actual }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { mensajeDeVistas(d.message || 'No se pudo guardar', true); return; }
    await cargarVistas();
    mensajeDeVistas(`Vista «${nombre.trim()}» guardada`);
}

async function actualizarVista(id, nombre) {
    if (!confirm(`¿Sustituir «${nombre}» por la cuadrícula que tienes ahora?`)) return;

    const res = await fetch(`/api/views/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ layout: currentLayout, cameras: composicionActual() }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { mensajeDeVistas(d.message || 'No se pudo actualizar', true); return; }
    await cargarVistas();
    mensajeDeVistas(`«${nombre}» actualizada`);
}

async function renombrarVista(id, nombreActual) {
    const nombre = prompt('Nuevo nombre:', nombreActual);
    if (nombre === null || nombre.trim() === nombreActual) return;

    const res = await fetch(`/api/views/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: nombre }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { mensajeDeVistas(d.message || 'No se pudo renombrar', true); return; }
    await cargarVistas();
}

async function borrarVista(id, nombre) {
    if (!confirm(`¿Eliminar la vista «${nombre}»?\n\n`
                 + 'Las cámaras no se tocan; solo se borra la disposición guardada.')) return;
    const res = await fetch(`/api/views/${id}`, { method: 'DELETE' });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { mensajeDeVistas(d.message || 'No se pudo eliminar', true); return; }
    await cargarVistas();
    mensajeDeVistas(`«${nombre}» eliminada`);
}


// ===========================================================================
// Mapa de cámaras
//
// Dos clases de fondo, con la misma interacción encima:
//
//   Teselas   OpenStreetMap y equivalentes. Coordenadas geográficas reales.
//   Imagen    Un plano que sube el administrador. Sin geografía, así que se usa
//             el sistema de coordenadas plano de Leaflet (CRS.Simple) y las
//             posiciones se guardan como fracción del ancho y del alto.
//
// Se guardan por separado, de modo que alternar entre un mapa de calles y un
// plano no borra lo colocado en el otro.
//
// El operador mira y navega; el administrador además coloca, mueve y quita.
// Igual que en el resto de la interfaz, ocultar los controles es comodidad: el
// servidor vuelve a exigir rol de administrador en cada petición.
// ===========================================================================

const MAPA = {
    leaflet: null,       // instancia de L.Map
    capa: null,          // capa de teselas o superposición de imagen
    marcadores: {},      // {camera_id: L.Marker}
    ajustes: null,
    editando: false,
};

function mapaEsImagen() {
    return MAPA.ajustes && MAPA.ajustes.mode === 'image';
}

/**
 * Convierte la posición guardada de una cámara en coordenadas de Leaflet.
 *
 * En modo imagen las fracciones se llevan a píxeles y se invierte la vertical:
 * CRS.Simple crece hacia arriba, mientras que en una imagen la fila 0 es la de
 * arriba. Sin invertir, las cámaras aparecerían reflejadas.
 */
function mapaPosicionDeCamara(cam) {
    if (mapaEsImagen()) {
        const p = cam.map_image;
        if (!p) return null;
        const alto = MAPA.ajustes.image_height || 1;
        const ancho = MAPA.ajustes.image_width || 1;
        return L.latLng(alto - p.y * alto, p.x * ancho);
    }
    const p = cam.map_geo;
    return p ? L.latLng(p.lat, p.lng) : null;
}

/** Operación inversa: de coordenadas de Leaflet a lo que se guarda. */
function mapaCoordenadasParaGuardar(latlng) {
    if (mapaEsImagen()) {
        const alto = MAPA.ajustes.image_height || 1;
        const ancho = MAPA.ajustes.image_width || 1;
        return {
            x: Math.min(1, Math.max(0, latlng.lng / ancho)),
            y: Math.min(1, Math.max(0, (alto - latlng.lat) / alto)),
        };
    }
    return { x: latlng.lng, y: latlng.lat };
}

/**
 * Abre el mapa.
 *
 * Args:
 *   camaraDestacada: si se indica, en lugar de encuadrar en el conjunto se va
 *                    a esa cámara y se resalta. Lo usa el botón de cada panel
 *                    de vídeo, donde la pregunta no es "dónde están todas" sino
 *                    "dónde está justo esta".
 */
async function abrirMapa(camaraDestacada = null) {
    document.getElementById('map-overlay').classList.add('active');
    try {
        MAPA.ajustes = await (await fetch('/api/map/settings')).json();
    } catch (e) {
        mensajeDeMapa('No se pudieron leer los ajustes del mapa', true);
        return;
    }
    construirMapa();
    dibujarMarcadores();
    renderListaDelMapa();

    // Leaflet mide el contenedor al crearse. Aquí acaba de hacerse visible, así
    // que sin esto se queda con tamaño cero y solo pinta una esquina.
    setTimeout(() => {
        if (!MAPA.leaflet) return;
        MAPA.leaflet.invalidateSize();
        // El encuadre va después de medir: calculado sobre un contenedor de
        // tamaño cero, el zoom saldría mal.
        if (camaraDestacada) destacarCamaraEnMapa(camaraDestacada);
        else encuadrarEnLasCamaras();
    }, 60);
}

/**
 * Centra el mapa en una cámara concreta y la señala.
 *
 * Sin resaltarla, en una zona con varias cámaras cerca no se sabría cuál de los
 * pines es la del panel desde el que se ha llegado.
 */
function destacarCamaraEnMapa(camId) {
    const cam = cameras.find(c => c.id === camId);
    if (!cam) return;

    const punto = mapaPosicionDeCamara(cam);
    if (!punto) {
        // No situarla es lo normal al empezar, así que se explica en lugar de
        // dejar el mapa en un sitio cualquiera sin decir por qué.
        encuadrarEnLasCamaras();
        mensajeDeMapa(IS_ADMIN
            ? `«${cam.name}» todavía no está en el mapa. Pulsa «Editar mapa» y arrástrala.`
            : `«${cam.name}» todavía no está situada en el mapa.`, true);
        return;
    }

    MAPA.leaflet.setView(punto, Math.max(MAPA.leaflet.getZoom(),
                                         MAPA_ZOOM_UNA_CAMARA));

    const marcador = MAPA.marcadores[camId];
    if (!marcador || !marcador.getElement()) return;
    const el = marcador.getElement();
    el.classList.add('destacado');
    // Se retira sola: el resalte sirve para encontrarla al llegar, no para
    // quedarse marcada mientras se usa el mapa.
    setTimeout(() => el.classList.remove('destacado'), 2600);
}

// Zoom al abrir sobre una sola cámara: se ve la calle sin quedarse pegado al
// tejado, que es lo que pasa al ampliar al máximo.
const MAPA_ZOOM_UNA_CAMARA = 16;

// Tope al encuadrar sobre un grupo: con varias cámaras muy juntas, ajustar sin
// límite deja el mapa tan cerca que se pierde toda referencia del entorno.
const MAPA_ZOOM_MAXIMO_ENCUADRE = 17;

/**
 * Abre el mapa mostrando dónde están las cámaras.
 *
 * Antes se abría siempre en el centro guardado en los ajustes, que rara vez
 * coincide con la instalación, y obligaba a navegar hasta las cámaras cada vez.
 *
 * Sobre un plano propio no se hace nada: al construirlo ya se encaja la imagen
 * entera, que es la vista útil de un plano.
 */
function encuadrarEnLasCamaras() {
    if (!MAPA.leaflet || mapaEsImagen()) return;

    const situadas = cameras.filter(c => c.map_geo);
    const puntos = situadas.map(c => L.latLng(c.map_geo.lat, c.map_geo.lng));

    if (!puntos.length) {
        // Sin ninguna cámara situada se respeta el centro configurado
        MAPA.leaflet.setView(MAPA.ajustes.center || [40.4168, -3.7038],
                             MAPA.ajustes.zoom || 6);
        return;
    }

    if (puntos.length === 1) {
        MAPA.leaflet.setView(puntos[0], MAPA_ZOOM_UNA_CAMARA);
        return;
    }

    const principales = grupoPrincipalDeCamaras(puntos);
    MAPA.leaflet.fitBounds(L.latLngBounds(principales), {
        padding: [50, 50],
        maxZoom: MAPA_ZOOM_MAXIMO_ENCUADRE,
    });

    const fuera = puntos.length - principales.length;
    if (fuera > 0) {
        mensajeDeMapa(`Vista centrada en ${principales.length} de ${puntos.length} `
                      + `cámaras; ${fuera} queda${fuera > 1 ? 'n' : ''} lejos`);
    }
}

/**
 * Descarta las cámaras sueltas para encuadrar donde está el grueso.
 *
 * Encajar todas sin más falla en cuanto hay una lejos del resto: con veinte
 * cámaras en un recinto y una en otra ciudad, el mapa se abre mostrando el país
 * entero y no se distingue ninguna.
 *
 * Se usa la mediana y no el promedio, tanto para el centro como para la
 * distancia, porque el promedio lo arrastra precisamente el punto que se quiere
 * ignorar. El umbral es generoso —cuatro veces la distancia típica— para
 * apartar solo lo que está desproporcionadamente lejos, y nunca deja menos de
 * dos cámaras: si todas están dispersas, lo honesto es mostrarlas todas.
 */
function grupoPrincipalDeCamaras(puntos) {
    const mediana = valores => {
        const o = [...valores].sort((a, b) => a - b);
        const m = Math.floor(o.length / 2);
        return o.length % 2 ? o[m] : (o[m - 1] + o[m]) / 2;
    };

    const centro = L.latLng(mediana(puntos.map(p => p.lat)),
                            mediana(puntos.map(p => p.lng)));
    const distancias = puntos.map(p => centro.distanceTo(p));

    // Suelo de 2 km: con las cámaras de un mismo edificio la distancia típica
    // es de metros, y multiplicarla por cuatro seguiría siendo tan pequeña que
    // descartaría vecinas legítimas.
    const limite = Math.max(mediana(distancias) * 4, 2000);
    const dentro = puntos.filter((_, i) => distancias[i] <= limite);
    return dentro.length >= 2 ? dentro : puntos;
}

function construirMapa() {
    const lienzo = document.getElementById('map-lienzo');

    // Se rehace de cero al cambiar de fondo: reutilizar la instancia obligaría
    // a cambiar el sistema de coordenadas en caliente, que Leaflet no admite.
    if (MAPA.leaflet) {
        MAPA.leaflet.remove();
        MAPA.leaflet = null;
        MAPA.marcadores = {};
    }

    if (mapaEsImagen() && MAPA.ajustes.image) {
        const alto = MAPA.ajustes.image_height;
        const ancho = MAPA.ajustes.image_width;
        MAPA.leaflet = L.map(lienzo, {
            crs: L.CRS.Simple,
            minZoom: -4,
            maxZoom: 4,
            attributionControl: false,
        });
        const limites = [[0, 0], [alto, ancho]];
        MAPA.capa = L.imageOverlay(`/static/maps/${MAPA.ajustes.image}`, limites)
                     .addTo(MAPA.leaflet);
        MAPA.leaflet.fitBounds(limites);
        // Zoom al que el plano entra justo en la ventana. Sirve de referencia
        // para decidir cuánto detalle mostrar en los marcadores: sobre un plano
        // no hay niveles de zoom con significado propio como en un mapa de
        // calles, así que se mide respecto a esta vista.
        MAPA.zoomBase = MAPA.leaflet.getZoom();
        // Sin este tope se puede alejar la imagen hasta perderla de vista
        MAPA.leaflet.setMaxBounds(L.latLngBounds(limites).pad(0.5));
    } else {
        MAPA.leaflet = L.map(lienzo).setView(
            MAPA.ajustes.center || [40.4168, -3.7038], MAPA.ajustes.zoom || 6);
        MAPA.capa = L.tileLayer(MAPA.ajustes.tile_url, {
            attribution: MAPA.ajustes.attribution,
            maxZoom: MAPA.ajustes.max_zoom || 19,
        }).addTo(MAPA.leaflet);
    }

    // Los marcadores muestran más o menos información según el zoom
    MAPA.leaflet.on('zoomend', actualizarDetalleDeMarcadores);
    actualizarDetalleDeMarcadores();

    // Soltar una cámara arrastrada desde la lista lateral
    lienzo.addEventListener('dragover', e => {
        if (MAPA.editando) e.preventDefault();
    });
    lienzo.addEventListener('drop', async e => {
        if (!MAPA.editando) return;
        e.preventDefault();
        let cam;
        try { cam = JSON.parse(e.dataTransfer.getData('text/plain')); } catch (_) { return; }
        if (!cam || !cam.id) return;
        const punto = MAPA.leaflet.mouseEventToLatLng(e);
        await situarCamara(cam.id, punto);
    });
}

/**
 * Ajusta cuánta información muestra cada marcador según el zoom.
 *
 * Con el mapa alejado, un rótulo con el nombre por cada cámara tapa justamente
 * lo que se quiere ver, y varios nombres se solapan entre sí. Se muestra
 * entonces solo el número, que ocupa un círculo pequeño; al acercarse aparece
 * el nombre, que a esa escala ya cabe sin estorbar.
 *
 * El umbral se fija de forma distinta en cada fondo. En un mapa de calles los
 * niveles de zoom tienen un significado conocido —13 es aproximadamente el
 * barrio— y ahí es donde el nombre empieza a ser útil. Sobre un plano propio no
 * existe esa escala, así que se mide respecto al zoom en que la imagen encaja
 * en la ventana.
 */
function actualizarDetalleDeMarcadores() {
    if (!MAPA.leaflet) return;
    const lienzo = document.getElementById('map-lienzo');
    const z = MAPA.leaflet.getZoom();

    let nivel;
    if (mapaEsImagen()) {
        const base = (typeof MAPA.zoomBase === 'number') ? MAPA.zoomBase : 0;
        nivel = z >= base ? 'completo' : (z >= base - 1.5 ? 'medio' : 'minimo');
    } else {
        nivel = z >= 13 ? 'completo' : (z >= 10 ? 'medio' : 'minimo');
    }

    lienzo.classList.remove('detalle-minimo', 'detalle-medio', 'detalle-completo');
    lienzo.classList.add(`detalle-${nivel}`);
}

function iconoDeCamara(cam, colocadaAhora) {
    const salud = cameraStatus[cam.id] || { status: 'unknown' };
    // El icono no tiene tamaño y se ancla en el propio punto geográfico: el pin
    // y la etiqueta se colocan después con transformaciones respecto a ese
    // origen. Así la punta del pin cae siempre sobre la ubicación exacta,
    // independientemente de lo largo que sea el nombre. Dejando que el ancho
    // del rótulo condicionara la caja, un nombre largo desplazaba el pin.
    return L.divIcon({
        className: 'map-marcador-envoltorio',
        html: `<span class="map-marcador estado-${salud.status}">
                   <span class="map-pin"></span>
                   <span class="map-etiqueta">
                       <span class="map-marcador-num">${cam.numero || ''}</span>
                       <span class="map-marcador-nombre">${cam.name}</span>
                   </span>
                   <span class="map-marcador-globo">${cam.name}</span>
               </span>`,
        iconSize: [0, 0],
        iconAnchor: [0, 0],
    });
}

function dibujarMarcadores() {
    Object.values(MAPA.marcadores).forEach(m => m.remove());
    MAPA.marcadores = {};

    cameras.forEach(cam => {
        const punto = mapaPosicionDeCamara(cam);
        if (!punto) return;

        const marcador = L.marker(punto, {
            icon: iconoDeCamara(cam),
            draggable: MAPA.editando,
            title: `${cam.name} — doble clic para verla`,
        }).addTo(MAPA.leaflet);

        // Doble clic: llevarla a un panel de vídeo. Es la acción principal para
        // el operador, y la única que tiene sin permisos de administrador.
        marcador.on('dblclick', e => {
            L.DomEvent.stop(e);        // si no, el mapa también hace zoom
            mostrarCamaraDesdeMapa(cam);
        });

        marcador.on('dragend', async () => {
            await situarCamara(cam.id, marcador.getLatLng(), true);
        });

        // Con el botón derecho se quita del mapa, igual que en la cuadrícula
        marcador.on('contextmenu', async e => {
            L.DomEvent.stop(e);
            if (!MAPA.editando) return;
            if (!confirm(`¿Quitar «${cam.name}» del mapa?\n\n`
                         + 'La cámara sigue existiendo; solo deja de aparecer aquí.')) return;
            await quitarCamaraDelMapa(cam.id);
        });

        MAPA.marcadores[cam.id] = marcador;
    });
}

/**
 * Lleva la cámara a un panel de vídeo.
 *
 * Se busca primero una celda libre, para no tapar lo que el usuario ya está
 * mirando. Si están todas ocupadas se usa la primera, que es lo que pidió el
 * planteamiento y evita quedarse sin poder abrir nada.
 */
function mostrarCamaraDesdeMapa(cam) {
    const celdas = [...document.querySelectorAll('#grid-container .video-cell')];
    if (!celdas.length) return;

    // Si ya se está viendo, no se duplica: repetir el doble clic la sacaría en
    // varios paneles a la vez, ocupando celdas para ver dos veces lo mismo.
    const yaVisible = celdas.findIndex(c => {
        const img = c.querySelector('img');
        return img && img.src.includes(`/video_feed/${cam.id}`);
    });
    if (yaVisible !== -1) {
        document.getElementById('map-overlay').classList.remove('active');
        showToast(`${cam.name} ya se está viendo en el panel ${yaVisible + 1}`);
        return;
    }

    let destino = celdas.find(c => !c.querySelector('img')) || celdas[0];
    const indice = parseInt(destino.dataset.index);
    const src = `/video_feed/${cam.id}`;

    _placeCellStream(destino, src, cam);
    streamState[indice] = { src, camId: cam.id };
    guardarComposicionActual();

    document.getElementById('map-overlay').classList.remove('active');
    showToast(`${cam.name} en el panel ${indice + 1}`);
}

async function situarCamara(camId, latlng, esMovimiento = false) {
    const { x, y } = mapaCoordenadasParaGuardar(latlng);
    const res = await fetch(`/api/map/cameras/${camId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: MAPA.ajustes.mode, x, y }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { mensajeDeMapa(d.message || 'No se pudo situar la cámara', true); return; }

    await fetchCameras();
    dibujarMarcadores();
    renderListaDelMapa();
    if (!esMovimiento) mensajeDeMapa('Cámara situada');
}

async function quitarCamaraDelMapa(camId) {
    const res = await fetch(
        `/api/map/cameras/${camId}?mode=${encodeURIComponent(MAPA.ajustes.mode)}`,
        { method: 'DELETE' });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { mensajeDeMapa(d.message || 'No se pudo quitar', true); return; }
    await fetchCameras();
    dibujarMarcadores();
    renderListaDelMapa();
    mensajeDeMapa('Cámara quitada del mapa');
}

/** Cámaras que todavía no están sobre el mapa, para arrastrarlas. */
function renderListaDelMapa() {
    const lista = document.getElementById('map-camaras');
    const selector = document.getElementById('map-manual-cam');
    if (!lista) return;

    const sinColocar = cameras.filter(c => !mapaPosicionDeCamara(c));

    lista.innerHTML = '';
    if (!sinColocar.length) {
        const li = document.createElement('li');
        li.className = 'map-camaras-vacio';
        li.textContent = 'Todas las cámaras están ya en el mapa.';
        lista.appendChild(li);
    } else {
        sinColocar.forEach(cam => {
            const li = document.createElement('li');
            li.className = 'map-camara-item';
            li.draggable = true;
            li.innerHTML = `<span class="cam-numero">${cam.numero || ''}</span>${cam.name}`;
            li.addEventListener('dragstart',
                e => e.dataTransfer.setData('text/plain', JSON.stringify(cam)));
            lista.appendChild(li);
        });
    }

    // El desplegable del alta manual sí lista todas: sirve tanto para colocar
    // una cámara nueva como para corregir las coordenadas de una ya situada.
    if (selector) {
        const elegida = selector.value;
        selector.innerHTML = '';
        cameras.forEach(cam => {
            const o = document.createElement('option');
            o.value = cam.id;
            o.textContent = `${cam.numero ? cam.numero + ' · ' : ''}${cam.name}`;
            selector.appendChild(o);
        });
        if (elegida) selector.value = elegida;
    }
}

function alternarEdicionDelMapa() {
    MAPA.editando = !MAPA.editando;
    document.getElementById('map-overlay')
            .classList.toggle('editando', MAPA.editando);
    document.getElementById('map-editar').textContent =
        MAPA.editando ? 'Terminar edición' : 'Editar mapa';
    document.getElementById('map-pista').textContent = MAPA.editando
        ? 'Arrastra cámaras al mapa · botón derecho sobre una para quitarla'
        : 'Doble clic sobre una cámara para verla';

    // Los marcadores solo se pueden arrastrar en edición, y eso se decide al
    // crearlos, así que se rehacen. La lista lateral también: si algo cambió
    // desde que se abrió el mapa, se quedaría mostrando cámaras ya colocadas.
    dibujarMarcadores();
    renderListaDelMapa();
    actualizarEtiquetasManuales();
}

function mensajeDeMapa(texto, error = false) {
    const msg = document.getElementById('map-msg');
    if (!msg) return;
    msg.textContent = texto;
    msg.style.color = error ? '#f87171' : '#4ade80';
    msg.classList.add('visible');
    clearTimeout(mensajeDeMapa._t);
    mensajeDeMapa._t = setTimeout(() => msg.classList.remove('visible'), 3000);
}

// ---- Colocar a mano ----

function actualizarEtiquetasManuales() {
    const imagen = mapaEsImagen();
    document.getElementById('map-manual-etiqueta-x').textContent =
        imagen ? 'X (0 a 1)' : 'Longitud';
    document.getElementById('map-manual-etiqueta-y').textContent =
        imagen ? 'Y (0 a 1)' : 'Latitud';
    document.getElementById('map-manual-ayuda').textContent = imagen
        ? 'Fracción del ancho y del alto de la imagen: 0,5 y 0,5 es el centro.'
        : 'Grados decimales. Se pueden copiar de cualquier mapa.';
    document.getElementById('map-manual-x').placeholder = imagen ? '0.5' : '-3.7038';
    document.getElementById('map-manual-y').placeholder = imagen ? '0.5' : '40.4168';
}

async function situarCamaraAMano() {
    const camId = document.getElementById('map-manual-cam').value;
    const x = parseFloat(document.getElementById('map-manual-x').value);
    const y = parseFloat(document.getElementById('map-manual-y').value);
    if (!camId || Number.isNaN(x) || Number.isNaN(y)) {
        mensajeDeMapa('Indica la cámara y las dos coordenadas', true);
        return;
    }

    const res = await fetch(`/api/map/cameras/${camId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: MAPA.ajustes.mode, x, y }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { mensajeDeMapa(d.message || 'No se pudo situar', true); return; }

    await fetchCameras();
    dibujarMarcadores();
    renderListaDelMapa();
    const marcador = MAPA.marcadores[camId];
    if (marcador) MAPA.leaflet.panTo(marcador.getLatLng());
    mensajeDeMapa('Cámara situada');
}

// ---- Fondo del mapa ----

function abrirAjustesDelMapa() {
    const a = MAPA.ajustes || {};
    const selector = document.getElementById('mapcfg-proveedor');
    selector.innerHTML = '';
    (a.providers || []).forEach(p => {
        const o = document.createElement('option');
        o.value = p.key;
        o.textContent = p.name;
        selector.appendChild(o);
    });
    selector.value = a.provider || 'osm';

    document.getElementById('mapcfg-url').value = a.custom_url || '';
    document.getElementById('mapcfg-atribucion').value = a.custom_attribution || '';
    document.getElementById('mapcfg-modo-imagen').checked = a.mode === 'image';
    document.getElementById('mapcfg-imagen-actual').textContent = a.image
        ? `Plano actual: ${a.image} (${a.image_width}×${a.image_height})`
        : 'Todavía no hay ningún plano subido.';
    alternarCamposDeFondo();
    document.getElementById('mapcfg-overlay').classList.add('active');
}

function alternarCamposDeFondo() {
    const imagen = document.getElementById('mapcfg-modo-imagen').checked;
    document.getElementById('mapcfg-teselas').style.display = imagen ? 'none' : 'block';
    document.getElementById('mapcfg-imagen').style.display = imagen ? 'block' : 'none';
}

async function guardarAjustesDelMapa() {
    const imagen = document.getElementById('mapcfg-modo-imagen').checked;
    const msg = document.getElementById('mapcfg-msg');
    const mostrar = (t, err) => {
        msg.textContent = t;
        msg.style.color = err ? '#f87171' : '#4ade80';
        msg.classList.add('visible');
        clearTimeout(guardarAjustesDelMapa._t);
        guardarAjustesDelMapa._t = setTimeout(() => msg.classList.remove('visible'), 4000);
    };

    if (imagen) {
        const archivo = document.getElementById('mapcfg-archivo').files[0];
        if (archivo) {
            const cuerpo = new FormData();
            cuerpo.append('image', archivo);
            const res = await fetch('/api/map/image', { method: 'POST', body: cuerpo });
            const d = await res.json().catch(() => ({}));
            if (!res.ok) { mostrar(d.message || 'No se pudo subir el plano', true); return; }
            MAPA.ajustes = d;
        } else if (!MAPA.ajustes.image) {
            mostrar('Elige una imagen para el plano', true);
            return;
        } else {
            const res = await fetch('/api/map/settings', {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode: 'image' }),
            });
            const d = await res.json().catch(() => ({}));
            if (!res.ok) { mostrar(d.message || 'No se pudo guardar', true); return; }
            MAPA.ajustes = d;
        }
    } else {
        const res = await fetch('/api/map/settings', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                mode: 'tiles',
                provider: document.getElementById('mapcfg-proveedor').value,
                custom_url: document.getElementById('mapcfg-url').value,
                custom_attribution: document.getElementById('mapcfg-atribucion').value,
            }),
        });
        const d = await res.json().catch(() => ({}));
        if (!res.ok) { mostrar(d.message || 'No se pudo guardar', true); return; }
        MAPA.ajustes = d;
    }

    document.getElementById('mapcfg-overlay').classList.remove('active');
    construirMapa();
    dibujarMarcadores();
    renderListaDelMapa();
    actualizarEtiquetasManuales();
    setTimeout(() => {
        if (!MAPA.leaflet) return;
        MAPA.leaflet.invalidateSize();
        encuadrarEnLasCamaras();
    }, 60);
    mensajeDeMapa('Fondo del mapa actualizado');
}

// ---- Conexiones ----

document.addEventListener('DOMContentLoaded', () => {
    const abrir = document.getElementById('open-map-btn');
    if (!abrir) return;

    abrir.addEventListener('click', abrirMapa);
    document.getElementById('map-cerrar').addEventListener('click',
        () => document.getElementById('map-overlay').classList.remove('active'));

    if (IS_ADMIN) {
        document.getElementById('map-editar').addEventListener('click', alternarEdicionDelMapa);
        document.getElementById('map-config').addEventListener('click', abrirAjustesDelMapa);
        document.getElementById('map-manual-aplicar').addEventListener('click', situarCamaraAMano);
        document.getElementById('mapcfg-modo-imagen').addEventListener('change', alternarCamposDeFondo);
        document.getElementById('mapcfg-guardar').addEventListener('click', guardarAjustesDelMapa);
        document.getElementById('mapcfg-cancelar').addEventListener('click',
            () => document.getElementById('mapcfg-overlay').classList.remove('active'));
    }
});


/**
 * Cambia el idioma de la interfaz.
 *
 * Hace falta recargar: la plantilla se traduce en el servidor, que es lo que
 * evita que la página se pinte un instante en español en cada carga. A cambio,
 * el cambio de idioma cuesta una recarga, que ocurre una vez y no en cada
 * visita.
 */
async function cambiarIdioma(e) {
    const idioma = e.target.value;
    const res = await fetch('/api/preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language: idioma }),
    });
    if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        alert(d.message || T('No se pudo cambiar el idioma'));
        return;
    }
    window.location.reload();
}
