fn main() {
    // libmpv se enlaza contra mpv.lib generado desde la DLL (var MPV_SOURCE apunta
    // a la carpeta que la contiene). Solo aplica a la build de Windows.
    if let Ok(src) = std::env::var("MPV_SOURCE") {
        println!("cargo:rustc-link-search=native={src}");
    }
    println!("cargo:rerun-if-env-changed=MPV_SOURCE");

    // Icono embebido: sin esto el .exe/ventana/barra de tareas salen con el icono
    // genérico de Windows. `animanga.ico` vive en la raíz del crate para viajar con
    // él a la carpeta de build de Windows. Solo en target Windows (necesita rc.exe
    // del MSVC toolchain, que ya está presente porque compilamos con cargo.exe).
    #[cfg(windows)]
    {
        let mut res = winresource::WindowsResource::new();
        res.set_icon("animanga.ico");
        if let Err(e) = res.compile() {
            println!("cargo:warning=no se pudo embeber el icono: {e}");
        }
    }
    println!("cargo:rerun-if-changed=animanga.ico");
}
