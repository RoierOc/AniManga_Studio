/* Anime4K sobre WebGPU para el player web — mismos shaders CNN (bloc97) que el
 * usuario ya usa en mpv, portados a compute shaders (paquete anime4k-webgpu).
 *
 * Bucle propio en vez del render() del paquete: necesitamos parar/rearmar al
 * cambiar de tier, de episodio o de pista sin fugar el device ni el rVFC loop.
 *
 * Flujo por frame: vídeo → copyExternalImageToTexture → pipelines Anime4K
 * (compute) → blit a un <canvas> webgpu que se muestra ENCIMA del <video>
 * (el vídeo sigue reproduciendo oculto: audio, subs y timing intactos). */
// Import dinámico: los pesos CNN pesan ~4 MB — solo se descargan la primera
// vez que se activa un tier, no en la carga de la app.
export const A4K_MODES = [
  { id: 'off',  label: 'Desactivado' },
  { id: 'LITE', label: 'Ligero — Deblur DoG (ideal iGPU)' },
  { id: 'A',    label: 'A — CNN (restaura + x2)' },
  { id: 'B',    label: 'B — CNN (suave)' },
  { id: 'C',    label: 'C — CNN (denoise)' },
  { id: 'AA',   label: 'A+A — CNN alta calidad' },
  { id: 'BB',   label: 'B+B — CNN alta calidad' },
  { id: 'CA',   label: 'C+A — CNN alta calidad' },
]
let _mod = null
async function presets() {
  if (!_mod) _mod = await import('anime4k-webgpu')
  const m = _mod
  return { LITE: m.DoG, A: m.ModeA, B: m.ModeB, C: m.ModeC, AA: m.ModeAA, BB: m.ModeBB, CA: m.ModeCA }
}

const BLIT_WGSL = /* wgsl */ `
@vertex fn vs(@builtin(vertex_index) i: u32) -> @builtin(position) vec4f {
  var pos = array(vec2f(-1.0, -1.0), vec2f(3.0, -1.0), vec2f(-1.0, 3.0));
  return vec4f(pos[i], 0.0, 1.0);
}
@group(0) @binding(0) var t: texture_2d<f32>;
@group(0) @binding(1) var s: sampler;
@fragment fn fs(@builtin(position) p: vec4f) -> @location(0) vec4f {
  let dims = vec2f(textureDimensions(t));
  return textureSample(t, s, p.xy / dims);
}`

// Pre-pase: textura EXTERNA del vídeo (importExternalTexture, la vía del
// compositor — la única que puede leer frames decodificados por hardware/VAAPI)
// → textura rgba8 normal que las pipelines de Anime4K sí aceptan.
const INGEST_WGSL = /* wgsl */ `
@vertex fn vs(@builtin(vertex_index) i: u32) -> @builtin(position) vec4f {
  var pos = array(vec2f(-1.0, -1.0), vec2f(3.0, -1.0), vec2f(-1.0, 3.0));
  return vec4f(pos[i], 0.0, 1.0);
}
@group(0) @binding(0) var t: texture_external;
@group(0) @binding(1) var s: sampler;
@group(0) @binding(2) var<uniform> dims: vec2f;
@fragment fn fs(@builtin(position) p: vec4f) -> @location(0) vec4f {
  return textureSampleBaseClampToEdge(t, s, p.xy / dims);
}`

export class Anime4KRenderer {
  constructor() {
    this.device = null
    this.running = false
    this._vfcHandle = 0
  }

  static supported() { return !!navigator.gpu }

