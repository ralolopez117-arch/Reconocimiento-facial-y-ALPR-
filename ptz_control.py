import threading
import time
import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth

try:
    from onvif import ONVIFCamera
except ImportError:
    ONVIFCamera = None


class AxisVAPIXController:
    """
    Manages Axis VAPIX HTTP PTZ control for Axis IP cameras via /axis-cgi/com/ptz.cgi
    """
    def __init__(self, ip, port=80, user="", password="", camera_num=1):
        self.ip = ip
        self.port = int(port) if port else 80
        self.user = user or ""
        self.password = password or ""
        self.camera_num = camera_num

    def _get_url(self):
        return f"http://{self.ip}:{self.port}/axis-cgi/com/ptz.cgi"

    def _request(self, params):
        url = self._get_url()
        auth = HTTPDigestAuth(self.user, self.password) if (self.user and self.password) else None
        try:
            r = requests.get(url, params=params, auth=auth, timeout=3)
            if r.status_code == 401 and self.user:
                r = requests.get(url, params=params, auth=HTTPBasicAuth(self.user, self.password), timeout=3)
            if r.status_code in [200, 204]:
                return True, "Axis VAPIX Ok"
            return False, f"Axis VAPIX HTTP {r.status_code}"
        except Exception as e:
            return False, f"Axis VAPIX Error: {e}"

    def move(self, pan=0.0, tilt=0.0, zoom=0.0):
        # Scale float -1.0..1.0 to VAPIX speed -100..100
        pan_speed = int(round(max(-1.0, min(1.0, float(pan))) * 100))
        tilt_speed = int(round(max(-1.0, min(1.0, float(tilt))) * 100))
        zoom_speed = int(round(max(-1.0, min(1.0, float(zoom))) * 100))

        params = {"camera": self.camera_num}

        if zoom_speed != 0:
            # Zoom only — send ONLY zoom param
            params["continuouszoommove"] = zoom_speed
            return self._request(params)

        # Pan/Tilt — use ONLY continuouspantiltmove=pan,tilt (combined form)
        # Do NOT mix with continuouspanmove/continuoustiltmove — Axis rejects mixed params
        params["continuouspantiltmove"] = f"{pan_speed},{tilt_speed}"

        success, msg = self._request(params)
        if success:
            return success, msg

        # Fallback: directional move (for cameras that don't support continuouspantiltmove)
        speed = max(abs(pan_speed), abs(tilt_speed))
        if pan_speed > 10 and tilt_speed > 10:
            move_cmd = "upright"
        elif pan_speed > 10 and tilt_speed < -10:
            move_cmd = "downright"
        elif pan_speed < -10 and tilt_speed > 10:
            move_cmd = "upleft"
        elif pan_speed < -10 and tilt_speed < -10:
            move_cmd = "downleft"
        elif pan_speed > 10:
            move_cmd = "right"
        elif pan_speed < -10:
            move_cmd = "left"
        elif tilt_speed > 10:
            move_cmd = "up"
        elif tilt_speed < -10:
            move_cmd = "down"
        else:
            move_cmd = "stop"

        fallback_params = {
            "camera": self.camera_num,
            "move": move_cmd,
            "speed": max(1, speed)
        }
        return self._request(fallback_params)

    def stop(self):
        # Send ONLY the combined stop — do NOT mix with move=stop (some firmware rejects it)
        params = {
            "camera": self.camera_num,
            "continuouspantiltmove": "0,0",
            "continuouszoommove": 0,
        }
        return self._request(params)


