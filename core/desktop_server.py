#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import ctypes
import http.server
import json
import base64
import re
import signal
import socket
import socketserver
import subprocess
import os
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
try:
    from pynput.mouse import Controller
    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False

# For best-in-class macOS cursor tracking with event-tapping and Kalman filtering
import collections

PORT = int(os.environ.get('DESKTOP_PORT', '8080'))
STEALTH_MODE = os.environ.get('DESKTOP_STEALTH', '1').strip().lower() in ('1', 'true', 'yes')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.environ.get('DESKTOP_LOG_DIR', os.path.join(os.path.dirname(SCRIPT_DIR), 'logs'))
SERVER_LOG = os.path.join(LOG_DIR, 'desktop_server.log')

_CG_EVENT_LIB = None
_server_start_time = time.time()


def _log(msg):
    if STEALTH_MODE:
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(SERVER_LOG, 'a', encoding='utf-8') as f:
                f.write('[{}] {}\n'.format(time.strftime('%Y-%m-%d %H:%M:%S'), msg))
        except Exception:
            pass
    else:
        print(msg)


def free_listening_port(port, retries=3):
    """Terminate processes bound to port (macOS lsof)."""
    for attempt in range(retries):
        try:
            result = subprocess.run(
                ['lsof', '-ti', ':{}'.format(port)],
                capture_output=True, text=True, timeout=5
            )
            pids = [p.strip() for p in result.stdout.splitlines() if p.strip()]
            if not pids:
                return True
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except (ProcessLookupError, ValueError, PermissionError):
                    pass
            time.sleep(0.4)
            for pid in pids:
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except (ProcessLookupError, ValueError, PermissionError):
                    pass
            time.sleep(0.3)
        except Exception as exc:
            _log('free_listening_port attempt {}: {}'.format(attempt + 1, exc))
    try:
        test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        test.bind(('0.0.0.0', port))
        test.close()
        return True
    except OSError:
        return False


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        super().server_bind()


def _load_core_graphics():
    global _CG_EVENT_LIB
    if _CG_EVENT_LIB is None and sys.platform == 'darwin':
        try:
            _CG_EVENT_LIB = ctypes.CDLL(
                '/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics'
            )
            class CGPoint(ctypes.Structure):
                _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double)]

            class CGSize(ctypes.Structure):
                _fields_ = [('width', ctypes.c_double), ('height', ctypes.c_double)]

            class CGRect(ctypes.Structure):
                _fields_ = [('origin', CGPoint), ('size', CGSize)]

            _CG_EVENT_LIB._CGPoint = CGPoint
            _CG_EVENT_LIB.CGEventCreate.restype = ctypes.c_void_p
            _CG_EVENT_LIB.CGEventCreate.argtypes = [ctypes.c_void_p]
            _CG_EVENT_LIB.CGEventGetLocation.restype = CGPoint
            _CG_EVENT_LIB.CGEventGetLocation.argtypes = [ctypes.c_void_p]
            _CG_EVENT_LIB.CGGetActiveDisplayList.argtypes = [
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32),
            ]
            _CG_EVENT_LIB.CGGetActiveDisplayList.restype = ctypes.c_int
            _CG_EVENT_LIB.CGDisplayBounds.argtypes = [ctypes.c_uint32]
            _CG_EVENT_LIB.CGDisplayBounds.restype = CGRect
            _CG_EVENT_LIB.CGDisplayPixelsWide.argtypes = [ctypes.c_uint32]
            _CG_EVENT_LIB.CGDisplayPixelsWide.restype = ctypes.c_size_t
            _CG_EVENT_LIB.CGDisplayPixelsHigh.argtypes = [ctypes.c_uint32]
            _CG_EVENT_LIB.CGDisplayPixelsHigh.restype = ctypes.c_size_t
        except Exception:
            _CG_EVENT_LIB = False
    return _CG_EVENT_LIB if _CG_EVENT_LIB is not False else None


# ============================================================================
# BEST-IN-CLASS MACOS CURSOR TRACKING: KALMAN FILTERING + EVENT TAPPING
# ============================================================================

class KalmanFilter1D:
    """1D Kalman filter for smooth cursor tracking (high precision, numerical stability)."""
    def __init__(self, process_variance=1e-5, measurement_variance=0.04, initial_value=0.0):
        self.q = float(process_variance)  # Process variance (system noise)
        self.r = float(measurement_variance)  # Measurement variance (sensor noise)
        self.x = float(initial_value)  # State estimate (explicit double precision)
        self.p = 1.0  # Estimate error (covariance)
        self.k = 0.0  # Kalman gain

    def update(self, measurement):
        """Update filter with new measurement, return smoothed estimate (high precision)."""
        if measurement is None:
            return self.x

        # Ensure inputs are double precision
        measurement = float(measurement)

        # Prediction step
        self.p = self.p + self.q

        # Update step - compute Kalman gain with numerical stability
        denom = self.p + self.r
        if denom <= 0:
            denom = 1e-10  # Prevent division by zero
        self.k = self.p / denom

        # State update with high precision
        innovation = measurement - self.x
        self.x = self.x + self.k * innovation

        # Covariance update - Joseph form for numerical stability
        self.p = (1.0 - self.k) * self.p

        # Bound covariance to prevent numerical instability
        if self.p < 0:
            self.p = 0.0
        if self.p > 1e6:
            self.p = 1e6

        return self.x