  /** Arranca (o re-arma) el pipeline para video→canvas con el tier dado.
   * targetW/H: resolución REAL de salida (rect visible × devicePixelRatio) —
   * computar a 2× nativo fijo (4K) desperdicia ~4× de GPU cuando el canvas se
   * muestra más pequeño; en la iGPU eso es la diferencia entre fluido y tirones. */
  async start(video, canvas, modeId, targetW = 0, targetH = 0) {
    this.stop()
    if (!navigator.gpu || !video.videoWidth) return false
    const Preset = (await presets())[modeId]
    if (!Preset) return false

    if (!this.device) {
      // Linux híbrido: low-power = la iGPU que decodifica el vídeo (VAAPI). La
      // dedicada computa más rápido pero SIEMPRE produce negro: los frames no
      // cruzan de GPU por ninguna vía. En Windows el decode va por D3D11 en la
      // GPU principal → high-performance (la dedicada, p.ej. 5070 junto a la
      // iGPU del Ryzen). Override manual: 'anime-a4k-gpu'.
      const pref = localStorage.getItem('anime-a4k-gpu')
        || (navigator.platform.includes('Linux') ? 'low-power' : 'high-performance')
      const adapter = await navigator.gpu.requestAdapter({ powerPreference: pref })
        || await navigator.gpu.requestAdapter()
      if (!adapter) return false
      this.device = await adapter.requestDevice()
      this.device.lost.then((info) => {
        console.warn('[a4k] device WebGPU perdido:', info?.message)
        this.device = null
        this.stop()
        this.onFatal?.(info)   // canvas fuera, vídeo visible
      })
    }
    const device = this.device
    const vw = video.videoWidth, vh = video.videoHeight
    // tope: 2× nativo (más no aporta); suelo: nativo (menos sería downscale)
    const tw = Math.round(Math.min(Math.max(targetW || vw * 2, vw), vw * 2))
    const th = Math.round(Math.min(Math.max(targetH || vh * 2, vh), vh * 2))
    this.frames = 0                              // contador para diagnóstico

    canvas.width = tw
    canvas.height = th
    const ctx = canvas.getContext('webgpu')
    const format = navigator.gpu.getPreferredCanvasFormat()
    ctx.configure({ device, format, alphaMode: 'opaque' })

    const inputTexture = device.createTexture({
      size: [vw, vh],
      format: 'rgba8unorm',
      usage: GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_DST
           | GPUTextureUsage.RENDER_ATTACHMENT,
    })
    const pipeline = new Preset({
      device, inputTexture,
      nativeDimensions: { width: vw, height: vh },
      targetDimensions: { width: tw, height: th },
    })

    const blitModule = device.createShaderModule({ code: BLIT_WGSL })
    const blit = device.createRenderPipeline({
      layout: 'auto',
      vertex: { module: blitModule, entryPoint: 'vs' },
      fragment: { module: blitModule, entryPoint: 'fs', targets: [{ format }] },
    })
    const sampler = device.createSampler({ magFilter: 'linear', minFilter: 'linear' })
    const bindGroup = device.createBindGroup({
      layout: blit.getBindGroupLayout(0),
      entries: [
        { binding: 0, resource: pipeline.getOutputTexture().createView() },
        { binding: 1, resource: sampler },
      ],
    })

    this.running = true
    let errCount = 0
    // Ingesta por VideoFrame en el MISMO device que decodifica (Intel):
    // única combinación que renderiza contenido real en este híbrido.
    // Matriz probada (2026-07-02): NVIDIA = negro siempre (cross-GPU);
    // canvas2D/getImageData = negro (frames VAAPI ilegibles);
    // importExternalTexture = negro en NVIDIA y cuelga el renderer en Intel.
    const frame = () => {
      if (!this.running) return
      try {
        const vf = new VideoFrame(video)
        try {
          device.queue.copyExternalImageToTexture(
            { source: vf }, { texture: inputTexture }, [vw, vh])
        } finally {
          vf.close()
        }
        const encoder = device.createCommandEncoder()
        pipeline.pass(encoder)
        const rp = encoder.beginRenderPass({
          colorAttachments: [{
            view: ctx.getCurrentTexture().createView(),
            loadOp: 'clear', storeOp: 'store', clearValue: { r: 0, g: 0, b: 0, a: 1 },
          }],
        })
        rp.setPipeline(blit)
        rp.setBindGroup(0, bindGroup)
        rp.draw(3)
        rp.end()
        device.queue.submit([encoder.finish()])
        this.frames++
        errCount = 0
      } catch (e) {
        // frames sueltos pueden fallar en seeks; errores SOSTENIDOS = pipeline
        // rota → apagar el shader y avisar (nunca dejar el canvas en negro)
        if (++errCount === 1) console.warn('[a4k] error de frame:', e)
        if (errCount > 30) {
          console.error('[a4k] errores sostenidos — apagando shader:', e)
          this.stop()
          this.onFatal?.(e)
          return
        }
      }
      this._vfcHandle = video.requestVideoFrameCallback(frame)
    }
    this._video = video
    this._vfcHandle = video.requestVideoFrameCallback(frame)
    return true
  }

  stop() {
    this.running = false
    if (this._video && this._vfcHandle) {
      try { this._video.cancelVideoFrameCallback(this._vfcHandle) } catch (_) {}
    }
    this._vfcHandle = 0
  }

  destroy() {
    this.stop()
    try { this.device?.destroy() } catch (_) {}
    this.device = null
  }
}
