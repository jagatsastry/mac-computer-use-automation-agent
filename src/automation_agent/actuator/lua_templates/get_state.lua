local app = hs.application.frontmostApplication()
local win = app and app:focusedWindow()
local frame = win and win:frame() or nil
return hs.json.encode({
    app_name = app and app:name() or "",
    app_bundle = app and app:bundleID() or "",
    window_title = win and win:title() or "",
    window_x = frame and frame.x or 0,
    window_y = frame and frame.y or 0,
    window_w = frame and frame.w or 0,
    window_h = frame and frame.h or 0
})