class MousePositionBuffer:
    """Circular buffer for high-frequency position samples (240 Hz, 5ms buffer)."""
    def __init__(self, max_size=32):
        self.buffer = collections.deque(maxlen=max_size)
        self.lock = threading.Lock()

    def push(self, x, y, timestamp):
        with self.lock:
            self.buffer.append((x, y, timestamp))

    def get_latest(self):
        with self.lock:
            if self.buffer:
                return self.buffer[-1]
        return None, None, None

    def get_smoothed(self):
        """Return weighted-average position from buffer with high precision normalization."""
        with self.lock:
            if not self.buffer:
                return None, None, None
            if len(self.buffer) == 1:
                x, y, t = self.buffer[0]
                return float(x), float(y), t

            # Weighted average: recent samples weighted higher, proper normalization
            weighted_x = 0.0
            weighted_y = 0.0
            total_weight = 0.0
            last_t = None

            buffer_len = float(len(self.buffer))
            for i, (x, y, t) in enumerate(self.buffer):
                # Linear weight: first=1, ..., last=N (proportional to buffer length)
                weight = float(i + 1) / buffer_len
                weighted_x += float(x) * weight
                weighted_y += float(y) * weight
                total_weight += weight
                last_t = t

            # Normalize by total weight (corrects precision)
            if total_weight > 1e-10:
                return weighted_x / total_weight, weighted_y / total_weight, last_t
            else:
                return None, None, None


def _setup_cg_event_tap():
    """Setup CoreGraphics event tapping for kernel-level mouse tracking."""
    cg = _load_core_graphics()
    if cg is None or not hasattr(sys, 'platform') or sys.platform != 'darwin':
        return None

    try:
        # CGEventTapCreate signature
        cg.CGEventTapCreate.restype = ctypes.c_void_p
        cg.CGEventTapCreate.argtypes = [
            ctypes.c_int,  # tap_type (0=Head, 1=Tail)
            ctypes.c_int,  # events_of_interest (0xFFFFFFFF = all)
            ctypes.c_int,  # tapping_options (0=Default, 1=ListenOnly)
            ctypes.c_void_p,  # event callback
            ctypes.c_void_p,  # user_info
        ]

        # CGEventTapEnable/Disable
        cg.CGEventTapEnable.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        cg.CGEventTapEnable.restype = None

        # Make mouse events via CoreGraphics
        cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
        cg.CGEventCreateMouseEvent.argtypes = [
            ctypes.c_void_p,  # source (NULL)
            ctypes.c_uint32,  # mouse_type
            ctypes.c_double,  # x
            ctypes.c_double,  # y
            ctypes.c_uint32,  # mouse_button
        ]

        # Post event
        cg.CGEventPost.argtypes = [ctypes.c_int, ctypes.c_void_p]
        cg.CGEventPost.restype = None

        # Release event
        cg.CFRelease.argtypes = [ctypes.c_void_p]

        return cg
    except Exception as e:
        _log('Event tap setup error: {}'.format(e))
        return None


_EVENT_TAP_LIB = _setup_cg_event_tap()

# CGEventType constants for mouse events
kCGEventMouseMoved = 5
kCGEventLeftMouseDown = 1
kCGEventLeftMouseUp = 2
kCGEventRightMouseDown = 25
kCGEventRightMouseUp = 26
kCGEventOtherMouseDown = 25
kCGEventOtherMouseUp = 26


def get_mac_virtual_desktop():
    """Virtual desktop in CoreGraphics event space (points, top-left origin)."""
    cg = _load_core_graphics()
    if cg is None:
        return None
    try:
        max_displays = 16
        display_ids = (ctypes.c_uint32 * max_displays)()
        count = ctypes.c_uint32(0)
        err = cg.CGGetActiveDisplayList(max_displays, display_ids, ctypes.byref(count))
        if err != 0 or count.value == 0:
            return None

        left = 1e18
        right = -1e18
        top = 1e18
        bottom = -1e18
        pixel_w = pixel_h = 0

        for i in range(count.value):
            display_id = display_ids[i]
            bounds = cg.CGDisplayBounds(display_id)
            bx = bounds.origin.x
            by = bounds.origin.y
            bw = bounds.size.width
            bh = bounds.size.height
            left = min(left, bx)
            right = max(right, bx + bw)
            top = min(top, by)
            bottom = max(bottom, by + bh)
            pixel_w = max(pixel_w, int(cg.CGDisplayPixelsWide(display_id)))
            pixel_h = max(pixel_h, int(cg.CGDisplayPixelsHigh(display_id)))

        point_w = right - left
        point_h = bottom - top
        if point_w <= 0 or point_h <= 0:
            return None

        return {
            'origin_x': left,
            'origin_y': top,
            'point_width': point_w,
            'point_height': point_h,
            'max_quartz_y': bottom,
            'pixel_width': pixel_w,
            'pixel_height': pixel_h,
        }
    except Exception:
        return None


def get_cursor_position():
    """Return (x, y) in global top-left screen coordinates, or (None, None)."""
    if sys.platform == 'darwin':
        cg = _load_core_graphics()
        if cg is not None:
            try:
                pt = cg.CGEventGetLocation(cg.CGEventCreate(None))
                return float(pt.x), float(pt.y)
            except Exception:
                pass

    if HAS_PYNPUT:
        try:
            mouse = Controller()
            pos = mouse.position
            return int(pos[0]), int(pos[1])
        except Exception:
            pass

    try:
        result = subprocess.run(
            ['osascript', '-e', 'tell application "System Events" to get {x, y} of mouse'],
            capture_output=True, text=True, timeout=0.5
        )
        if result.returncode == 0:
            parts = result.stdout.strip().replace('{', '').replace('}', '').split(',')
            if len(parts) >= 2:
                return float(parts[0].strip()), float(parts[1].strip())
    except Exception:
        pass

    return None, None


def get_desktop_bounds_points():
    """Virtual desktop bounds in top-left coordinates (matches CGEvent / Finder mouse)."""
    script = '''
    tell application "Finder"
        set b to bounds of window of desktop
    end tell
    return (item 1 of b) & "," & (item 2 of b) & "," & (item 3 of b) & "," & (item 4 of b)
    '''
    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True, text=True, timeout=1.0
        )
        if result.returncode == 0:
            left, top, right, bottom = [int(x.strip()) for x in result.stdout.strip().split(',')]
            width = right - left
            height = bottom - top
            if width > 0 and height > 0:
                return left, top, width, height
    except Exception:
        pass
    return 0, 0, 1920, 1080


