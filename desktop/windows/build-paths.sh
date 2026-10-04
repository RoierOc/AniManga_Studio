#!/usr/bin/env bash
# Defaults del instalador derivados del perfil Windows; permite rutas explícitas.
if [[ -z "${WIN_BUILD_DIR:-}" || -z "${MPV_SOURCE_WIN:-}" || -z "${LIBMPV_DIR:-}" ]]; then
    WINDOWS_PROFILE="${WINDOWS_PROFILE:-$(cmd.exe /c echo %USERPROFILE% 2>/dev/null | tr -d '\r' | tail -n 1)}"
    if [[ -z "$WINDOWS_PROFILE" || "$WINDOWS_PROFILE" == '%USERPROFILE%' ]]; then
        printf '%s\n' 'No se pudo detectar el perfil Windows. Configura las rutas de compilación.' >&2
        return 1
    fi
    WINDOWS_PROFILE_WSL="$(wslpath -u "$WINDOWS_PROFILE")" || return 1
    WIN_BUILD_DIR="${WIN_BUILD_DIR:-$WINDOWS_PROFILE_WSL/animanga-native/native}"
    MPV_SOURCE_WIN="${MPV_SOURCE_WIN:-$WINDOWS_PROFILE\animanga-native\libmpv}"
    LIBMPV_DIR="${LIBMPV_DIR:-$WINDOWS_PROFILE_WSL/animanga-native/libmpv}"
fi