class ONVIFPTZController:
    """
    Manages ONVIF PTZ control for IP cameras using onvif-zeep.
    """
    def __init__(self, ip, port=80, user="", password=""):
        self.ip = ip
        self.port = int(port) if port else 80
        self.user = user or ""
        self.password = password or ""
        self.camera = None
        self.ptz = None
        self.media = None
        self.profile = None
        self.lock = threading.Lock()
        self.is_connected = False

    def _connect(self):
        global ONVIFCamera
        if ONVIFCamera is None:
            try:
                from onvif import ONVIFCamera
            except ImportError:
                raise RuntimeError("La librería 'onvif-zeep' no está instalada en el entorno de Python. Ejecute: pip install onvif-zeep")
        
        with self.lock:
            if self.is_connected and self.ptz and self.profile:
                return
            
            try:
                # Initialize ONVIF Camera
                self.camera = ONVIFCamera(self.ip, self.port, self.user, self.password)
                
                # Create PTZ and Media service
                self.ptz = self.camera.create_ptz_service()
                self.media = self.camera.create_media_service()
                
                # Get media profiles
                profiles = self.media.GetProfiles()
                if not profiles:
                    raise RuntimeError("No media profiles found on ONVIF camera")
                
                self.profile = profiles[0]
                self.is_connected = True
            except Exception as e:
                self.is_connected = False
                raise RuntimeError(f"Fallo conexión ONVIF en {self.ip}:{self.port} - {e}")

    def move(self, pan=0.0, tilt=0.0, zoom=0.0):
        try:
            self._connect()
        except Exception as e:
            return False, str(e)

        with self.lock:
            try:
                pan = max(-1.0, min(1.0, float(pan)))
                tilt = max(-1.0, min(1.0, float(tilt)))
                zoom = max(-1.0, min(1.0, float(zoom)))

                request = self.ptz.create_type('ContinuousMove')
                request.ProfileToken = self.profile.token

                status = self.ptz.GetStatus({'ProfileToken': self.profile.token})
                if not request.Velocity:
                    request.Velocity = status.PTZStatus.Position

                if request.Velocity and hasattr(request.Velocity, 'PanTilt'):
                    request.Velocity.PanTilt.x = pan
                    request.Velocity.PanTilt.y = tilt

                if request.Velocity and hasattr(request.Velocity, 'Zoom'):
                    request.Velocity.Zoom.x = zoom

                self.ptz.ContinuousMove(request)
                return True, "ONVIF Moving"
            except Exception as e:
                self.is_connected = False
                return False, f"ONVIF PTZ Move Error: {e}"

    def stop(self):
        try:
            self._connect()
        except Exception as e:
            return False, str(e)

        try:
            with self.lock:
                request = self.ptz.create_type('Stop')
                request.ProfileToken = self.profile.token
                request.PanTilt = True
                request.Zoom = True
                self.ptz.Stop(request)
                return True, "ONVIF Stopped"
        except Exception as e:
            return False, f"ONVIF PTZ Stop Error: {e}"


class PTZController:
    """
    Unified PTZ Controller that supports both Axis VAPIX and ONVIF protocol.
    """
    def __init__(self, ip, port=80, user="", password="", is_axis=False):
        self.is_axis = is_axis
        self.axis_ctrl = AxisVAPIXController(ip, port, user, password)
        self.onvif_ctrl = ONVIFPTZController(ip, port, user, password)

    def move(self, pan=0.0, tilt=0.0, zoom=0.0):
        if self.is_axis:
            success, msg = self.axis_ctrl.move(pan, tilt, zoom)
            if success:
                return success, msg
        # Try ONVIF or fallback to Axis VAPIX
        success, msg = self.onvif_ctrl.move(pan, tilt, zoom)
        if not success and not self.is_axis:
            # Fallback attempt via Axis VAPIX if ONVIF failed
            v_success, v_msg = self.axis_ctrl.move(pan, tilt, zoom)
            if v_success:
                self.is_axis = True
                return v_success, v_msg
        return success, msg

    def stop(self):
        if self.is_axis:
            return self.axis_ctrl.stop()
        success, msg = self.onvif_ctrl.stop()
        if not success:
            return self.axis_ctrl.stop()
        return success, msg


# Controller Registry / Cache to reuse connections per camera
_controllers = {}
_controllers_lock = threading.Lock()

def get_ptz_controller(cam_data):
    """
    Retrieves or creates a cached PTZController instance for camera dict data.
    Detects Axis VAPIX automatically from camera source URL or parameters.
    """
    cam_id = cam_data.get("id")
    source = str(cam_data.get("source", ""))
    
    # Extract IP and Port from IP field or source URL
    ip = cam_data.get("ip", "").strip()
    if not ip:
        # Extract IP from source URL e.g. http://24.30.252.59/axis-cgi/...
        clean_src = source.replace("http://", "").replace("https://", "").replace("rtsp://", "")
        ip = clean_src.split("/")[0].split(":")[0]

    port = cam_data.get("onvif_port", 80)
    user = cam_data.get("user", "")
    password = cam_data.get("password", "")

    # Auto-detect Axis camera from source URL containing "axis-cgi" or "axis",
    # or from the ip field containing "axis" in the camera name/protocol field
    # Also treat as Axis if the camera has a non-empty ip and is_ptz (most common case with Axis)
    protocol = str(cam_data.get("protocol", "")).lower()
    cam_name = str(cam_data.get("name", "")).lower()
    is_axis = (
        "axis-cgi" in source.lower()
        or "axis" in source.lower()
        or "axis" in cam_name
        or protocol == "vapix"
        or (bool(ip) and bool(cam_data.get("is_ptz")))
    )

    with _controllers_lock:
        if cam_id not in _controllers:
            _controllers[cam_id] = PTZController(ip, port, user, password, is_axis=is_axis)
        else:
            ctrl = _controllers[cam_id]
            ctrl.axis_ctrl.ip = ip
            ctrl.axis_ctrl.port = int(port) if port else 80
            ctrl.axis_ctrl.user = user
            ctrl.axis_ctrl.password = password
            
            ctrl.onvif_ctrl.ip = ip
            ctrl.onvif_ctrl.port = int(port) if port else 80
            ctrl.onvif_ctrl.user = user
            ctrl.onvif_ctrl.password = password
            if is_axis:
                ctrl.is_axis = True
        return _controllers[cam_id]