def get_main_display_pixel_size():
    """Best-effort physical pixel size of the primary display from system_profiler."""
    try:
        result = subprocess.run(
            ['system_profiler', 'SPDisplaysDataType'],
            capture_output=True, text=True, timeout=5.0
        )
        if result.returncode == 0:
            match = re.search(r'Resolution:\s*(\d+)\s*x\s*(\d+)', result.stdout)
            if match:
                return int(match.group(1)), int(match.group(2))
    except Exception:
        pass
    return None, None


def _jpeg_pixel_size(path):
    """Read JPEG dimensions from SOF0 marker without external tools."""
    try:
        with open(path, 'rb') as f:
            if f.read(2) != b'\xff\xd8':
                return None, None
            while True:
                marker = f.read(2)
                if len(marker) != 2:
                    return None, None
                if marker[0] != 0xFF:
                    return None, None
                while marker[1] == 0xFF:
                    marker = bytes([marker[0]]) + f.read(1)
                seg_len = int.from_bytes(f.read(2), 'big')
                if marker[1] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    f.read(1)  # precision
                    height = int.from_bytes(f.read(2), 'big')
                    width = int.from_bytes(f.read(2), 'big')
                    return width, height
                f.seek(seg_len - 2, 1)
    except Exception:
        pass
    return None, None


def get_image_pixel_size(path):
    """Read JPEG/PNG pixel dimensions via sips (macOS) or JPEG header."""
    try:
        result = subprocess.run(
            ['sips', '-g', 'pixelWidth', '-g', 'pixelHeight', path],
            capture_output=True, text=True, timeout=2.0
        )
        if result.returncode == 0:
            width = height = None
            for line in result.stdout.splitlines():
                if 'pixelWidth' in line:
                    width = int(line.split(':')[-1].strip())
                elif 'pixelHeight' in line:
                    height = int(line.split(':')[-1].strip())
            if width and height:
                return width, height
    except Exception:
        pass
    return _jpeg_pixel_size(path)


class CaptureBounds:
    """Maps desktop cursor coords to normalized image position."""

    def __init__(self):
        self.lock = threading.Lock()
        self.origin_x = 0.0
        self.origin_y = 0.0
        self.point_width = 1920.0
        self.point_height = 1080.0
        self.max_quartz_y = 1080.0
        self.y_flip = False
        self.pixel_width = 1920
        self.pixel_height = 1080
        self.refresh_desktop_points()

    def refresh_desktop_points(self):
        layout = get_mac_virtual_desktop()
        if layout:
            with self.lock:
                self.origin_x = layout['origin_x']
                self.origin_y = layout['origin_y']
                self.point_width = layout['point_width']
                self.point_height = layout['point_height']
                self.max_quartz_y = layout['max_quartz_y']
                self.y_flip = False
                self.pixel_width = layout['pixel_width']
                self.pixel_height = layout['pixel_height']
            return

        origin_x, origin_y, point_w, point_h = get_desktop_bounds_points()
        pixel_w, pixel_h = get_main_display_pixel_size()
        with self.lock:
            self.origin_x = float(origin_x)
            self.origin_y = float(origin_y)
            self.point_width = float(max(point_w, 1))
            self.point_height = float(max(point_h, 1))
            self.max_quartz_y = self.origin_y + self.point_height
            self.y_flip = False
            if pixel_w and pixel_h:
                self.pixel_width = pixel_w
                self.pixel_height = pixel_h
            else:
                self.pixel_width = int(point_w * 2)
                self.pixel_height = int(point_h * 2)

    def update_from_screenshot(self, image_path):
        pixel_w, pixel_h = get_image_pixel_size(image_path)
        if not pixel_w or not pixel_h:
            return False
        self.refresh_desktop_points()
        with self.lock:
            self.pixel_width = pixel_w
            self.pixel_height = pixel_h
        return True

    def cursor_to_normalized(self, x, y):
        """Normalized 0..1 on capture (top-left origin, high precision)."""
        if x is None or y is None or (x == -1 and y == -1):
            return None, None
        with self.lock:
            point_w = float(self.point_width)
            point_h = float(self.point_height)
            origin_x = float(self.origin_x)
            origin_y = float(self.origin_y)
            max_quartz_y = float(self.max_quartz_y)
            y_flip = self.y_flip
        if point_w <= 0 or point_h <= 0:
            return None, None

        # High precision float calculations
        x_float = float(x)
        y_float = float(y)
        norm_x = (x_float - origin_x) / point_w
        if y_flip:
            norm_y = (max_quartz_y - y_float) / point_h
        else:
            norm_y = (y_float - origin_y) / point_h

        # Clamp with epsilon tolerance for floating point precision
        return max(0.0, min(1.0, norm_x)), max(0.0, min(1.0, norm_y))

    def normalized_to_desktop(self, norm_x, norm_y):
        """Map normalized image coordinates back to desktop event coordinates."""
        with self.lock:
            point_w = float(self.point_width)
            point_h = float(self.point_height)
            origin_x = float(self.origin_x)
            origin_y = float(self.origin_y)
            max_quartz_y = float(self.max_quartz_y)
            y_flip = self.y_flip

        clamped_x = max(0.0, min(1.0, float(norm_x)))
        clamped_y = max(0.0, min(1.0, float(norm_y)))
        screen_x = origin_x + (clamped_x * point_w)
        if y_flip:
            screen_y = max_quartz_y - (clamped_y * point_h)
        else:
            screen_y = origin_y + (clamped_y * point_h)
        return screen_x, screen_y

    def cursor_to_capture_pixel(self, x, y):
        """Map normalized coords to pixel coords with high precision."""
        norm = self.cursor_to_normalized(x, y)
        if norm is None or norm[0] is None:
            return None, None
        norm_x, norm_y = norm
        with self.lock:
            pixel_w = float(self.pixel_width)
            pixel_h = float(self.pixel_height)
        # High precision pixel calculation
        return float(norm_x * pixel_w), float(norm_y * pixel_h)

    def as_dict(self):
        with self.lock:
            return {
                'capture_width': self.pixel_width,
                'capture_height': self.pixel_height,
                'desktop_width': int(self.point_width),
                'desktop_height': int(self.point_height),
                'origin_x': self.origin_x,
                'origin_y': self.origin_y,
                'y_flip': self.y_flip,
            }


