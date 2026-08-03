// Rol del usuario en sesión, inyectado por la plantilla. Gobierna qué partes de
// la interfaz se construyen. El servidor valida los permisos de nuevo en cada
// endpoint: esto es comodidad visual, no la barrera de seguridad.
const IS_ADMIN = document.body.dataset.role === 'admin';

document.addEventListener('DOMContentLoaded', () => {
    fetchCameras();
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

function renderStatusDot(salud) {
    const info = ETIQUETAS_ESTADO[salud.status] || ETIQUETAS_ESTADO.unknown;

    // Detalle en el tooltip: sin él, un punto gris no distingue "caída" de
    // "aún no comprobada".
    let detalle = info.texto;
    if (salud.streaming) {
        detalle = 'En servicio · emitiendo ahora';
    } else if (salud.status === 'online' && salud.latency_ms != null) {
        detalle = `En servicio · respondió en ${salud.latency_ms} ms`;
    }
    if (salud.checked_seconds_ago != null) {
        detalle += ` · comprobada hace ${salud.checked_seconds_ago} s`;
    }

    return `<span class="cam-status-dot ${info.clase}" title="${detalle}"
                  role="img" aria-label="${info.texto}"></span>`;
}

async function fetchCameraStatus() {
    try {
        const res = await fetch('/api/cameras/status');
        if (!res.ok) return;
        const data = await res.json();
        cameraStatus = data.cameras || {};
        renderCameraList();
    } catch (e) {
        // Un fallo puntual de red no debe borrar los indicadores existentes
    }
}

function renderCameraList() {
    const list = document.getElementById('camera-list');
    const filtro = (document.getElementById('camera-search-input')?.value || '')
        .trim().toLowerCase();

    list.innerHTML = '';

    const visibles = filtro
        ? cameras.filter(c => (c.name || '').toLowerCase().includes(filtro))
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
        const salud = cameraStatus[cam.id] || { status: 'unknown' };
        const punto = renderStatusDot(salud);
        // Editar y eliminar solo para administradores. El operador ve la lista
        // y puede arrastrar cámaras a la cuadrícula, pero no modificarlas.
        const acciones = IS_ADMIN ? `
            <div class="cam-actions">
                <button onclick="openModal('${cam.id}')">✎</button>
                <button onclick="deleteCamera('${cam.id}')">🗑</button>
            </div>
        ` : '';

        li.innerHTML = `
            <div class="cam-info">
                <h4>${punto}${cam.name} ${ptzBadge}</h4>
                <span>${cam.type}</span>
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
        document.getElementById('modal-type').value = cam.type || "IP";
        document.getElementById('modal-source').value = cam.source || "";
        document.getElementById('modal-ip').value = cam.ip || "";
        document.getElementById('modal-onvif-port').value = cam.onvif_port || 80;
        document.getElementById('modal-user').value = cam.user || "";
        document.getElementById('modal-password').value = cam.password || "";
        document.getElementById('modal-is-ptz').checked = !!cam.is_ptz;
    } else {
        document.getElementById('modal-cam-id').value = "";
        document.getElementById('modal-name').value = "";
        document.getElementById('modal-type').value = "IP";
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
        type: document.getElementById('modal-type').value,
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

function renderCellHeader(cell, cam) {
    const header = document.createElement('div');
    header.className = 'camera-header';
    
    const isPtz = cam.is_ptz === true || cam.is_ptz === 'true' || cam.is_ptz == 1;
    const ptzBadge = isPtz ? '<span class="ptz-badge">PTZ</span>' : '';
    const index = cell.dataset.index;

    header.innerHTML = `
        <div class="camera-header-left">
            <span class="status-dot" title="Transmisión En Vivo"></span>
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

async function fetchUsers() {
    const res = await fetch('/api/users');
    if (!res.ok) return;
    const data = await res.json();
    currentUserId = data.current_user_id;
    renderUsers(data.users, data.roles);
}

function renderUsers(users, roles) {
    const tbody = document.getElementById('users-table-body');
    tbody.innerHTML = '';

    users.forEach(u => {
        const esUnoMismo = u.id === currentUserId;
        const opciones = roles.map(r =>
            `<option value="${r.key}"${r.key === u.role ? ' selected' : ''}>${r.label}</option>`
        ).join('');

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
