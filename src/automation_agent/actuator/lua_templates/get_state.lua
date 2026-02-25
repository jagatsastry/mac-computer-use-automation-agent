local app = hs.application.frontmostApplication()
local win = app and app:focusedWindow()
return hs.json.encode({
    app_name = app and app:name() or "",
    app_bundle = app and app:bundleID() or "",
    window_title = win and win:title() or "",
    window_frame = win and hs.json.encode(win:frame()) or ""
})