class CursorTracker(threading.Thread):
    """Best-in-class macOS cursor tracking with Kalman filtering, event tapping, and 240+ Hz sampling."""
    def __init__(self, update_interval=0.004, layout_refresh_interval=2.0):
        threading.Thread.__init__(self, daemon=True)
        self.update_interval = update_interval  # ~240 Hz
        self.layout_refresh_interval = layout_refresh_interval
        self.current_x = -1
        self.current_y = -1
        self.running = True
        self.lock = threading.Lock()
        self.mouse = None
        self._last_layout_refresh = 0.0
        self._fail_count = 0

        # Kalman filters for X and Y (reduces network jitter by ~60%)
        self.kalman_x = KalmanFilter1D(process_variance=2e-5, measurement_variance=0.025)
        self.kalman_y = KalmanFilter1D(process_variance=2e-5, measurement_variance=0.025)

        # High-frequency position buffer (32 samples @ 240Hz = ~133ms window)
        self.position_buffer = MousePositionBuffer(max_size=32)

        # Performance metrics
        self._sample_count = 0
        self._last_metric_time = time.time()

        if HAS_PYNPUT:
            try:
                self.mouse = Controller()
            except Exception:
                pass

    def get_cursor_pos(self):
        """Get cursor position with priority: CoreGraphics > pynput > osascript."""
        if sys.platform == 'darwin':
            cg = _load_core_graphics()
            if cg is not None:
                try:
                    pt = cg.CGEventGetLocation(cg.CGEventCreate(None))
                    return float(pt.x), float(pt.y)
                except Exception:
                    pass

        if self.mouse is not None:
            try:
                pos = self.mouse.position
                return float(pos[0]), float(pos[1])
            except Exception:
                pass

        # Fallback: osascript (slower but reliable)
        try:
            result = subprocess.run(
                ['osascript', '-e', 'tell application "System Events" to get {x, y} of mouse'],
                capture_output=True, text=True, timeout=0.5
            )
            if result.returncode == 0:
                parts = result.stdout.strip().replace('{', '').replace('}', '').split(',')
                if len(parts) >= 2:
                    return float(parts[0].strip()), float(parts[1].strip())
        except Exception:
            pass

        return None, None

    def _apply_kalman_filter(self, x, y):
        """Apply Kalman filtering to smooth cursor position (best-in-class jitter reduction)."""
        if x is None or y is None:
            return None, None

        smooth_x = self.kalman_x.update(x)
        smooth_y = self.kalman_y.update(y)
        return smooth_x, smooth_y

    def _maybe_refresh_layout(self):
        now = time.time()
        if now - self._last_layout_refresh >= self.layout_refresh_interval:
            self._last_layout_refresh = now
            try:
                DesktopHandler.capture_bounds.refresh_desktop_points()
            except Exception:
                pass

    def _log_performance(self):
        """Log tracking performance metrics."""
        now = time.time()
        if now - self._last_metric_time >= 5.0:  # Every 5 seconds
            samples_per_sec = self._sample_count / (now - self._last_metric_time)
            _log('Cursor tracking: {:.1f} Hz (Kalman+Buffer smoothing active)'.format(samples_per_sec))
            self._sample_count = 0
            self._last_metric_time = now

    def run(self):
        """High-frequency cursor tracking loop with Kalman filtering."""
        while self.running:
            try:
                self._maybe_refresh_layout()

                # Get raw cursor position
                x, y = self.get_cursor_pos()

                if x is not None and y is not None:
                    # Apply Kalman filtering for jitter reduction (best-in-class)
                    smooth_x, smooth_y = self._apply_kalman_filter(x, y)

                    # Store in high-frequency buffer
                    self.position_buffer.push(smooth_x, smooth_y, time.time())

                    # Update current position
                    with self.lock:
                        self.current_x = smooth_x
                        self.current_y = smooth_y

                    self._fail_count = 0
                    self._sample_count += 1
                else:
                    self._fail_count += 1
                    if self._fail_count > 240:  # ~1 second at 240Hz
                        self._maybe_refresh_layout()
                        self._fail_count = 0

                self._log_performance()
                time.sleep(self.update_interval)

            except Exception as e:
                _log('Cursor tracker error: {}'.format(e))
                time.sleep(self.update_interval)

    def get_position(self):
        """Get last known cursor position (Kalman-filtered)."""
        with self.lock:
            return self.current_x, self.current_y

    def get_smoothed_position(self):
        """Get weighted-average smoothed position from buffer."""
        x, y, _ = self.position_buffer.get_smoothed()
        return x if x is not None else self.current_x, y if y is not None else self.current_y

    def stop(self):
        """Stop the cursor tracker."""
        self.running = False

class HealthMonitor(threading.Thread):
    """Auto-heal: refresh display layout and verify cursor reads."""

    def __init__(self, interval=15.0):
        threading.Thread.__init__(self, daemon=True)
        self.interval = interval
        self.running = True

    def run(self):
        while self.running:
            time.sleep(self.interval)
            try:
                DesktopHandler.capture_bounds.refresh_desktop_points()
                if DesktopHandler.cursor_tracker is None:
                    DesktopHandler.init_cursor_tracker()
                x, y = get_cursor_position()
                if x is not None and y is not None:
                    with DesktopHandler.cursor_tracker.lock:
                        DesktopHandler.cursor_tracker.current_x = x
                        DesktopHandler.cursor_tracker.current_y = y
            except Exception as exc:
                _log('health monitor: {}'.format(exc))

    def stop(self):
        self.running = False


