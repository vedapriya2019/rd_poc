# Remote Desktop Mouse Tracking

This note documents the local remote desktop mouse tracking and input behavior
implemented in `core/desktop_server.py`.

## Summary

The desktop viewer now keeps browser coordinates, screenshot coordinates, and
macOS input coordinates in the same top-left coordinate model. This fixes the
previous inverted Y axis and keeps clicks aligned when the browser window is
resized or toggled into fullscreen.

The cursor tracker also samples at a high rate, smooths raw cursor positions,
and uses CoreGraphics for mouse click injection when available.

## Coordinate Model

- Browser click positions are normalized from the actual rendered image rect.
- Server-side desktop bounds use top-left origin coordinates.
- `CaptureBounds.cursor_to_normalized()` maps desktop cursor coordinates to
  normalized image coordinates.
- `CaptureBounds.normalized_to_desktop()` maps browser click coordinates back
  to desktop input coordinates.
- The API still returns `y_flip` for compatibility, but current macOS
  CoreGraphics paths report `false`.

## Resize Handling

The desktop page sizes `#screenFrame` from the screenshot aspect ratio instead
of relying on CSS `max-width`/`max-height`. The frame is recalculated after:

- a screenshot loads
- browser resize
- fullscreen changes

The page shell is a flex column, so the desktop area uses the remaining space
below the toolbar even when the toolbar wraps on narrow windows.

## Tracking And Input

- `CursorTracker` samples cursor position at roughly 240 Hz.
- `KalmanFilter1D` smooths X and Y independently.
- `MousePositionBuffer` keeps recent samples and can return a weighted average.
- Mouse clicks use `CGEventCreateMouseEvent` and `CGEventPost` on macOS, with
  an osascript fallback.
- `/api/cursor` includes `tracking_quality: "kalman_filtered"`.

## Useful Checks

```bash
python3 -m py_compile core/desktop_server.py
curl http://127.0.0.1:8080/api/health
curl http://127.0.0.1:8080/api/cursor
```

Expected health/cursor responses should show `y_flip: false` on the current
macOS CoreGraphics path, with `norm_y` increasing from the top of the displayed
screen toward the bottom.
