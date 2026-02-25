local app = hs.application.get("{{app_name}}")
if app then
    app:kill()
    return "quit"
else
    return "not_found"
end