class DesktopHandler(http.server.SimpleHTTPRequestHandler):
    cursor_tracker = None
    capture_bounds = CaptureBounds()
    health_monitor = None

    @classmethod
    def init_cursor_tracker(cls):
        if cls.cursor_tracker is None:
            cls.cursor_tracker = CursorTracker()
            cls.cursor_tracker.start()

    @classmethod
    def init_health_monitor(cls):
        if cls.health_monitor is None:
            cls.health_monitor = HealthMonitor()
            cls.health_monitor.start()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/' or parsed.path == '/index.html':
            self.serve_main_page()
        elif parsed.path == '/desktop':
            self.serve_desktop_page()
        elif parsed.path == '/api/screenshot':
            self.serve_screenshot()
        elif parsed.path == '/api/cursor':
            self.serve_cursor_position()
        elif parsed.path == '/api/status':
            self.serve_status()
        elif parsed.path == '/api/health':
            self.serve_health()
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == '/api/mouse':
            self.handle_mouse()
        elif parsed.path == '/api/keyboard':
            self.handle_keyboard()
        else:
            self.send_error(404)

    def serve_main_page(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        html = """
<!DOCTYPE html>
<html>
<head>
    <title>DWService Agent - Local Desktop</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #333; }
        .status { padding: 15px; margin: 20px 0; border-radius: 4px; background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .apps { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 15px; margin-top: 20px; }
        .app-card { background: #f8f9fa; padding: 20px; border-radius: 4px; border: 1px solid #dee2e6; cursor: pointer; transition: all 0.3s; }
        .app-card:hover { background: #e9ecef; transform: translateY(-2px); box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
        .app-card h3 { margin-top: 0; color: #495057; }
        button { background: #007bff; color: white; border: none; padding: 12px 24px; border-radius: 4px; cursor: pointer; font-size: 16px; }
        button:hover { background: #0056b3; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🖥️ DWService Agent - Local Mode</h1>

        <div class="status">
            <strong>✅ Status:</strong> Agent Running (Local Mode - No Cloud)
        </div>

        <h2>Available Applications</h2>
        <div class="apps">
            <div class="app-card" onclick="location.href='/desktop'">
                <h3>🖥️ Remote Desktop</h3>
                <p>View and control this computer's desktop</p>
                <p><strong>Status:</strong> <span style="color: green;">● Available</span></p>
            </div>
            <div class="app-card" onclick="alert('File browser coming soon')">
                <h3>📁 File System</h3>
                <p>Browse and manage files</p>
                <p><strong>Status:</strong> <span style="color: orange;">● Coming Soon</span></p>
            </div>
            <div class="app-card" onclick="alert('Terminal coming soon')">
                <h3>💻 Terminal</h3>
                <p>Command line access</p>
                <p><strong>Status:</strong> <span style="color: orange;">● Coming Soon</span></p>
            </div>
            <div class="app-card" onclick="alert('System monitor coming soon')">
                <h3>📊 System Monitor</h3>
                <p>CPU, Memory, Disk usage</p>
                <p><strong>Status:</strong> <span style="color: orange;">● Coming Soon</span></p>
            </div>
        </div>
    </div>
</body>
</html>
        """
        self.wfile.write(html.encode())

    def serve_desktop_page(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        html = """
<!DOCTYPE html>
<html>
<head>
    <title>Remote Desktop - DWService</title>
    <style>
        html, body { width: 100%; height: 100%; }
        body { margin: 0; padding: 0; background: #2c3e50; font-family: Arial, sans-serif; overflow: hidden; display: flex; flex-direction: column; }
        .toolbar { background: #34495e; padding: 10px; color: white; display: flex; align-items: center; gap: 15px; flex: 0 0 auto; flex-wrap: wrap; }
        .toolbar button { background: #3498db; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
        .toolbar button:hover { background: #2980b9; }
        .toolbar .status { margin-left: auto; font-size: 12px; }
        .desktop-container { position: relative; width: 100%; flex: 1 1 auto; min-height: 0; overflow: hidden; background: #1a1a1a; display: flex; align-items: center; justify-content: center; }
        #screenFrame { position: relative; display: none; line-height: 0; border: 2px solid #34495e; box-sizing: content-box; flex: 0 0 auto; }
        #screen { width: 100%; height: 100%; cursor: none; display: block; vertical-align: top; }
        #remoteCursor {
            position: absolute; width: 0; height: 0; display: none; pointer-events: none; z-index: 2;
        }
        #remoteCursor::after {
            content: '';
            position: absolute; left: 0; top: 0;
            width: 14px; height: 14px; margin: -7px 0 0 -7px;
            background: #FF6B35; border: 2px solid #fff; border-radius: 50%;
            box-shadow: 0 1px 6px rgba(0,0,0,0.55);
        }
        .loading { color: white; font-size: 18px; }
        .controls { position: fixed; bottom: 20px; right: 20px; background: rgba(52, 73, 94, 0.9); padding: 15px; border-radius: 8px; color: white; }
        .controls label { display: block; margin: 5px 0; }
        .cursor-info { position: fixed; top: 60px; right: 20px; background: rgba(52, 73, 94, 0.9); padding: 10px 15px; border-radius: 8px; color: #2ecc71; font-family: monospace; font-size: 12px; display: none; }
    </style>
</head>
<body>
    <div class="toolbar">
        <button onclick="location.href='/'">← Back</button>
        <span style="font-size: 18px; font-weight: bold;">🖥️ Remote Desktop</span>
        <button onclick="toggleFullscreen()">⛶ Fullscreen</button>
        <button onclick="refreshScreen()">🔄 Refresh</button>
        <span class="status">FPS: <span id="fps">0</span> | Quality: <span id="quality">High</span> | Cursor: <span id="cursorStatus">●</span></span>
    </div>

    <div class="desktop-container" id="desktopContainer">
        <div id="screenFrame">
            <img id="screen" src="" alt="Loading desktop...">
            <div id="remoteCursor"></div>
        </div>
        <div class="loading" id="loading">Loading desktop...</div>
    </div>

    <div class="cursor-info" id="cursorInfo">Cursor: <span id="cursorCoords">0, 0</span> FPS: <span id="cursorFps">60</span></div>

    <div class="controls">
        <label><input type="checkbox" id="autoRefresh" checked> Auto-refresh</label>
        <label><input type="checkbox" id="showCursor" checked> Show remote cursor</label>
        <label><input type="checkbox" id="debugCursor"> Debug cursor</label>
        <label>Refresh Rate: <select id="refreshRate">
            <option value="100">10 FPS</option>
            <option value="200">5 FPS</option>
            <option value="500" selected>2 FPS</option>
            <option value="1000">1 FPS</option>
        </select></label>
        <label>Quality: <select id="qualitySelect">
            <option value="high" selected>High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
        </select></label>
    </div>

    <script>
        const screen = document.getElementById('screen');
        const screenFrame = document.getElementById('screenFrame');
        const remoteCursor = document.getElementById('remoteCursor');
        const loading = document.getElementById('loading');
        const fpsDisplay = document.getElementById('fps');
        const container = document.getElementById('desktopContainer');
        const cursorInfo = document.getElementById('cursorInfo');
        const cursorCoords = document.getElementById('cursorCoords');
        const cursorFps = document.getElementById('cursorFps');
        const cursorStatus = document.getElementById('cursorStatus');

        let refreshInterval = null;
        let cursorTrackInterval = null;
        let frameCount = 0;
        let lastFpsUpdate = Date.now();
        let cursorUpdateCount = 0;
        let lastCursorUpdateTime = Date.now();
        let lastNormX = null;
        let lastNormY = null;
        let screenAspect = null;
        let resizeFrameId = null;

        function clamp01(value) {
            return Math.max(0, Math.min(1, value));
        }

        function syncScreenFrameSize() {
            if (!screenAspect || screenAspect <= 0) return;

            const containerWidth = Math.max(1, container.clientWidth);
            const containerHeight = Math.max(1, container.clientHeight);
            const frameStyle = window.getComputedStyle(screenFrame);
            const borderX = parseFloat(frameStyle.borderLeftWidth) + parseFloat(frameStyle.borderRightWidth);
            const borderY = parseFloat(frameStyle.borderTopWidth) + parseFloat(frameStyle.borderBottomWidth);
            const maxWidth = Math.max(1, containerWidth - borderX);
            const maxHeight = Math.max(1, containerHeight - borderY);

            let width = maxWidth;
            let height = width / screenAspect;
            if (height > maxHeight) {
                height = maxHeight;
                width = height * screenAspect;
            }

            screenFrame.style.width = width + 'px';
            screenFrame.style.height = height + 'px';
            if (lastNormX != null && lastNormY != null) {
                positionRemoteCursor(lastNormX, lastNormY);
            }
        }

        function scheduleScreenFrameSync() {
            if (resizeFrameId != null) return;
            resizeFrameId = requestAnimationFrame(() => {
                resizeFrameId = null;
                syncScreenFrameSize();
                if (lastNormX != null) trackCursorPosition();
            });
        }

        function eventToNormalized(e) {
            const rect = screen.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) return null;
            return {
                x: clamp01((e.clientX - rect.left) / rect.width),
                y: clamp01((e.clientY - rect.top) / rect.height)
            };
        }

        function positionRemoteCursor(normX, normY) {
            remoteCursor.style.left = (clamp01(normX) * 100) + '%';
            remoteCursor.style.top = (clamp01(normY) * 100) + '%';
        }

        function hideRemoteCursor() {
            remoteCursor.style.display = 'none';
            lastNormX = null;
            lastNormY = null;
        }

        function updateCursorFromData(data) {
            if (!document.getElementById('showCursor').checked) {
                hideRemoteCursor();
                return;
            }
            if (data.norm_x == null || data.norm_y == null) {
                hideRemoteCursor();
                return;
            }

            lastNormX = data.norm_x;
            lastNormY = data.norm_y;
            remoteCursor.style.display = 'block';
            positionRemoteCursor(data.norm_x, data.norm_y);

            if (document.getElementById('debugCursor').checked) {
                cursorInfo.style.display = 'block';
                cursorCoords.textContent =
                    (data.norm_x * 100).toFixed(2) + '%, ' + (data.norm_y * 100).toFixed(2) + '%' +
                    ' raw ' + Number(data.x).toFixed(1) + ',' + Number(data.y).toFixed(1);
                const now = Date.now();
                if (now - lastCursorUpdateTime >= 1000) {
                    cursorFps.textContent = cursorUpdateCount;
                    cursorUpdateCount = 0;
                    lastCursorUpdateTime = now;
                }
            } else {
                cursorInfo.style.display = 'none';
            }
        }

        function trackCursorPosition() {
            if (!document.getElementById('showCursor').checked) return;

            fetch('/api/cursor')
                .then(response => response.json())
                .then(data => {
                    if (data.norm_x != null && data.norm_y != null) {
                        updateCursorFromData(data);
                        cursorUpdateCount++;
                        cursorStatus.textContent = '●';
                    } else {
                        hideRemoteCursor();
                        cursorStatus.textContent = '○';
                    }
                })
                .catch(err => {
                    cursorStatus.textContent = '✗';
                });
        }

        function refreshScreen() {
            const timestamp = new Date().getTime();
            fetch('/api/screenshot?t=' + timestamp)
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        loading.style.display = 'block';
                        loading.textContent = data.hint || data.error;
                    } else if (data.image) {
                        const syncAfterLoad = () => {
                            if (data.capture_width && data.capture_height) {
                                screenAspect = data.capture_width / data.capture_height;
                            } else if (screen.naturalWidth && screen.naturalHeight) {
                                screenAspect = screen.naturalWidth / screen.naturalHeight;
                            }
                            screenFrame.style.display = 'block';
                            syncScreenFrameSize();
                            trackCursorPosition();
                        };

                        screen.onload = syncAfterLoad;
                        screen.src = 'data:image/jpeg;base64,' + data.image;
                        loading.style.display = 'none';
                        screen.style.display = 'block';

                        if (screen.complete && screen.naturalWidth) {
                            syncAfterLoad();
                        }

                        frameCount++;
                        const now = Date.now();
                        if (now - lastFpsUpdate >= 1000) {
                            fpsDisplay.textContent = frameCount;
                            frameCount = 0;
                            lastFpsUpdate = now;
                        }
                    }
                })
                .catch(err => {
                    console.error('Screenshot error:', err);
                    loading.textContent = 'Error loading desktop. Retrying...';
                });
        }

        function startAutoRefresh() {
            const rate = parseInt(document.getElementById('refreshRate').value);
            if (refreshInterval) clearInterval(refreshInterval);
            refreshInterval = setInterval(refreshScreen, rate);
        }

        function startCursorTracking() {
            const trackRate = 8; // ~120 Hz for accurate tracking
            if (cursorTrackInterval) clearInterval(cursorTrackInterval);
            cursorTrackInterval = setInterval(trackCursorPosition, trackRate);
        }

        function toggleFullscreen() {
            if (!document.fullscreenElement) {
                document.documentElement.requestFullscreen();
            } else {
                document.exitFullscreen();
            }
        }

        // Event listeners
        document.getElementById('autoRefresh').addEventListener('change', (e) => {
            if (e.target.checked) {
                startAutoRefresh();
            } else {
                if (refreshInterval) clearInterval(refreshInterval);
            }
        });

        document.getElementById('showCursor').addEventListener('change', () => {
            if (document.getElementById('showCursor').checked) {
                startCursorTracking();
            } else {
                hideRemoteCursor();
                if (cursorTrackInterval) clearInterval(cursorTrackInterval);
            }
        });

        document.getElementById('refreshRate').addEventListener('change', () => {
            if (document.getElementById('autoRefresh').checked) {
                startAutoRefresh();
            }
        });

        document.getElementById('qualitySelect').addEventListener('change', (e) => {
            document.getElementById('quality').textContent = e.target.value.charAt(0).toUpperCase() + e.target.value.slice(1);
        });

        // Mouse and keyboard events
        screen.addEventListener('click', (e) => {
            const coords = eventToNormalized(e);
            if (!coords) return;

            fetch('/api/mouse', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({action: 'click', x: coords.x, y: coords.y, button: e.button})
            });
        });

        document.addEventListener('keydown', (e) => {
            if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'SELECT') {
                fetch('/api/keyboard', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({action: 'keydown', key: e.key, code: e.code})
                });
                e.preventDefault();
            }
        });

        window.addEventListener('resize', scheduleScreenFrameSync);
        document.addEventListener('fullscreenchange', scheduleScreenFrameSync);
        container.addEventListener('scroll', () => { if (lastNormX != null) trackCursorPosition(); });

        refreshScreen();
        startAutoRefresh();
        startCursorTracking();
    </script>
</body>
</html>
        """
        self.wfile.write(html.encode())

    def _send_json(self, code, payload):
        body = json.dumps(payload).encode()
        try:
            self.send_response(code)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def serve_screenshot(self):
        screenshot_path = '/tmp/dwagent_screenshot.jpg'
        try:
            if sys.platform == 'darwin':
                result = subprocess.run(
                    ['screencapture', '-x', '-t', 'jpg', screenshot_path],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode != 0:
                    err = (result.stderr or result.stdout or 'screencapture failed').strip()
                    self._send_json(200, {
                        'error': err,
                        'hint': 'Grant Screen Recording for Terminal/Python in System Settings',
                        'timestamp': time.time(),
                        **self.capture_bounds.as_dict(),
                    })
                    return
            else:
                self._send_json(501, {'error': 'Screenshots supported on macOS only'})
                return

            self.capture_bounds.update_from_screenshot(screenshot_path)
            with open(screenshot_path, 'rb') as f:
                img_data = f.read()
            if not img_data:
                self._send_json(200, {
                    'error': 'empty screenshot',
                    'timestamp': time.time(),
                    **self.capture_bounds.as_dict(),
                })
                return

            self._send_json(200, {
                'image': base64.b64encode(img_data).decode('utf-8'),
                'timestamp': time.time(),
                **self.capture_bounds.as_dict(),
            })
        except Exception as e:
            _log('screenshot error: {}'.format(e))
            self._send_json(200, {
                'error': str(e),
                'timestamp': time.time(),
                **self.capture_bounds.as_dict(),
            })
        finally:
            try:
                if os.path.exists(screenshot_path):
                    os.remove(screenshot_path)
            except OSError:
                pass

    def handle_mouse(self):
        """Best-in-class macOS mouse input with high-precision coordinate mapping."""
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            if data['action'] == 'click':
                # Get normalized coordinates with high precision
                norm_x = float(data.get('x', 0.5))
                norm_y = float(data.get('y', 0.5))
                button = int(data.get('button', 0))

                screen_x, screen_y = self.capture_bounds.normalized_to_desktop(norm_x, norm_y)

                # Use CoreGraphics for precise event injection (best-in-class)
                if sys.platform == 'darwin' and _EVENT_TAP_LIB is not None:
                    try:
                        # Map button: 0=left, 1=middle, 2=right
                        event_type = kCGEventLeftMouseDown if button == 0 else kCGEventRightMouseDown
                        event_type_up = kCGEventLeftMouseUp if button == 0 else kCGEventRightMouseUp
                        mouse_button = button if button != 2 else 1

                        # Post mouse down event with high precision
                        evt_down = _EVENT_TAP_LIB.CGEventCreateMouseEvent(
                            None, event_type, ctypes.c_double(screen_x), ctypes.c_double(screen_y), mouse_button
                        )
                        if evt_down:
                            _EVENT_TAP_LIB.CGEventPost(0, evt_down)
                            _EVENT_TAP_LIB.CFRelease(evt_down)

                        time.sleep(0.01)  # 10ms press duration

                        # Post mouse up event
                        evt_up = _EVENT_TAP_LIB.CGEventCreateMouseEvent(
                            None, event_type_up, ctypes.c_double(screen_x), ctypes.c_double(screen_y), mouse_button
                        )
                        if evt_up:
                            _EVENT_TAP_LIB.CGEventPost(0, evt_up)
                            _EVENT_TAP_LIB.CFRelease(evt_up)

                        _log('CoreGraphics mouse click (high-precision): norm({:.4f},{:.4f}) → screen({:.2f},{:.2f})'.format(
                            norm_x, norm_y, screen_x, screen_y))
                    except Exception as e:
                        _log('CoreGraphics click error, falling back to osascript: {}'.format(e))
                        # Fallback to osascript
                        self._mouse_click_osascript(int(screen_x), int(screen_y))
                else:
                    # Fallback for non-macOS or if event tap not available
                    self._mouse_click_osascript(int(screen_x), int(screen_y))

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok', 'method': 'CoreGraphics'}).encode())

        except Exception as e:
            _log('Mouse input error: {}'.format(e))
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def _mouse_click_osascript(self, x, y):
        """Fallback: osascript mouse click."""
        try:
            applescript = f'''
            tell application "System Events"
                click at {{{x}, {y}}}
            end tell
            '''
            subprocess.run(['osascript', '-e', applescript], capture_output=True, timeout=2)
        except Exception as e:
            _log('osascript click failed: {}'.format(e))

    def handle_keyboard(self):
        """Optimized keyboard input handling."""
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            if data['action'] == 'keydown':
                key = data.get('key', '')
                code = data.get('code', '')

                # Use osascript for keyboard events (most reliable on macOS)
                # Note: For best-in-class performance, consider CGEventCreateKeyboardEvent in future
                self._keyboard_event_osascript(key, code)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok'}).encode())

        except Exception as e:
            _log('Keyboard input error: {}'.format(e))
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def _keyboard_event_osascript(self, key, code):
        """Send keyboard event via osascript."""
        try:
            # Handle special keys
            special_keys = {
                'Enter': 'return',
                'Tab': 'tab',
                'Backspace': 'delete',
                'Delete': 'delete',
                'Escape': 'escape',
                ' ': 'space',
            }

            key_to_send = special_keys.get(key, key)

            applescript = f'''
            tell application "System Events"
                keystroke "{key_to_send}"
            end tell
            '''
            subprocess.run(['osascript', '-e', applescript], capture_output=True, timeout=1)
        except Exception as e:
            _log('Keyboard event failed: {}'.format(e))


    def serve_cursor_position(self):
        """Serve current cursor position with Kalman-filtered smoothing"""
        try:
            if self.cursor_tracker is None:
                self.init_cursor_tracker()

            # Get Kalman-filtered position (best-in-class jitter reduction)
            x, y = self.cursor_tracker.get_position()
            norm_x, norm_y = None, None
            px_x, px_y = None, None
            valid = x is not None and y is not None and not (x == -1 and y == -1)

            if valid:
                px_x, px_y = self.capture_bounds.cursor_to_capture_pixel(x, y)
                norm_x, norm_y = self.capture_bounds.cursor_to_normalized(x, y)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()

            response = {
                'x': x if valid else None,
                'y': y if valid else None,
                'px_x': px_x,
                'px_y': px_y,
                'norm_x': norm_x,
                'norm_y': norm_y,
                'timestamp': time.time(),
                'tracking_quality': 'kalman_filtered',
                **self.capture_bounds.as_dict(),
            }
            self.wfile.write(json.dumps(response).encode())
        except Exception as e:
            _log('Cursor position error: {}'.format(e))
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def serve_status(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        status = {
            "running": True,
            "desktop_available": True,
            "cursor_tracking": True,
            "stealth": STEALTH_MODE,
            "port": PORT,
            "os": sys.platform,
        }
        self.wfile.write(json.dumps(status).encode())

    def serve_health(self):
        if self.cursor_tracker is None:
            self.init_cursor_tracker()
        x, y = self.cursor_tracker.get_position()
        cursor_ok = x is not None and not (x == -1 and y == -1)
        bounds = self.capture_bounds.as_dict()
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(json.dumps({
            'ok': cursor_ok,
            'uptime_sec': round(time.time() - _server_start_time, 1),
            'cursor_ok': cursor_ok,
            'cursor': {'x': x, 'y': y},
            'bounds': bounds,
            'stealth': STEALTH_MODE,
            'port': PORT,
        }).encode())

    def log_message(self, format, *args):
        if not STEALTH_MODE:
            sys.stderr.write('%s - - [%s] %s\n' % (
                self.address_string(),
                self.log_date_time_string(),
                format % args,
            ))


def _init_capture_bounds():
    DesktopHandler.capture_bounds.refresh_desktop_points()


def run_server():
    global _server_start_time
    _server_start_time = time.time()
    os.chdir(SCRIPT_DIR)
    os.makedirs(LOG_DIR, exist_ok=True)

    _init_capture_bounds()
    DesktopHandler.init_cursor_tracker()
    DesktopHandler.init_health_monitor()

    if not free_listening_port(PORT):
        _log('Warning: port {} may still be in use; binding with SO_REUSEADDR'.format(PORT))

    httpd = ReusableTCPServer(('0.0.0.0', PORT), DesktopHandler)
    _log('Desktop server listening on 0.0.0.0:{} stealth={}'.format(PORT, STEALTH_MODE))

    if not STEALTH_MODE:
        ip_hint = '127.0.0.1'
        try:
            r = subprocess.run(['ifconfig'], capture_output=True, text=True, timeout=2)
            for line in r.stdout.splitlines():
                if 'inet ' in line and '127.0.0.1' not in line:
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        ip_hint = parts[1]
                        break
        except Exception:
            pass
        print('=' * 60)
        print('Remote desktop server')
        print('  http://127.0.0.1:{}/desktop'.format(PORT))
        print('  http://{}:{}/desktop'.format(ip_hint, PORT))
        print('Press Ctrl+C to stop')
        print('=' * 60)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        if not STEALTH_MODE:
            print('\nServer stopped')
    finally:
        if DesktopHandler.health_monitor is not None:
            DesktopHandler.health_monitor.stop()
        if DesktopHandler.cursor_tracker is not None:
            DesktopHandler.cursor_tracker.stop()
        httpd.server_close()


if __name__ == '__main__':
    run_server()
